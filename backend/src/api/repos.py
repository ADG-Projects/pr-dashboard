"""API routes for tracked repositories."""

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.schemas import RepoCreate, RepoDetail, RepoSummary
from src.db.engine import get_session
from src.models.tables import CheckRun, PRStack, PullRequest, Space, TrackedRepo
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient

router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("", response_model=list[RepoSummary])
async def list_repos(
    space_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[RepoSummary]:
    """List all tracked repos with summary stats, optionally filtered by space."""
    stmt = (
        select(TrackedRepo)
        .options(joinedload(TrackedRepo.space))
        .where(TrackedRepo.is_active.is_(True))
    )
    if space_id is not None:
        stmt = stmt.where(TrackedRepo.space_id == space_id)

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
            await session.execute(
                select(func.count()).select_from(failing_subq.subquery())
            )
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
            await session.execute(
                select(func.count(PRStack.id)).where(
                    PRStack.repo_id == repo.id
                )
            )
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
            )
        )

    return summaries


@router.post("", response_model=RepoDetail, status_code=201)
async def add_repo(
    body: RepoCreate, session: AsyncSession = Depends(get_session)
) -> RepoDetail:
    """Add a repo to track. Requires space_id to determine which token to use."""
    if not body.space_id:
        raise HTTPException(
            status_code=400, detail="space_id is required"
        )

    space = await session.get(Space, body.space_id)
    if not space or not space.is_active:
        raise HTTPException(status_code=404, detail="Space not found")

    owner = body.owner or space.slug
    full_name = f"{owner}/{body.name}"

    existing = (
        await session.execute(
            select(TrackedRepo).where(TrackedRepo.full_name == full_name)
        )
    ).scalar_one_or_none()
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.space_id = body.space_id
            await session.commit()
            await session.refresh(existing)
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
            )
        raise HTTPException(
            status_code=409, detail=f"{full_name} is already tracked"
        )

    # Validate repo exists on GitHub using space's token
    token = decrypt_token(space.encrypted_token) if space.encrypted_token else ""
    gh = GitHubClient(token=token, base_url=space.base_url)
    try:
        gh_repo = await gh.get_repo(owner, body.name)
    except Exception as exc:
        raise HTTPException(
            status_code=404, detail=f"GitHub repo {full_name} not found"
        ) from exc
    finally:
        await gh.close()

    repo = TrackedRepo(
        owner=owner,
        name=body.name,
        full_name=full_name,
        default_branch=gh_repo.get("default_branch", "main"),
        space_id=body.space_id,
    )
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    logger.info(f"Now tracking {full_name}")

    async def _background_sync(
        repo_id: int, owner: str, name: str, space_id: int
    ) -> None:
        from src.services.sync_service import SyncService

        svc = SyncService()
        # Get space's client
        from src.db.engine import async_session_factory

        async with async_session_factory() as s:
            sp = await s.get(Space, space_id)
            if sp and sp.encrypted_token:
                t = decrypt_token(sp.encrypted_token)
                client = GitHubClient(token=t, base_url=sp.base_url)
            else:
                client = GitHubClient()
        try:
            await svc.sync_repo(repo_id, owner, name, client)
        except Exception:
            logger.exception(f"Background sync failed for {owner}/{name}")
        finally:
            await client.close()

    asyncio.create_task(
        _background_sync(repo.id, repo.owner, repo.name, body.space_id)
    )

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
    )


@router.delete("/{repo_id}", status_code=204)
async def remove_repo(
    repo_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    """Stop tracking a repo (soft-delete)."""
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    repo.is_active = False
    await session.commit()


@router.post("/{repo_id}/sync", status_code=202)
async def force_sync(
    repo_id: int, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    """Trigger an immediate sync for a repo."""
    result = await session.execute(
        select(TrackedRepo)
        .options(joinedload(TrackedRepo.space))
        .where(TrackedRepo.id == repo_id)
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    from src.services.sync_service import SyncService

    svc = SyncService()

    gh: GitHubClient | None = None
    if repo.space and repo.space.encrypted_token:
        token = decrypt_token(repo.space.encrypted_token)
        gh = GitHubClient(token=token, base_url=repo.space.base_url)

    try:
        await svc.sync_repo(repo.id, repo.owner, repo.name, gh)
    finally:
        if gh:
            await gh.close()

    return {"status": "sync complete", "repo": repo.full_name}
