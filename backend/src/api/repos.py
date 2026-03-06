"""API routes for tracked repositories."""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.api.auth import get_github_user_id
from src.api.schemas import RepoCreate, RepoDetail, RepoSummary, RepoVisibilityUpdate
from src.db.engine import get_session
from src.models.tables import CheckRun, PRStack, PullRequest, Space, TrackedRepo
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
    stmt = (
        select(TrackedRepo)
        .options(joinedload(TrackedRepo.space))
        .where(TrackedRepo.is_active.is_(True))
    )
    # Visibility filter: direct repo-level visibility
    if user_id:
        stmt = stmt.where(
            or_(
                TrackedRepo.visibility == "shared",
                TrackedRepo.user_id == user_id,
            )
        )
    else:
        stmt = stmt.where(TrackedRepo.visibility == "shared")
    if space_id is not None:
        stmt = stmt.where(TrackedRepo.space_id == space_id)
    stmt = stmt.order_by(TrackedRepo.full_name)

    repos = (await session.execute(stmt)).scalars().unique().all()
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
                space_id=repo.space_id,
                space_name=repo.space.name if repo.space else None,
                visibility=repo.visibility,
                user_id=repo.user_id,
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
    repo_user_id = get_github_user_id(request)
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.visibility = "private"
            existing.space_id = body.space_id
            existing.user_id = repo_user_id
            existing.last_synced_at = None
            await session.commit()
            await session.refresh(existing)
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
                space_id=existing.space_id,
                visibility=existing.visibility,
                user_id=existing.user_id,
            )
        raise HTTPException(status_code=409, detail=f"{full_name} is already tracked")

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
        space_id=body.space_id,
        user_id=repo_user_id,
    )
    session.add(repo)
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
        space_id=repo.space_id,
        visibility=repo.visibility,
        user_id=repo.user_id,
    )


@router.delete("/{repo_id}", status_code=204)
async def remove_repo(repo_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """Stop tracking a repo (soft-delete)."""
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    repo.is_active = False
    await session.commit()


@router.post("/{repo_id}/sync", status_code=202)
async def force_sync(repo_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Trigger an immediate sync for a repo."""
    result = await session.execute(
        select(TrackedRepo)
        .options(joinedload(TrackedRepo.space).selectinload(Space.github_account))
        .where(TrackedRepo.id == repo_id)
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    from src.services.sync_service import SyncService

    svc = SyncService()

    gh: GitHubClient | None = None
    account = repo.space.github_account if repo.space else None
    if account and account.encrypted_token:
        token = decrypt_token(account.encrypted_token)
        gh = GitHubClient(token=token, base_url=account.base_url)

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
    """Set a repo's visibility (private or shared). Owner only."""
    if body.visibility not in ("private", "shared"):
        raise HTTPException(status_code=400, detail="visibility must be 'private' or 'shared'")

    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    if repo.user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the repo owner can change visibility")

    repo.visibility = body.visibility
    await session.commit()
    await session.refresh(repo, attribute_names=["space"])

    logger.info(f"Repo '{repo.full_name}' visibility set to '{repo.visibility}'")

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
        space_id=repo.space_id,
        space_name=repo.space.name if repo.space else None,
        visibility=repo.visibility,
        user_id=repo.user_id,
    )
