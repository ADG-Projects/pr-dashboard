"""API routes for tracked repositories."""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.auth import get_github_user_id
from src.api.schemas import RepoCreate, RepoDetail, RepoSummary, RepoVisibilityUpdate
from src.db.engine import get_session
from src.models.tables import CheckRun, PRStack, PullRequest, RepoTracker, Space, TrackedRepo
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient

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

    summaries: list[RepoSummary] = []
    for repo in repos:
        open_count = (
            await session.execute(
                select(func.count(PullRequest.id)).where(
                    PullRequest.repo_id == repo.id,
                    PullRequest.state == "open",
                )
            )
        ).scalar_one()

        failing_subq = (
            select(CheckRun.pull_request_id)
            .join(PullRequest)
            .where(
                PullRequest.repo_id == repo.id,
                PullRequest.state == "open",
                CheckRun.conclusion == "failure",
            )
            .distinct()
        )
        failing_count = (
            await session.execute(select(func.count()).select_from(failing_subq.subquery()))
        ).scalar_one()

        stale_cutoff = datetime.now(UTC) - timedelta(days=7)
        stale_count = (
            await session.execute(
                select(func.count(PullRequest.id)).where(
                    PullRequest.repo_id == repo.id,
                    PullRequest.state == "open",
                    PullRequest.updated_at < stale_cutoff,
                )
            )
        ).scalar_one()

        stack_count = (
            await session.execute(select(func.count(PRStack.id)).where(PRStack.repo_id == repo.id))
        ).scalar_one()

        tracker_count = (
            await session.execute(
                select(func.count(RepoTracker.id)).where(RepoTracker.repo_id == repo.id)
            )
        ).scalar_one()

        # Use current user's tracker for user-specific fields, or fall back to first shared tracker
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
                open_pr_count=open_count,
                failing_ci_count=failing_count,
                stale_pr_count=stale_count,
                stack_count=stack_count,
                space_id=space_id_val,
                space_name=space_name_val,
                visibility=visibility_val,
                user_id=user_id_val,
                tracker_count=tracker_count,
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
                client = GitHubClient(token=t, base_url=acct.base_url)
            else:
                client = GitHubClient()
        try:
            await svc.sync_repo(repo_id, owner, name, client)
        except Exception:
            logger.exception(f"Background sync failed for {owner}/{name}")
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
            # Create a new tracker for this user on the existing repo
            tracker = RepoTracker(
                user_id=repo_user_id,
                repo_id=existing.id,
                space_id=body.space_id,
            )
            session.add(tracker)
            await session.commit()
            asyncio.create_task(
                _background_sync(existing.id, existing.owner, existing.name, body.space_id)
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
        else:
            # Reactivate inactive repo
            existing.is_active = True
            existing.last_synced_at = None
            tracker = RepoTracker(
                user_id=repo_user_id,
                repo_id=existing.id,
                space_id=body.space_id,
            )
            session.add(tracker)
            await session.commit()
            asyncio.create_task(
                _background_sync(existing.id, existing.owner, existing.name, body.space_id)
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
    token = decrypt_token(account.encrypted_token) if account and account.encrypted_token else ""
    base_url = account.base_url if account else "https://api.github.com"
    gh = GitHubClient(token=token, base_url=base_url)
    try:
        gh_repo = await gh.get_repo(owner, body.name)
    except Exception as exc:
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

    asyncio.create_task(_background_sync(repo.id, repo.owner, repo.name, body.space_id))

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
    """Remove current user's tracking of a repo. Deactivates repo if no trackers remain."""
    user_id = get_github_user_id(request)

    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    if user_id:
        # Delete just this user's tracker
        tracker = (
            await session.execute(
                select(RepoTracker).where(
                    RepoTracker.user_id == user_id,
                    RepoTracker.repo_id == repo_id,
                )
            )
        ).scalar_one_or_none()
        if tracker:
            await session.delete(tracker)

    # If no trackers remain, deactivate the repo
    remaining = (
        await session.execute(
            select(func.count(RepoTracker.id)).where(RepoTracker.repo_id == repo_id)
        )
    ).scalar_one()
    if remaining == 0:
        repo.is_active = False

    await session.commit()


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

    # Prefer current user's tracker
    user_id = get_github_user_id(request)
    gh: GitHubClient | None = None
    sorted_trackers = sorted(trackers, key=lambda t: t.user_id != user_id)
    for tracker in sorted_trackers:
        if tracker.space and tracker.space.github_account:
            account = tracker.space.github_account
            if account.encrypted_token:
                token = decrypt_token(account.encrypted_token)
                gh = GitHubClient(token=token, base_url=account.base_url)
                break

    if not gh:
        from src.config.settings import settings

        if settings.github_token:
            gh = GitHubClient(token=settings.github_token)

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
