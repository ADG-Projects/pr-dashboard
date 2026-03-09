"""Background sync service that fetches GitHub data and upserts into the database."""

import asyncio
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.engine import async_session_factory
from src.models.tables import CheckRun, PullRequest, Review, Space, TrackedRepo, User
from src.services.crypto import decrypt_token
from src.services.events import broadcast_event
from src.services.github_client import GitHubClient, parse_gh_datetime
from src.services.stack_detector import detect_stacks


class SyncService:
    """Periodically syncs GitHub PR data into the local database."""

    def __init__(self, interval_seconds: int = 180) -> None:
        self.interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the background sync loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"Sync service started (interval={self.interval}s)")

    async def stop(self) -> None:
        """Stop the background sync loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Sync service stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.sync_all()
            except Exception:
                logger.exception("Sync cycle failed")
            await asyncio.sleep(self.interval)

    async def sync_all(self) -> None:
        """Run one full sync cycle across all tracked repos, grouped by space."""
        async with async_session_factory() as session:
            spaces = (
                (
                    await session.execute(
                        select(Space)
                        .options(selectinload(Space.github_account))
                        .where(Space.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )

        for space in spaces:
            account = space.github_account
            if not account or not account.encrypted_token:
                logger.warning(f"Space '{space.name}' has no linked account/token, skipping")
                continue
            token = decrypt_token(account.encrypted_token)
            gh = GitHubClient(token=token, base_url=account.base_url)
            try:
                async with async_session_factory() as session:
                    repos = (
                        (
                            await session.execute(
                                select(TrackedRepo).where(
                                    TrackedRepo.is_active.is_(True),
                                    TrackedRepo.space_id == space.id,
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )

                for repo in repos:
                    try:
                        await self.sync_repo(repo.id, repo.owner, repo.name, gh)
                    except Exception:
                        logger.exception(f"Failed to sync {repo.full_name}")
            finally:
                await gh.close()

        # Also sync repos without a space (legacy, using fallback token)
        async with async_session_factory() as session:
            orphan_repos = (
                (
                    await session.execute(
                        select(TrackedRepo).where(
                            TrackedRepo.is_active.is_(True),
                            TrackedRepo.space_id.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

        if orphan_repos:
            from src.config.settings import settings

            if settings.github_token:
                gh = GitHubClient(token=settings.github_token)
                try:
                    for repo in orphan_repos:
                        try:
                            await self.sync_repo(repo.id, repo.owner, repo.name, gh)
                        except Exception:
                            logger.exception(f"Failed to sync {repo.full_name}")
                finally:
                    await gh.close()

    async def sync_repo(
        self,
        repo_id: int,
        owner: str,
        name: str,
        github: GitHubClient | None = None,
    ) -> None:
        """Sync all open PRs for a single repo."""
        logger.info(f"Syncing {owner}/{name}...")
        now = datetime.now(UTC)

        # Create a default client if none provided (for backward compat)
        close_after = False
        if github is None:
            from src.config.settings import settings

            github = GitHubClient(token=settings.github_token)
            close_after = True

        try:
            gh_pulls = await github.list_open_pulls(owner, name)
            logger.info(f"  Found {len(gh_pulls)} open PRs")

            fetched_pr_numbers = {gh_pr["number"] for gh_pr in gh_pulls}

            async with async_session_factory() as session:
                for gh_pr in gh_pulls:
                    pr = await self._upsert_pr(session, repo_id, gh_pr)

                    # Fetch detail, workflow runs, and reviews in parallel
                    detail_result, runs_result, reviews_result = await asyncio.gather(
                        github.get_pull(owner, name, gh_pr["number"]),
                        github.get_workflow_runs(owner, name, gh_pr["head"]["sha"]),
                        github.get_reviews(owner, name, gh_pr["number"]),
                        return_exceptions=True,
                    )

                    if isinstance(detail_result, Exception):
                        logger.warning(
                            f"  Could not fetch detail for PR #{gh_pr['number']}: {detail_result}"
                        )
                    else:
                        pr.additions = detail_result.get("additions", 0)
                        pr.deletions = detail_result.get("deletions", 0)
                        pr.changed_files = detail_result.get("changed_files", 0)
                        pr.mergeable_state = detail_result.get("mergeable_state")

                    if isinstance(runs_result, Exception):
                        logger.warning(
                            f"  Could not fetch workflow runs for PR #{gh_pr['number']}: "
                            f"{runs_result}"
                        )
                    else:
                        checks = [
                            {
                                "name": r["name"],
                                "status": r["status"],
                                "conclusion": r.get("conclusion"),
                                "details_url": r.get("html_url"),
                            }
                            for r in runs_result
                        ]
                        await self._upsert_check_runs(session, pr.id, checks)

                    if isinstance(reviews_result, Exception):
                        logger.warning(
                            f"  Could not fetch reviews for PR #{gh_pr['number']}: {reviews_result}"
                        )
                    else:
                        await self._upsert_reviews(session, pr.id, reviews_result)

                # Detect stale PRs: open in DB but not returned by GitHub
                db_open_prs = (
                    (
                        await session.execute(
                            select(PullRequest).where(
                                PullRequest.repo_id == repo_id,
                                PullRequest.state == "open",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                stale_prs = [pr for pr in db_open_prs if pr.number not in fetched_pr_numbers]

                if stale_prs:
                    logger.info(f"  Updating {len(stale_prs)} stale PR(s) for {owner}/{name}")
                    sem = asyncio.Semaphore(5)

                    async def fetch_stale(pr_number: int) -> dict | None:
                        async with sem:
                            try:
                                return await github.get_pull(owner, name, pr_number)
                            except Exception as exc:
                                logger.warning(f"  Could not fetch stale PR #{pr_number}: {exc}")
                                return None

                    stale_details = await asyncio.gather(
                        *(fetch_stale(pr.number) for pr in stale_prs)
                    )

                    for pr, detail in zip(stale_prs, stale_details, strict=True):
                        if detail is None:
                            continue
                        pr.state = detail["state"]
                        pr.merged_at = parse_gh_datetime(detail.get("merged_at"))
                        pr.updated_at = parse_gh_datetime(detail.get("updated_at")) or datetime.now(
                            UTC
                        )
                        pr.last_synced_at = datetime.now(UTC)

                repo = await session.get(TrackedRepo, repo_id)
                if repo:
                    repo.last_synced_at = now

                await session.commit()

            async with async_session_factory() as session:
                stacks = await detect_stacks(session, repo_id)
                await session.commit()
                if stacks:
                    logger.info(f"  Detected {len(stacks)} stack(s) for {owner}/{name}")

            await broadcast_event(
                "sync_complete",
                {"repo_id": repo_id, "owner": owner, "name": name},
            )
            logger.info(f"  Sync complete for {owner}/{name}")
        finally:
            if close_after:
                await github.close()

    async def _upsert_pr(self, session: AsyncSession, repo_id: int, gh_pr: dict) -> PullRequest:
        """Insert or update a pull request from GitHub data."""
        result = await session.execute(
            select(PullRequest).where(
                PullRequest.repo_id == repo_id,
                PullRequest.number == gh_pr["number"],
            )
        )
        pr = result.scalar_one_or_none()

        now = datetime.now(UTC)
        new_reviewers = [
            {
                "login": r["login"],
                "avatar_url": r.get("avatar_url"),
                "github_id": r["id"],
            }
            for r in (gh_pr.get("requested_reviewers") or [])
        ]

        # Auto-discover reviewer users
        await self._ensure_reviewer_users(session, gh_pr.get("requested_reviewers") or [])

        if pr is None:
            pr = PullRequest(
                repo_id=repo_id,
                number=gh_pr["number"],
                title=gh_pr["title"],
                state=gh_pr["state"],
                draft=gh_pr.get("draft", False),
                head_ref=gh_pr["head"]["ref"],
                base_ref=gh_pr["base"]["ref"],
                author=gh_pr["user"]["login"],
                additions=0,
                deletions=0,
                changed_files=0,
                head_sha=gh_pr["head"]["sha"],
                html_url=gh_pr["html_url"],
                created_at=parse_gh_datetime(gh_pr["created_at"]) or now,
                updated_at=parse_gh_datetime(gh_pr["updated_at"]) or now,
                merged_at=parse_gh_datetime(gh_pr.get("merged_at")),
                last_synced_at=now,
                github_requested_reviewers=new_reviewers,
            )
            session.add(pr)
            await session.flush()
        else:
            pr.title = gh_pr["title"]
            pr.state = gh_pr["state"]
            pr.draft = gh_pr.get("draft", False)
            pr.head_ref = gh_pr["head"]["ref"]
            pr.base_ref = gh_pr["base"]["ref"]
            pr.head_sha = gh_pr["head"]["sha"]
            pr.updated_at = parse_gh_datetime(gh_pr["updated_at"]) or now
            pr.merged_at = parse_gh_datetime(gh_pr.get("merged_at"))
            pr.last_synced_at = now
            pr.github_requested_reviewers = new_reviewers

        return pr

    async def _ensure_reviewer_users(self, session: AsyncSession, gh_reviewers: list[dict]) -> None:
        """Upsert User rows for requested reviewers so they appear in team dropdowns."""
        for reviewer in gh_reviewers:
            github_id = reviewer.get("id")
            if not github_id:
                continue
            result = await session.execute(select(User).where(User.github_id == github_id))
            user = result.scalar_one_or_none()
            if user is None:
                session.add(
                    User(
                        github_id=github_id,
                        login=reviewer["login"],
                        avatar_url=reviewer.get("avatar_url"),
                        name=reviewer.get("name"),
                        is_active=True,
                    )
                )
            else:
                user.login = reviewer["login"]
                if reviewer.get("avatar_url"):
                    user.avatar_url = reviewer["avatar_url"]

    async def _upsert_check_runs(
        self, session: AsyncSession, pr_id: int, checks: list[dict]
    ) -> None:
        """Replace check runs for a PR."""
        existing = (
            (await session.execute(select(CheckRun).where(CheckRun.pull_request_id == pr_id)))
            .scalars()
            .all()
        )
        for check in existing:
            await session.delete(check)

        now = datetime.now(UTC)
        for check in checks:
            session.add(
                CheckRun(
                    pull_request_id=pr_id,
                    name=check["name"],
                    status=check["status"],
                    conclusion=check.get("conclusion"),
                    details_url=check.get("details_url"),
                    last_synced_at=now,
                )
            )

    async def _upsert_reviews(self, session: AsyncSession, pr_id: int, reviews: list[dict]) -> None:
        """Replace reviews for a PR."""
        existing = (
            (await session.execute(select(Review).where(Review.pull_request_id == pr_id)))
            .scalars()
            .all()
        )
        for review in existing:
            await session.delete(review)

        for review in reviews:
            submitted = parse_gh_datetime(review.get("submitted_at"))
            if not submitted:
                continue
            session.add(
                Review(
                    pull_request_id=pr_id,
                    reviewer=review["user"]["login"],
                    state=review["state"],
                    commit_id=review.get("commit_id"),
                    submitted_at=submitted,
                )
            )
