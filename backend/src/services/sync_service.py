"""Background sync service that fetches GitHub data and upserts into the database."""

import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.engine import async_session_factory
from src.models.tables import (
    CheckRun,
    GitHubAccount,
    PullRequest,
    RepoTracker,
    Review,
    Space,
    TrackedRepo,
    User,
)
from src.services.crypto import decrypt_token
from src.services.events import broadcast_event
from src.services.github_client import GitHubAuthError, GitHubClient, parse_gh_datetime
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

    async def _resolve_clients_for_repo(
        self, session: AsyncSession, repo_id: int
    ) -> list[GitHubClient]:
        """Return all candidate GitHub clients for a repo (one per tracker with a valid token)."""
        trackers = (
            (
                await session.execute(
                    select(RepoTracker)
                    .options(selectinload(RepoTracker.space).selectinload(Space.github_account))
                    .where(RepoTracker.repo_id == repo_id)
                )
            )
            .scalars()
            .all()
        )

        clients: list[GitHubClient] = []
        for tracker in trackers:
            if tracker.space and tracker.space.is_active and tracker.space.github_account:
                account = tracker.space.github_account
                if account.encrypted_token and account.is_active:
                    token = decrypt_token(account.encrypted_token)
                    if token:
                        clients.append(GitHubClient(token=token, base_url=account.base_url))
        return clients

    async def sync_all(self) -> None:
        """Run one full sync cycle across all active tracked repos."""
        async with async_session_factory() as session:
            repos = (
                (await session.execute(select(TrackedRepo).where(TrackedRepo.is_active.is_(True))))
                .scalars()
                .all()
            )

        for repo in repos:
            clients: list[GitHubClient] = []
            try:
                async with async_session_factory() as session:
                    clients = await self._resolve_clients_for_repo(session, repo.id)

                if not clients:
                    logger.warning(f"No token available for {repo.full_name}, skipping")
                    continue

                # Try each client; fall back on auth errors
                synced = False
                for i, gh in enumerate(clients):
                    try:
                        await self.sync_repo(repo.id, repo.owner, repo.name, gh)
                        synced = True
                        break
                    except GitHubAuthError as exc:
                        remaining = len(clients) - i - 1
                        if remaining > 0:
                            logger.warning(
                                f"Token {i + 1}/{len(clients)} lacks access to "
                                f"{repo.full_name} ({exc.response.status_code}), "
                                f"trying next token"
                            )
                        else:
                            logger.warning(
                                f"All {len(clients)} token(s) failed for "
                                f"{repo.full_name} ({exc.response.status_code}), skipping"
                            )

                if not synced:
                    continue

            except Exception:
                logger.exception(f"Failed to sync {repo.full_name}")
            finally:
                for gh in clients:
                    await gh.close()

    async def sync_repo(
        self,
        repo_id: int,
        owner: str,
        name: str,
        github: GitHubClient,
    ) -> None:
        """Sync PRs for a single repo (open, stale, closed, and merged)."""
        logger.info(f"Syncing {owner}/{name}...")
        now = datetime.now(UTC)

        from src.config.settings import settings

        gh_pulls = await github.list_open_pulls(owner, name)
        logger.info(f"  Found {len(gh_pulls)} open PRs")

        # Fetch recently closed/merged PRs so they appear even after a DB wipe
        cutoff = now - timedelta(days=settings.merged_pr_lookback_days)
        closed_pulls = await github.list_recently_closed_pulls(owner, name, cutoff)
        logger.info(f"  Found {len(closed_pulls)} recently closed PRs")

        all_pulls = gh_pulls + closed_pulls
        fetched_pr_numbers = {gh_pr["number"] for gh_pr in all_pulls}

        async with async_session_factory() as session:
            for gh_pr in all_pulls:
                pr = await self._upsert_pr(session, repo_id, gh_pr, gh_client=github)

                # Fetch detail, workflow runs, reviews, and comments in parallel
                (
                    detail_result,
                    runs_result,
                    reviews_result,
                    issue_comments_result,
                    review_comments_result,
                ) = await asyncio.gather(
                    github.get_pull(owner, name, gh_pr["number"]),
                    github.get_workflow_runs(owner, name, gh_pr["head"]["sha"]),
                    github.get_reviews(owner, name, gh_pr["number"]),
                    github.get_issue_comments(owner, name, gh_pr["number"]),
                    github.get_review_comments(owner, name, gh_pr["number"]),
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
                        f"  Could not fetch workflow runs for PR #{gh_pr['number']}: {runs_result}"
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
                    await self._upsert_reviews(session, pr.id, reviews_result, gh_client=github)

                # Extract unique commenters (excluding PR author)
                commenter_logins: set[str] = set()
                pr_author = gh_pr["user"]["login"]
                for comments_result in (issue_comments_result, review_comments_result):
                    if isinstance(comments_result, Exception):
                        logger.warning(
                            f"  Could not fetch comments for PR #{gh_pr['number']}: "
                            f"{comments_result}"
                        )
                        continue
                    for comment in comments_result:
                        login = comment.get("user", {}).get("login")
                        if login and login != pr_author:
                            commenter_logins.add(login)
                pr.commenters = sorted(commenter_logins)

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

                stale_details = await asyncio.gather(*(fetch_stale(pr.number) for pr in stale_prs))

                for pr, detail in zip(stale_prs, stale_details, strict=True):
                    if detail is None:
                        continue
                    pr.state = detail["state"]
                    pr.merged_at = parse_gh_datetime(detail.get("merged_at"))
                    pr.updated_at = parse_gh_datetime(detail.get("updated_at")) or datetime.now(UTC)
                    pr.last_synced_at = datetime.now(UTC)

            repo = await session.get(TrackedRepo, repo_id)
            if repo:
                repo.last_synced_at = now

            await session.commit()

            # Clean up repo if all trackers were removed while sync was running
            await self._delete_if_orphaned(repo_id, f"{owner}/{name}")

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

    async def _delete_if_orphaned(self, repo_id: int, repo_name: str) -> None:
        """Delete a repo if all its trackers were removed during sync."""
        async with async_session_factory() as session:
            remaining = (
                await session.execute(
                    select(func.count(RepoTracker.id)).where(RepoTracker.repo_id == repo_id)
                )
            ).scalar_one()
            if remaining == 0:
                from sqlalchemy import delete

                await session.execute(delete(TrackedRepo).where(TrackedRepo.id == repo_id))
                await session.commit()
                logger.info(f"  Deleted orphaned repo {repo_name} after sync")

    async def _upsert_pr(
        self,
        session: AsyncSession,
        repo_id: int,
        gh_pr: dict,
        gh_client: GitHubClient | None = None,
    ) -> PullRequest:
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

        # Ensure PR author User exists with name
        author_user = gh_pr.get("user", {})
        if author_user.get("id"):
            await self._find_or_create_user(
                session,
                author_user["id"],
                author_user["login"],
                author_user.get("avatar_url"),
                author_user.get("name"),
                gh_client=gh_client,
            )

        # Auto-discover reviewer users
        await self._ensure_reviewer_users(
            session, gh_pr.get("requested_reviewers") or [], gh_client=gh_client
        )

        # Resolve assignee from GitHub
        assignee_id = await self._resolve_assignee(session, gh_pr, gh_client=gh_client)

        # Derive manual_priority from GitHub labels
        label_names = {lbl["name"] for lbl in (gh_pr.get("labels") or [])}
        if "priority:high" in label_names:
            manual_priority = "high"
        elif "priority:low" in label_names:
            manual_priority = "low"
        else:
            manual_priority = None

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
                assignee_id=assignee_id,
                manual_priority=manual_priority,
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
            pr.assignee_id = assignee_id
            pr.manual_priority = manual_priority

        return pr

    async def _find_or_create_user(
        self,
        session: AsyncSession,
        github_id: int,
        login: str,
        avatar_url: str | None = None,
        name: str | None = None,
        gh_client: GitHubClient | None = None,
    ) -> User:
        """Find a User by github_id, checking linked GitHubAccounts first.

        If the github_id belongs to a GitHubAccount linked to an existing User
        (e.g. a second account added via OAuth), return that User instead of
        creating a duplicate.

        When name is missing and gh_client is provided, fetches the user's
        full name from the GitHub API.
        """
        # Check if this github_id is already linked as a GitHubAccount
        acct_result = await session.execute(
            select(GitHubAccount).where(GitHubAccount.github_id == github_id).limit(1)
        )
        acct = acct_result.scalar_one_or_none()
        if acct:
            user = await session.get(User, acct.user_id)
            if user:
                if not user.name and gh_client:
                    name = await self._fetch_user_name(gh_client, login)
                    if name:
                        user.name = name
                return user

        # Fall back to direct User.github_id lookup
        result = await session.execute(select(User).where(User.github_id == github_id))
        user = result.scalar_one_or_none()
        if user is None:
            if not name and gh_client:
                name = await self._fetch_user_name(gh_client, login)
            user = User(
                github_id=github_id,
                login=login,
                avatar_url=avatar_url,
                name=name,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            user.login = login
            if avatar_url:
                user.avatar_url = avatar_url
            if not user.name and gh_client:
                name = await self._fetch_user_name(gh_client, login)
                if name:
                    user.name = name
        return user

    async def _fetch_user_name(self, gh_client: GitHubClient, login: str) -> str | None:
        """Fetch a user's full name from the GitHub API, returning None on failure."""
        try:
            profile = await gh_client.get_user(login)
            return profile.get("name")
        except Exception:
            logger.debug(f"Could not fetch profile for {login}")
            return None

    async def _resolve_assignee(
        self, session: AsyncSession, gh_pr: dict, gh_client: GitHubClient | None = None
    ) -> int | None:
        """Resolve GitHub assignee to a local User id."""
        assignees = gh_pr.get("assignees") or []
        if not assignees:
            single = gh_pr.get("assignee")
            if single:
                assignees = [single]
        if not assignees:
            return None
        gh_assignee = assignees[0]
        github_id = gh_assignee.get("id")
        if not github_id:
            return None
        user = await self._find_or_create_user(
            session,
            github_id,
            gh_assignee["login"],
            gh_assignee.get("avatar_url"),
            gh_assignee.get("name"),
            gh_client=gh_client,
        )
        return user.id

    async def _ensure_reviewer_users(
        self, session: AsyncSession, gh_reviewers: list[dict], gh_client: GitHubClient | None = None
    ) -> None:
        """Upsert User rows for requested reviewers so they appear in team dropdowns."""
        for reviewer in gh_reviewers:
            github_id = reviewer.get("id")
            if not github_id:
                continue
            await self._find_or_create_user(
                session,
                github_id,
                reviewer["login"],
                reviewer.get("avatar_url"),
                reviewer.get("name"),
                gh_client=gh_client,
            )

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

    async def _upsert_reviews(
        self,
        session: AsyncSession,
        pr_id: int,
        reviews: list[dict],
        gh_client: GitHubClient | None = None,
    ) -> None:
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
            # Ensure reviewer User exists with name
            reviewer_user = review.get("user", {})
            if reviewer_user.get("id"):
                await self._find_or_create_user(
                    session,
                    reviewer_user["id"],
                    reviewer_user["login"],
                    reviewer_user.get("avatar_url"),
                    reviewer_user.get("name"),
                    gh_client=gh_client,
                )
            session.add(
                Review(
                    pull_request_id=pr_id,
                    reviewer=review["user"]["login"],
                    state=review["state"],
                    commit_id=review.get("commit_id"),
                    submitted_at=submitted,
                )
            )
