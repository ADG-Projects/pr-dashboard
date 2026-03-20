"""Background sync service that fetches GitHub data and upserts into the database."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
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
from src.services.github_client import (
    AuthErrorType,
    GitHubAuthError,
    GitHubClient,
    parse_gh_datetime,
)
from src.services.stack_detector import detect_stacks

ALLOWED_LABELS: dict[str, dict[str, str]] = {
    "bug": {"color": "d73a4a", "description": "Something isn't working"},
    "enhancement": {"color": "0075ca", "description": "New feature or request"},
    "documentation": {"color": "0e8a16", "description": "Documentation changes"},
    "refactor": {"color": "7057ff", "description": "Code restructuring"},
    "testing": {"color": "fbca04", "description": "Test-related changes"},
}


class SyncService:
    """Periodically syncs GitHub PR data into the local database."""

    def __init__(self, interval_seconds: int = 180) -> None:
        self.interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # ETag cache for list endpoints, keyed by "{owner}/{name}/{endpoint}"
        self._etag_cache: dict[str, str] = {}
        # Cache of user logins we already attempted to fetch a name for
        self._user_name_fetch_attempted: set[str] = set()

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
    ) -> list[tuple[GitHubClient, int]]:
        """Return candidate GitHub clients for a repo as (client, account_id) tuples."""
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

        clients: list[tuple[GitHubClient, int]] = []
        for tracker in trackers:
            if tracker.space and tracker.space.is_active and tracker.space.github_account:
                account = tracker.space.github_account
                if account.encrypted_token and account.is_active:
                    token = decrypt_token(account.encrypted_token)
                    if token:
                        clients.append(
                            (GitHubClient(token=token, base_url=account.base_url), account.id)
                        )
                    else:
                        # Decrypt failed (likely SECRET_KEY rotation) - only broadcast on transition
                        if account.token_status != "decrypt_failed":
                            account.token_status = "decrypt_failed"
                            account.token_error = (
                                "Token cannot be decrypted. "
                                "The server encryption key may have changed."
                            )
                            account.token_checked_at = datetime.now(UTC)
                            logger.warning(
                                f"Decrypt failed for account {account.login} (id={account.id})"
                            )
                            await broadcast_event(
                                "auth_issue",
                                {
                                    "account_id": account.id,
                                    "login": account.login,
                                    "token_status": "decrypt_failed",
                                },
                            )
        return clients

    async def migrate_webhook_events(self) -> None:
        """One-time migration: add pull_request_review_thread to existing webhooks."""
        target_event = "pull_request_review_thread"

        async with async_session_factory() as session:
            repos = (
                (
                    await session.execute(
                        select(TrackedRepo).where(
                            TrackedRepo.github_webhook_id.isnot(None),
                            TrackedRepo.is_active.is_(True),
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not repos:
                return

            logger.info(f"Checking {len(repos)} webhook(s) for event migration")

            for repo in repos:
                client_tuples = await self._resolve_clients_for_repo(session, repo.id)
                if not client_tuples:
                    continue
                gh, _account_id = client_tuples[0]
                try:
                    hooks = await gh.list_webhooks(repo.owner, repo.name)
                    for hook in hooks:
                        if hook["id"] == repo.github_webhook_id:
                            current_events = hook.get("events", [])
                            if target_event not in current_events:
                                new_events = current_events + [target_event]
                                await gh.update_webhook_events(
                                    repo.owner, repo.name, hook["id"], new_events
                                )
                                logger.info(
                                    f"Added {target_event} to webhook {hook['id']} "
                                    f"for {repo.full_name}"
                                )
                            break
                except Exception as exc:
                    logger.warning(f"Could not migrate webhook for {repo.full_name}: {exc}")
                finally:
                    await gh.close()

    async def sync_all(self) -> None:
        """Run one full sync cycle across all active tracked repos."""
        from src.config.settings import settings

        # Budget check: skip cycle if rate limit is too low
        budget_ok = await self._check_rate_limit_budget(settings.rate_limit_min_remaining)
        if not budget_ok:
            return

        async with async_session_factory() as session:
            repos = (
                (await session.execute(select(TrackedRepo).where(TrackedRepo.is_active.is_(True))))
                .scalars()
                .all()
            )

        for repo in repos:
            client_tuples: list[tuple[GitHubClient, int]] = []
            try:
                async with async_session_factory() as session:
                    client_tuples = await self._resolve_clients_for_repo(session, repo.id)

                if not client_tuples:
                    logger.warning(f"No token available for {repo.full_name}, skipping")
                    await self._record_repo_error(
                        repo.id,
                        "No valid token available for this repository",
                        account_id=None,
                        error_type=AuthErrorType.decrypt_failed,
                    )
                    continue

                # Try each client; fall back on auth errors
                synced = False
                synced_account_id: int | None = None
                last_auth_error: GitHubAuthError | None = None
                last_error_account_id: int | None = None
                for i, (gh, account_id) in enumerate(client_tuples):
                    try:
                        if repo.github_webhook_id is not None:
                            await self.sync_repo_lightweight(repo.id, repo.owner, repo.name, gh)
                        else:
                            await self.sync_repo(repo.id, repo.owner, repo.name, gh)
                        synced = True
                        synced_account_id = account_id
                        break
                    except GitHubAuthError as exc:
                        last_auth_error = exc
                        last_error_account_id = account_id
                        remaining = len(client_tuples) - i - 1
                        if remaining > 0:
                            logger.warning(
                                f"Token {i + 1}/{len(client_tuples)} lacks access to "
                                f"{repo.full_name} ({exc.response.status_code}), "
                                f"trying next token"
                            )
                        else:
                            logger.warning(
                                f"All {len(client_tuples)} token(s) failed for "
                                f"{repo.full_name} ({exc.response.status_code}), skipping"
                            )
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 404:
                            logger.warning(
                                f"Repo {repo.full_name} returned 404, marking as not accessible"
                            )
                            await self._record_repo_error(
                                repo.id,
                                "Repository not found or access removed",
                                account_id=None,
                                error_type=AuthErrorType.repo_not_accessible,
                            )
                            break
                        raise

                if synced:
                    await self._record_repo_success(repo.id, account_id=synced_account_id)
                elif last_auth_error is not None:
                    await self._record_repo_error(
                        repo.id,
                        str(last_auth_error),
                        account_id=last_error_account_id,
                        error_type=last_auth_error.error_type,
                    )

            except Exception:
                logger.exception(f"Failed to sync {repo.full_name}")
            finally:
                for gh, _account_id in client_tuples:
                    await gh.close()

    async def _check_rate_limit_budget(self, min_remaining: int) -> bool:
        """Check GitHub rate limit before syncing. Returns False if budget is too low.

        Uses the first available client to call /rate_limit (this endpoint is free).
        """
        async with async_session_factory() as session:
            repos = (
                (await session.execute(select(TrackedRepo).where(TrackedRepo.is_active.is_(True))))
                .scalars()
                .all()
            )
            for repo in repos:
                client_tuples = await self._resolve_clients_for_repo(session, repo.id)
                if client_tuples:
                    gh, _account_id = client_tuples[0]
                    try:
                        data = await gh.get_rate_limit()
                        core = data.get("resources", {}).get("core", {})
                        remaining = core.get("remaining", 9999)
                        limit = core.get("limit", 5000)
                        logger.info(f"Rate limit: {remaining}/{limit} remaining")
                        if remaining < min_remaining:
                            logger.warning(
                                f"Rate limit too low ({remaining} < {min_remaining}), "
                                f"skipping sync cycle"
                            )
                            return False
                        return True
                    except Exception:
                        logger.debug("Could not check rate limit, proceeding with sync")
                        return True
                    finally:
                        for c, _ in client_tuples:
                            await c.close()
        return True

    # Map AuthErrorType to the token_status string stored on GitHubAccount
    _ERROR_TYPE_TO_STATUS: dict[AuthErrorType, str] = {
        AuthErrorType.token_expired: "expired",
        AuthErrorType.token_revoked: "revoked",
        AuthErrorType.insufficient_scope: "insufficient_scope",
        AuthErrorType.sso_required: "sso_required",
        AuthErrorType.decrypt_failed: "decrypt_failed",
        AuthErrorType.repo_not_accessible: "repo_not_accessible",
    }

    # Account-level errors (should update GitHubAccount.token_status)
    _ACCOUNT_LEVEL_ERRORS = {
        AuthErrorType.token_expired,
        AuthErrorType.token_revoked,
        AuthErrorType.decrypt_failed,
    }

    async def _record_repo_error(
        self,
        repo_id: int,
        error_message: str,
        *,
        account_id: int | None,
        error_type: AuthErrorType,
    ) -> None:
        """Record an auth/sync error on TrackedRepo and optionally GitHubAccount."""
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            repo = await session.get(TrackedRepo, repo_id)
            if repo:
                repo.last_sync_error = error_message[:500]
                repo.last_sync_error_at = now

            # Update account status for account-level errors
            should_broadcast = False
            broadcast_login = ""
            broadcast_status = ""
            if account_id is not None and error_type in self._ACCOUNT_LEVEL_ERRORS:
                account = await session.get(GitHubAccount, account_id)
                if account:
                    was_ok = account.token_status == "ok"
                    account.token_status = self._ERROR_TYPE_TO_STATUS.get(
                        error_type, "insufficient_scope"
                    )
                    account.token_error = error_message[:500]
                    account.token_checked_at = now
                    if was_ok:
                        should_broadcast = True
                        broadcast_login = account.login
                        broadcast_status = account.token_status
                else:
                    logger.warning(
                        f"Cannot update account status: GitHubAccount {account_id} not found"
                    )
            elif not repo:
                logger.warning(f"Cannot record sync error: TrackedRepo {repo_id} not found")
            await session.commit()

            if should_broadcast:
                await broadcast_event(
                    "auth_issue",
                    {
                        "account_id": account_id,
                        "login": broadcast_login,
                        "token_status": broadcast_status,
                    },
                )

    async def _record_repo_success(self, repo_id: int, *, account_id: int | None = None) -> None:
        """Clear sync errors on a repo and account after successful sync."""
        now = datetime.now(UTC)
        async with async_session_factory() as session:
            repo = await session.get(TrackedRepo, repo_id)
            if repo:
                had_error = repo.last_sync_error is not None
                repo.last_sync_error = None
                repo.last_sync_error_at = None
                repo.last_successful_sync_at = now
                if had_error:
                    logger.info(f"Cleared sync error for {repo.full_name}")

            # Reset account status if it was in error
            if account_id is not None:
                account = await session.get(GitHubAccount, account_id)
                if account and account.token_status != "ok":
                    old_status = account.token_status
                    account.token_status = "ok"
                    account.token_error = None
                    account.token_checked_at = now
                    logger.info(f"Account {account.login} recovered from {old_status}")
                    await session.commit()
                    await broadcast_event(
                        "auth_resolved",
                        {"account_id": account_id, "login": account.login},
                    )
                    return

            await session.commit()

    async def sync_repo(
        self,
        repo_id: int,
        owner: str,
        name: str,
        github: GitHubClient,
    ) -> None:
        """Sync PRs for a single repo (open, stale, closed, and merged)."""
        logger.info(f"Syncing {owner}/{name}...")
        github.reset_rate_limited()
        now = datetime.now(UTC)

        from src.config.settings import settings

        # Use ETag caching: 304 Not Modified responses don't count against rate limit
        open_etag_key = f"{owner}/{name}/pulls/open"
        closed_etag_key = f"{owner}/{name}/pulls/closed"

        gh_pulls, new_open_etag, open_modified = await github.list_open_pulls_with_etag(
            owner, name, etag=self._etag_cache.get(open_etag_key)
        )
        if new_open_etag:
            self._etag_cache[open_etag_key] = new_open_etag

        cutoff = now - timedelta(days=settings.merged_pr_lookback_days)
        (
            closed_pulls,
            new_closed_etag,
            closed_modified,
        ) = await github.list_recently_closed_pulls_with_etag(
            owner, name, cutoff, etag=self._etag_cache.get(closed_etag_key)
        )
        if new_closed_etag:
            self._etag_cache[closed_etag_key] = new_closed_etag

        # If both lists returned 304, nothing changed at all - skip detail fetches
        if not open_modified and not closed_modified:
            logger.info(f"  No changes detected (ETag 304) for {owner}/{name}, skipping details")
            async with async_session_factory() as session:
                repo_obj = await session.get(TrackedRepo, repo_id)
                if repo_obj:
                    repo_obj.last_synced_at = now
                await session.commit()
            await broadcast_event(
                "sync_complete",
                {"repo_id": repo_id, "owner": owner, "name": name},
            )
            return

        logger.info(f"  Found {len(gh_pulls)} open PRs")
        logger.info(f"  Found {len(closed_pulls)} recently closed PRs")

        all_pulls = gh_pulls + closed_pulls
        fetched_pr_numbers = {gh_pr["number"] for gh_pr in all_pulls}

        async with async_session_factory() as session:
            # Load existing PRs to detect which ones actually changed
            existing_prs = (
                await session.execute(
                    select(
                        PullRequest.number,
                        PullRequest.updated_at,
                        PullRequest.head_sha,
                    ).where(PullRequest.repo_id == repo_id)
                )
            ).all()
            db_pr_state = {row.number: (row.updated_at, row.head_sha) for row in existing_prs}

            changed_open: list[dict] = []
            changed_closed: list[dict] = []
            unchanged_open: list[dict] = []
            unchanged_closed_count = 0
            for gh_pr in all_pulls:
                pr_num = gh_pr["number"]
                gh_updated = parse_gh_datetime(gh_pr.get("updated_at"))
                gh_head_sha = gh_pr["head"]["sha"]
                db_state = db_pr_state.get(pr_num)
                is_changed = (
                    db_state is None or db_state[0] != gh_updated or db_state[1] != gh_head_sha
                )
                is_open = gh_pr["state"] == "open"
                if is_changed and is_open:
                    changed_open.append(gh_pr)
                elif is_changed:
                    changed_closed.append(gh_pr)
                elif is_open:
                    unchanged_open.append(gh_pr)
                else:
                    unchanged_closed_count += 1

            logger.info(
                f"  {len(changed_open)} changed open, "
                f"{len(changed_closed)} changed closed, "
                f"{len(unchanged_open)} unchanged open, "
                f"{unchanged_closed_count} unchanged closed (skipped)"
            )

            # --- Full fetch for changed open PRs (5 endpoints) ---
            blocked_pr_objects: dict[int, PullRequest] = {}
            for gh_pr in changed_open:
                if github.rate_limited:
                    logger.warning(f"  Aborting sync for {owner}/{name}: rate limit exhausted")
                    break

                pr = await self._upsert_pr(session, repo_id, gh_pr, gh_client=github)

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
                    pr.commit_count = detail_result.get("commits", 0)
                    if pr.mergeable_state == "blocked":
                        blocked_pr_objects[gh_pr["number"]] = pr
                    else:
                        pr.unresolved_thread_count = None

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

                commenter_logins: set[str] = set()
                pr_author = gh_pr["user"]["login"]
                author_last_commented_at: datetime | None = None
                for comments_result in (issue_comments_result, review_comments_result):
                    if isinstance(comments_result, Exception):
                        logger.warning(
                            f"  Could not fetch comments for PR #{gh_pr['number']}: "
                            f"{comments_result}"
                        )
                        continue
                    for comment in comments_result:
                        login = comment.get("user", {}).get("login")
                        if login and login == pr_author:
                            ts = parse_gh_datetime(comment.get("created_at"))
                            if ts and (
                                author_last_commented_at is None or ts > author_last_commented_at
                            ):
                                author_last_commented_at = ts
                        elif login:
                            commenter_logins.add(login)
                pr.commenters = sorted(commenter_logins)
                pr.author_last_commented_at = author_last_commented_at

            # Batched GraphQL: fetch unresolved thread counts for all blocked PRs
            if blocked_pr_objects and not github.rate_limited:
                try:
                    thread_counts = await github.get_unresolved_thread_counts(
                        owner, name, list(blocked_pr_objects.keys())
                    )
                    for pr_num, count in thread_counts.items():
                        if pr_num in blocked_pr_objects:
                            blocked_pr_objects[pr_num].unresolved_thread_count = count
                except Exception as exc:
                    logger.warning(f"  Could not fetch thread counts for {owner}/{name}: {exc}")

            # --- Reduced fetch for changed closed/merged PRs ---
            # No CI fetch (irrelevant for closed). Skip get_pull if already
            # in DB (diff stats don't change after close).
            for gh_pr in changed_closed:
                if github.rate_limited:
                    logger.warning(f"  Aborting sync for {owner}/{name}: rate limit exhausted")
                    break

                already_in_db = gh_pr["number"] in db_pr_state
                pr = await self._upsert_pr(session, repo_id, gh_pr, gh_client=github)

                coros: list = []
                coro_names: list[str] = []
                if not already_in_db:
                    coros.append(github.get_pull(owner, name, gh_pr["number"]))
                    coro_names.append("detail")
                coros.append(github.get_reviews(owner, name, gh_pr["number"]))
                coro_names.append("reviews")
                coros.append(github.get_issue_comments(owner, name, gh_pr["number"]))
                coro_names.append("issue_comments")
                coros.append(github.get_review_comments(owner, name, gh_pr["number"]))
                coro_names.append("review_comments")

                results = await asyncio.gather(*coros, return_exceptions=True)
                result_map = dict(zip(coro_names, results, strict=True))

                if "detail" in result_map:
                    detail_result = result_map["detail"]
                    if isinstance(detail_result, Exception):
                        logger.warning(
                            f"  Could not fetch detail for PR #{gh_pr['number']}: {detail_result}"
                        )
                    else:
                        pr.additions = detail_result.get("additions", 0)
                        pr.deletions = detail_result.get("deletions", 0)
                        pr.changed_files = detail_result.get("changed_files", 0)
                        pr.mergeable_state = detail_result.get("mergeable_state")
                        pr.commit_count = detail_result.get("commits", 0)

                reviews_result = result_map["reviews"]
                if isinstance(reviews_result, Exception):
                    logger.warning(
                        f"  Could not fetch reviews for PR #{gh_pr['number']}: {reviews_result}"
                    )
                else:
                    await self._upsert_reviews(session, pr.id, reviews_result, gh_client=github)

                commenter_logins: set[str] = set()
                pr_author = gh_pr["user"]["login"]
                author_last_commented_at: datetime | None = None
                for key in ("issue_comments", "review_comments"):
                    comments_result = result_map[key]
                    if isinstance(comments_result, Exception):
                        logger.warning(
                            f"  Could not fetch comments for PR #{gh_pr['number']}: "
                            f"{comments_result}"
                        )
                        continue
                    for comment in comments_result:
                        login = comment.get("user", {}).get("login")
                        if login and login == pr_author:
                            ts = parse_gh_datetime(comment.get("created_at"))
                            if ts and (
                                author_last_commented_at is None or ts > author_last_commented_at
                            ):
                                author_last_commented_at = ts
                        elif login:
                            commenter_logins.add(login)
                pr.commenters = sorted(commenter_logins)
                pr.author_last_commented_at = author_last_commented_at

            # --- Unchanged open PRs: CI-only, no upsert overhead ---
            # PR data hasn't changed so skip _upsert_pr (avoids redundant
            # user lookups). Just look up the PR ID and refresh checks.
            for gh_pr in unchanged_open:
                if github.rate_limited:
                    logger.warning(f"  Aborting sync for {owner}/{name}: rate limit exhausted")
                    break

                existing_pr = (
                    await session.execute(
                        select(PullRequest).where(
                            PullRequest.repo_id == repo_id,
                            PullRequest.number == gh_pr["number"],
                        )
                    )
                ).scalar_one_or_none()
                if not existing_pr:
                    continue
                existing_pr.last_synced_at = now

                try:
                    runs_result = await github.get_workflow_runs(owner, name, gh_pr["head"]["sha"])
                except Exception as exc:
                    runs_result = exc

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
                    await self._upsert_check_runs(session, existing_pr.id, checks)

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

    async def sync_repo_lightweight(
        self,
        repo_id: int,
        owner: str,
        name: str,
        github: GitHubClient,
    ) -> None:
        """Lightweight sync for webhook-active repos: list-level upsert + stale detection only.

        Skips all detail fetches (get_pull, reviews, comments, workflow runs) since
        webhooks deliver those updates instantly. Still does ETag list fetches to catch
        any missed webhook deliveries and detect stale PRs.
        """
        logger.info(f"Lightweight syncing {owner}/{name} (webhook active)...")
        github.reset_rate_limited()
        now = datetime.now(UTC)

        from src.config.settings import settings

        open_etag_key = f"{owner}/{name}/pulls/open"
        closed_etag_key = f"{owner}/{name}/pulls/closed"

        gh_pulls, new_open_etag, open_modified = await github.list_open_pulls_with_etag(
            owner, name, etag=self._etag_cache.get(open_etag_key)
        )
        if new_open_etag:
            self._etag_cache[open_etag_key] = new_open_etag

        cutoff = now - timedelta(days=settings.merged_pr_lookback_days)
        (
            closed_pulls,
            new_closed_etag,
            closed_modified,
        ) = await github.list_recently_closed_pulls_with_etag(
            owner, name, cutoff, etag=self._etag_cache.get(closed_etag_key)
        )
        if new_closed_etag:
            self._etag_cache[closed_etag_key] = new_closed_etag

        # Both 304: nothing changed at all
        if not open_modified and not closed_modified:
            logger.info(
                f"  No changes detected (ETag 304) for {owner}/{name}, skipping lightweight sync"
            )
            async with async_session_factory() as session:
                repo_obj = await session.get(TrackedRepo, repo_id)
                if repo_obj:
                    repo_obj.last_synced_at = now
                await session.commit()
            await broadcast_event(
                "sync_complete",
                {"repo_id": repo_id, "owner": owner, "name": name},
            )
            return

        all_pulls = gh_pulls + closed_pulls
        fetched_pr_numbers = {gh_pr["number"] for gh_pr in all_pulls}
        logger.info(
            f"  Lightweight: {len(gh_pulls)} open, {len(closed_pulls)} closed PRs from list"
        )

        async with async_session_factory() as session:
            # Upsert basic PR data from list (no detail fetches)
            for gh_pr in all_pulls:
                await self._upsert_pr(session, repo_id, gh_pr, gh_client=github)

            # Stale PR detection: open in DB but not in GitHub's list
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
        logger.info(f"  Lightweight sync complete for {owner}/{name}")

    async def sync_single_pr(
        self,
        repo_id: int,
        owner: str,
        name: str,
        pr_number: int,
        github: GitHubClient,
    ) -> None:
        """Sync a single PR (used by webhook handler for instant updates)."""
        import time as _time

        start = _time.monotonic()
        logger.info(f"Webhook sync_single_pr: {owner}/{name}#{pr_number}")

        async with async_session_factory() as session:
            gh_pr = await github.get_pull(owner, name, pr_number)
            pr = await self._upsert_pr(session, repo_id, gh_pr, gh_client=github)

            # Extract detail fields that _upsert_pr doesn't handle
            pr.additions = gh_pr.get("additions", 0)
            pr.deletions = gh_pr.get("deletions", 0)
            pr.changed_files = gh_pr.get("changed_files", 0)
            pr.mergeable_state = gh_pr.get("mergeable_state")
            pr.commit_count = gh_pr.get("commits", 0)

            # Fetch detail, workflow runs, reviews, and comments in parallel
            (
                runs_result,
                reviews_result,
                issue_comments_result,
                review_comments_result,
            ) = await asyncio.gather(
                github.get_workflow_runs(owner, name, gh_pr["head"]["sha"]),
                github.get_reviews(owner, name, pr_number),
                github.get_issue_comments(owner, name, pr_number),
                github.get_review_comments(owner, name, pr_number),
                return_exceptions=True,
            )

            if isinstance(runs_result, Exception):
                logger.warning(
                    f"  Could not fetch workflow runs for PR #{pr_number}: {runs_result}"
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
                logger.warning(f"  Could not fetch reviews for PR #{pr_number}: {reviews_result}")
            else:
                await self._upsert_reviews(session, pr.id, reviews_result, gh_client=github)

            commenter_logins: set[str] = set()
            pr_author = gh_pr["user"]["login"]
            author_last_commented_at: datetime | None = None
            for comments_result in (issue_comments_result, review_comments_result):
                if isinstance(comments_result, Exception):
                    logger.warning(
                        f"  Could not fetch comments for PR #{pr_number}: {comments_result}"
                    )
                    continue
                for comment in comments_result:
                    login = comment.get("user", {}).get("login")
                    if login and login == pr_author:
                        ts = parse_gh_datetime(comment.get("created_at"))
                        if ts and (
                            author_last_commented_at is None or ts > author_last_commented_at
                        ):
                            author_last_commented_at = ts
                    elif login:
                        commenter_logins.add(login)
            pr.commenters = sorted(commenter_logins)
            pr.author_last_commented_at = author_last_commented_at

            # Fetch unresolved thread count for blocked PRs
            if pr.mergeable_state == "blocked":
                try:
                    counts = await github.get_unresolved_thread_counts(owner, name, [pr_number])
                    pr.unresolved_thread_count = counts.get(pr_number)
                except Exception as exc:
                    logger.warning(f"  Could not fetch thread counts for PR #{pr_number}: {exc}")
            else:
                pr.unresolved_thread_count = None

            await session.commit()

        # Re-detect stacks if head_ref/base_ref may have changed
        async with async_session_factory() as session:
            await detect_stacks(session, repo_id)
            await session.commit()

        await broadcast_event(
            "sync_complete",
            {"repo_id": repo_id, "owner": owner, "name": name},
        )
        elapsed = _time.monotonic() - start
        logger.info(
            f"Webhook sync_single_pr completed: {owner}/{name}#{pr_number} in {elapsed:.1f}s"
        )

    async def sync_checks_by_sha(
        self,
        repo_id: int,
        owner: str,
        name: str,
        head_sha: str,
        github: GitHubClient,
    ) -> None:
        """Sync check runs for all PRs matching a given head SHA."""
        logger.info(f"Webhook sync_checks_by_sha: {owner}/{name} sha={head_sha[:8]}")

        async with async_session_factory() as session:
            prs = (
                (
                    await session.execute(
                        select(PullRequest).where(
                            PullRequest.repo_id == repo_id,
                            PullRequest.head_sha == head_sha,
                            PullRequest.state == "open",
                        )
                    )
                )
                .scalars()
                .all()
            )

            if not prs:
                logger.debug(f"  No open PRs found for sha={head_sha[:8]}")
                return

            runs = await github.get_workflow_runs(owner, name, head_sha)
            checks = [
                {
                    "name": r["name"],
                    "status": r["status"],
                    "conclusion": r.get("conclusion"),
                    "details_url": r.get("html_url"),
                }
                for r in runs
            ]

            for pr in prs:
                await self._upsert_check_runs(session, pr.id, checks)
                logger.debug(f"  Updated {len(checks)} checks for PR #{pr.number}")

            await session.commit()

        await broadcast_event(
            "sync_complete",
            {"repo_id": repo_id, "owner": owner, "name": name},
        )

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

        # Filter GitHub labels to the allowed set
        synced_labels = [
            {"name": lbl["name"], "color": ALLOWED_LABELS[lbl["name"]]["color"]}
            for lbl in (gh_pr.get("labels") or [])
            if lbl["name"] in ALLOWED_LABELS
        ]

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
                labels=synced_labels,
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
            pr.labels = synced_labels

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
        """Fetch a user's full name from the GitHub API, returning None on failure.

        Skips the API call if we already attempted this login (even if the result
        was None), to avoid repeated calls for users without a public name.
        """
        if login in self._user_name_fetch_attempted:
            return None
        self._user_name_fetch_attempted.add(login)
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
