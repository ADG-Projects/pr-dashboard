"""Background sync service that fetches GitHub data and upserts into the database."""

import asyncio
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.engine import async_session_factory
from src.models.tables import CheckRun, PullRequest, Review, TrackedRepo
from src.services.events import broadcast_event
from src.services.github_client import GitHubClient, parse_gh_datetime
from src.services.stack_detector import detect_stacks


class SyncService:
    """Periodically syncs GitHub PR data into the local database."""

    def __init__(self, interval_seconds: int = 180) -> None:
        self.interval = interval_seconds
        self.github = GitHubClient()
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
        await self.github.close()
        logger.info("Sync service stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.sync_all()
            except Exception:
                logger.exception("Sync cycle failed")
            await asyncio.sleep(self.interval)

    async def sync_all(self) -> None:
        """Run one full sync cycle across all tracked repos."""
        async with async_session_factory() as session:
            repos = (
                (await session.execute(select(TrackedRepo).where(TrackedRepo.is_active.is_(True))))
                .scalars()
                .all()
            )

        for repo in repos:
            try:
                await self.sync_repo(repo.id, repo.owner, repo.name)
            except Exception:
                logger.exception(f"Failed to sync {repo.full_name}")

    async def sync_repo(self, repo_id: int, owner: str, name: str) -> None:
        """Sync all open PRs for a single repo."""
        logger.info(f"Syncing {owner}/{name}...")
        now = datetime.now(UTC)

        gh_pulls = await self.github.list_open_pulls(owner, name)
        logger.info(f"  Found {len(gh_pulls)} open PRs")

        async with async_session_factory() as session:
            for gh_pr in gh_pulls:
                pr = await self._upsert_pr(session, repo_id, gh_pr)

                # Fetch detailed data for each PR
                try:
                    detail = await self.github.get_pull(owner, name, gh_pr["number"])
                    pr.additions = detail.get("additions", 0)
                    pr.deletions = detail.get("deletions", 0)
                    pr.changed_files = detail.get("changed_files", 0)
                    pr.mergeable_state = detail.get("mergeable_state")
                except Exception as exc:
                    logger.warning(f"  Could not fetch detail for PR #{gh_pr['number']}: {exc}")

                # Sync workflow runs (via Actions API)
                try:
                    runs = await self.github.get_workflow_runs(owner, name, gh_pr["head"]["sha"])
                    checks = [
                        {
                            "name": r["name"],
                            "status": r["status"],
                            "conclusion": r.get("conclusion"),
                            "details_url": r.get("html_url"),
                        }
                        for r in runs
                    ]
                    await self._upsert_check_runs(session, pr.id, checks)
                except Exception as exc:
                    logger.warning(
                        f"  Could not fetch workflow runs for PR #{gh_pr['number']}: {exc}"
                    )

                # Sync reviews
                try:
                    reviews = await self.github.get_reviews(owner, name, gh_pr["number"])
                    await self._upsert_reviews(session, pr.id, reviews)
                except Exception as exc:
                    logger.warning(f"  Could not fetch reviews for PR #{gh_pr['number']}: {exc}")

            # Update repo last_synced_at
            repo = await session.get(TrackedRepo, repo_id)
            if repo:
                repo.last_synced_at = now

            await session.commit()

        # Run stack detection in a separate session
        async with async_session_factory() as session:
            stacks = await detect_stacks(session, repo_id)
            await session.commit()
            if stacks:
                logger.info(f"  Detected {len(stacks)} stack(s) for {owner}/{name}")

        await broadcast_event("sync_complete", {"repo_id": repo_id, "owner": owner, "name": name})
        logger.info(f"  Sync complete for {owner}/{name}")

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

        return pr

    async def _upsert_check_runs(
        self, session: AsyncSession, pr_id: int, checks: list[dict]
    ) -> None:
        """Replace check runs for a PR (simpler than individual upsert)."""
        # Delete existing checks for this PR
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
