"""API routes for tracked repositories."""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.auth import get_github_user_id
from src.api.schemas import RepoCreate, RepoDetail, RepoSummary, RepoVisibilityUpdate
from src.api.webhook_admin import auto_register_webhook
from src.db.engine import async_session_factory, get_session
from src.models.tables import CheckRun, PRStack, PullRequest, RepoTracker, Space, TrackedRepo
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient

# Strong references to background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    """Add task to the set and register cleanup callback."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("", response_model=list[RepoSummary])
async def list_repos(
    request: Request,
    space_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[RepoSummary]:
    """List tracked repos visible to the current user, optionally filtered by space."""
    user_id = get_github_user_id(request)

    # Base: active repos that the user tracks OR that any tracker has shared
    stmt = select(TrackedRepo).where(TrackedRepo.is_active.is_(True))
    if user_id:
        # Subquery: repo IDs the user directly tracks
        user_tracker_ids = select(RepoTracker.repo_id).where(RepoTracker.user_id == user_id)
        # Subquery: repo IDs shared by anyone
        shared_ids = select(RepoTracker.repo_id).where(RepoTracker.visibility == "shared")
        stmt = stmt.where(
            or_(
                TrackedRepo.id.in_(user_tracker_ids),
                TrackedRepo.id.in_(shared_ids),
            )
        )
    else:
        shared_ids = select(RepoTracker.repo_id).where(RepoTracker.visibility == "shared")
        stmt = stmt.where(TrackedRepo.id.in_(shared_ids))

    if space_id is not None:
        tracker_repo_ids = select(RepoTracker.repo_id).where(RepoTracker.space_id == space_id)
        stmt = stmt.where(TrackedRepo.id.in_(tracker_repo_ids))

    stmt = stmt.order_by(TrackedRepo.full_name)
    repos = (await session.execute(stmt)).scalars().unique().all()

    # Preload all tracker data for the current user (for populating user-specific fields)
    user_trackers: dict[int, RepoTracker] = {}
    if user_id:
        tracker_result = await session.execute(
            select(RepoTracker)
            .options(selectinload(RepoTracker.space))
            .where(
                RepoTracker.user_id == user_id,
                RepoTracker.repo_id.in_([r.id for r in repos]),
            )
        )
        for t in tracker_result.scalars().all():
            user_trackers[t.repo_id] = t

    # Batch-load all counts in a single query per metric
    repo_ids = [r.id for r in repos]

    if repo_ids:
        stale_cutoff = datetime.now(UTC) - timedelta(days=7)

        # Open PR counts
        open_counts_rows = (
            await session.execute(
                select(PullRequest.repo_id, func.count(PullRequest.id))
                .where(PullRequest.repo_id.in_(repo_ids), PullRequest.state == "open")
                .group_by(PullRequest.repo_id)
            )
        ).all()
        open_counts = dict(open_counts_rows)

        # Failing CI counts (distinct PRs with at least one failing check)
        failing_subq = (
            select(PullRequest.repo_id, CheckRun.pull_request_id)
            .join(CheckRun, CheckRun.pull_request_id == PullRequest.id)
            .where(
                PullRequest.repo_id.in_(repo_ids),
                PullRequest.state == "open",
                CheckRun.conclusion == "failure",
            )
            .distinct()
            .subquery()
        )
        failing_counts_rows = (
            await session.execute(
                select(failing_subq.c.repo_id, func.count()).group_by(failing_subq.c.repo_id)
            )
        ).all()
        failing_counts = dict(failing_counts_rows)

        # Stale PR counts
        stale_counts_rows = (
            await session.execute(
                select(PullRequest.repo_id, func.count(PullRequest.id))
                .where(
                    PullRequest.repo_id.in_(repo_ids),
                    PullRequest.state == "open",
                    PullRequest.updated_at < stale_cutoff,
                )
                .group_by(PullRequest.repo_id)
            )
        ).all()
        stale_counts = dict(stale_counts_rows)

        # Stack counts
        stack_counts_rows = (
            await session.execute(
                select(PRStack.repo_id, func.count(PRStack.id))
                .where(PRStack.repo_id.in_(repo_ids))
                .group_by(PRStack.repo_id)
            )
        ).all()
        stack_counts = dict(stack_counts_rows)

        # Tracker counts
        tracker_counts_rows = (
            await session.execute(
                select(RepoTracker.repo_id, func.count(RepoTracker.id))
                .where(RepoTracker.repo_id.in_(repo_ids))
                .group_by(RepoTracker.repo_id)
            )
        ).all()
        tracker_counts = dict(tracker_counts_rows)
    else:
        open_counts = {}
        failing_counts = {}
        stale_counts = {}
        stack_counts = {}
        tracker_counts = {}

    summaries: list[RepoSummary] = []
    for repo in repos:
        tracker = user_trackers.get(repo.id)
        space_id_val = tracker.space_id if tracker else None
        space_name_val = tracker.space.name if tracker and tracker.space else None
        visibility_val = tracker.visibility if tracker else "shared"
        user_id_val = tracker.user_id if tracker else None

        summaries.append(
            RepoSummary(
                id=repo.id,
                owner=repo.owner,
                name=repo.name,
                full_name=repo.full_name,
                is_active=repo.is_active,
                default_branch=repo.default_branch,
                last_synced_at=repo.last_synced_at,
                open_pr_count=open_counts.get(repo.id, 0),
                failing_ci_count=failing_counts.get(repo.id, 0),
                stale_pr_count=stale_counts.get(repo.id, 0),
                stack_count=stack_counts.get(repo.id, 0),
                space_id=space_id_val,
                space_name=space_name_val,
                visibility=visibility_val,
                user_id=user_id_val,
                tracker_count=tracker_counts.get(repo.id, 0),
            )
        )

    return summaries


@router.post("", response_model=RepoDetail, status_code=201)
async def add_repo(
    body: RepoCreate, request: Request, session: AsyncSession = Depends(get_session)
) -> RepoDetail:
    """Add a repo to track. Requires space_id to determine which token to use."""
    if not body.space_id:
        raise HTTPException(status_code=400, detail="space_id is required")

    result = await session.execute(
        select(Space).options(selectinload(Space.github_account)).where(Space.id == body.space_id)
    )
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    owner = body.owner or space.slug
    full_name = f"{owner}/{body.name}"
    repo_user_id = get_github_user_id(request)

    async def _background_sync(repo_id: int, owner: str, name: str, space_id: int) -> None:
        from src.services.sync_service import SyncService

        svc = SyncService()
        from src.db.engine import async_session_factory

        async with async_session_factory() as s:
            result = await s.execute(
                select(Space)
                .options(selectinload(Space.github_account))
                .where(Space.id == space_id)
            )
            sp = result.scalar_one_or_none()
            acct = sp.github_account if sp else None
            if acct and acct.encrypted_token:
                t = decrypt_token(acct.encrypted_token)
                if t:
                    client = GitHubClient(token=t, base_url=acct.base_url)
                else:
                    logger.warning(
                        f"Cannot decrypt token for {owner}/{name}, using unauthenticated client"
                    )
                    client = GitHubClient()
            else:
                logger.warning(
                    f"No token available for {owner}/{name}, using unauthenticated client"
                )
                client = GitHubClient()
        try:
            await svc.sync_repo(repo_id, owner, name, client)
        except Exception:
            logger.exception(f"Background sync failed for {owner}/{name}")
            from src.services.events import broadcast_event

            await broadcast_event(
                "sync_error",
                {
                    "repo_id": repo_id,
                    "owner": owner,
                    "name": name,
                    "error": f"Sync failed for {owner}/{name}",
                },
            )
        finally:
            await client.close()

    existing = (
        await session.execute(select(TrackedRepo).where(TrackedRepo.full_name == full_name))
    ).scalar_one_or_none()

    if existing:
        if existing.is_active:
            # Check if current user already has a tracker
            if repo_user_id:
                existing_tracker = (
                    await session.execute(
                        select(RepoTracker).where(
                            RepoTracker.user_id == repo_user_id,
                            RepoTracker.repo_id == existing.id,
                        )
                    )
                ).scalar_one_or_none()
                if existing_tracker:
                    raise HTTPException(
                        status_code=409, detail="You are already tracking this repo"
                    )
        else:
            # Reactivate inactive repo
            existing.is_active = True
            existing.last_synced_at = None

        # Create a new tracker for this user on the existing repo.
        # The repo may have been deleted by sync cleanup between the SELECT above
        # and this INSERT, so flush to catch FK violations early.
        tracker = RepoTracker(
            user_id=repo_user_id,
            repo_id=existing.id,
            space_id=body.space_id,
        )
        session.add(tracker)
        try:
            await session.flush()
        except Exception:
            # Repo was deleted between the check and the insert; fall through
            # to create a fresh repo below.
            await session.rollback()
            existing = None

    if existing:
        await session.commit()
        _track_task(
            asyncio.create_task(
                _background_sync(existing.id, existing.owner, existing.name, body.space_id)
            )
        )
        _track_task(
            asyncio.create_task(auto_register_webhook(existing.id, existing.owner, existing.name))
        )
        return RepoDetail(
            id=existing.id,
            owner=existing.owner,
            name=existing.name,
            full_name=existing.full_name,
            is_active=existing.is_active,
            default_branch=existing.default_branch,
            last_synced_at=existing.last_synced_at,
            created_at=existing.created_at,
            space_id=body.space_id,
            visibility="private",
            user_id=repo_user_id,
        )

    # Validate repo exists on GitHub using the space's account token
    account = space.github_account
    encrypted = account.encrypted_token if account else None
    token = (decrypt_token(encrypted) if encrypted else None) or ""
    base_url = account.base_url if account else "https://api.github.com"
    gh = GitHubClient(token=token, base_url=base_url)
    try:
        gh_repo = await gh.get_repo(owner, body.name)
    except Exception as exc:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 401:
                raise HTTPException(
                    status_code=401, detail="GitHub token is invalid or expired"
                ) from exc
            if status == 403:
                raise HTTPException(
                    status_code=403, detail="GitHub token lacks permission"
                ) from exc
            if status == 429:
                raise HTTPException(
                    status_code=429, detail="GitHub API rate limit exceeded"
                ) from exc
        raise HTTPException(status_code=404, detail=f"GitHub repo {full_name} not found") from exc
    finally:
        await gh.close()

    repo = TrackedRepo(
        owner=owner,
        name=body.name,
        full_name=full_name,
        default_branch=gh_repo.get("default_branch", "main"),
    )
    session.add(repo)
    await session.flush()

    tracker = RepoTracker(
        user_id=repo_user_id,
        repo_id=repo.id,
        space_id=body.space_id,
    )
    session.add(tracker)
    await session.commit()
    await session.refresh(repo)
    logger.info(f"Now tracking {full_name}")

    _track_task(
        asyncio.create_task(_background_sync(repo.id, repo.owner, repo.name, body.space_id))
    )
    _track_task(asyncio.create_task(auto_register_webhook(repo.id, repo.owner, repo.name)))

    return RepoDetail(
        id=repo.id,
        owner=repo.owner,
        name=repo.name,
        full_name=repo.full_name,
        is_active=repo.is_active,
        default_branch=repo.default_branch,
        last_synced_at=repo.last_synced_at,
        created_at=repo.created_at,
        space_id=body.space_id,
        visibility="private",
        user_id=repo_user_id,
    )


@router.delete("/{repo_id}", status_code=204)
async def remove_repo(
    repo_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> None:
    """Remove current user's tracking of a repo. Deletes repo if no trackers remain."""
    user_id = get_github_user_id(request)

    repo_row = (
        await session.execute(select(TrackedRepo).where(TrackedRepo.id == repo_id))
    ).scalar_one_or_none()
    if not repo_row:
        raise HTTPException(status_code=404, detail="Repo not found")
    repo_name = repo_row.full_name

    if user_id:
        # Delete just this user's tracker via bulk SQL (no ORM lock)
        await session.execute(
            delete(RepoTracker).where(
                RepoTracker.user_id == user_id,
                RepoTracker.repo_id == repo_id,
            )
        )

    await session.commit()
    logger.info(f"Untracked repo {repo_name} for user {user_id}")

    # Delete webhook from GitHub if the repo had one and no trackers remain
    async with async_session_factory() as check_session:
        remaining_count = (
            await check_session.execute(
                select(func.count(RepoTracker.id)).where(RepoTracker.repo_id == repo_id)
            )
        ).scalar_one()
        if remaining_count == 0 and repo_row.github_webhook_id:
            from src.api.webhook_admin import _get_client_for_repo

            gh = await _get_client_for_repo(repo_id)
            if gh:
                try:
                    await gh.delete_webhook(
                        repo_row.owner, repo_row.name, repo_row.github_webhook_id
                    )
                    logger.info(
                        f"Deleted webhook {repo_row.github_webhook_id} from {repo_name} on untrack"
                    )
                except Exception as exc:
                    logger.warning(f"Failed to delete webhook from {repo_name}: {exc}")
                finally:
                    await gh.close()

    # Try to delete the orphaned repo in a fresh session. If a sync holds the
    # row lock, NOWAIT fails fast and the sync service cleans up after it finishes.
    async with async_session_factory() as cleanup_session:
        remaining = (
            await cleanup_session.execute(
                select(func.count(RepoTracker.id)).where(RepoTracker.repo_id == repo_id)
            )
        ).scalar_one()
        if remaining == 0:
            try:
                await cleanup_session.execute(
                    select(TrackedRepo.id)
                    .where(TrackedRepo.id == repo_id)
                    .with_for_update(nowait=True)
                )
                await cleanup_session.execute(delete(TrackedRepo).where(TrackedRepo.id == repo_id))
                await cleanup_session.commit()
                logger.info(f"Deleted repo {repo_name}: no trackers remain")
            except Exception:
                await cleanup_session.rollback()
                logger.info(f"Deferred deletion of repo {repo_name}: sync in progress")
        else:
            logger.info(f"Repo {repo_name} still has {remaining} tracker(s)")


@router.post("/{repo_id}/sync", status_code=202)
async def force_sync(
    repo_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    """Trigger an immediate sync for a repo."""
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    # Find a token from any tracker's space
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

    # Only use the requesting user's tracker token
    user_id = get_github_user_id(request)
    gh: GitHubClient | None = None
    for tracker in (t for t in trackers if t.user_id == user_id):
        if tracker.space and tracker.space.github_account:
            account = tracker.space.github_account
            if account.encrypted_token:
                token = decrypt_token(account.encrypted_token)
                if token:
                    gh = GitHubClient(token=token, base_url=account.base_url)
                    break

    if not gh:
        raise HTTPException(status_code=400, detail="No GitHub token available for this repo")

    from src.services.sync_service import SyncService

    svc = SyncService()
    try:
        await svc.sync_repo(repo.id, repo.owner, repo.name, gh)
    finally:
        if gh:
            await gh.close()

    return {"status": "sync complete", "repo": repo.full_name}


@router.patch("/{repo_id}/visibility", response_model=RepoSummary)
async def set_repo_visibility(
    repo_id: int,
    body: RepoVisibilityUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RepoSummary:
    """Set a repo's visibility (private or shared). Updates the current user's tracker."""
    if body.visibility not in ("private", "shared"):
        raise HTTPException(status_code=400, detail="visibility must be 'private' or 'shared'")

    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    tracker = (
        await session.execute(
            select(RepoTracker)
            .options(selectinload(RepoTracker.space))
            .where(
                RepoTracker.user_id == user_id,
                RepoTracker.repo_id == repo_id,
            )
        )
    ).scalar_one_or_none()
    if not tracker:
        raise HTTPException(status_code=403, detail="You are not tracking this repo")

    tracker.visibility = body.visibility
    await session.commit()

    repo = await session.get(TrackedRepo, repo_id)
    logger.info(
        f"Repo '{repo.full_name}' visibility set to '{tracker.visibility}' by user {user_id}"
    )

    return RepoSummary(
        id=repo.id,
        owner=repo.owner,
        name=repo.name,
        full_name=repo.full_name,
        is_active=repo.is_active,
        default_branch=repo.default_branch,
        last_synced_at=repo.last_synced_at,
        open_pr_count=0,
        failing_ci_count=0,
        stale_pr_count=0,
        stack_count=0,
        space_id=tracker.space_id,
        space_name=tracker.space.name if tracker.space else None,
        visibility=tracker.visibility,
        user_id=tracker.user_id,
    )
