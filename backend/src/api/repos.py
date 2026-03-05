"""API routes for tracked repositories."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import AvailableRepo, RepoCreate, RepoDetail, RepoSummary
from src.config.settings import settings
from src.db.engine import get_session
from src.models.tables import CheckRun, PRStack, PullRequest, TrackedRepo
from src.services.github_client import GitHubClient

router = APIRouter(prefix="/api/repos", tags=["repos"])


@router.get("", response_model=list[RepoSummary])
async def list_repos(session: AsyncSession = Depends(get_session)) -> list[RepoSummary]:
    """List all tracked repos with summary stats."""
    repos = (await session.execute(select(TrackedRepo))).scalars().all()
    summaries: list[RepoSummary] = []

    for repo in repos:
        # Count open PRs
        open_count = (
            await session.execute(
                select(func.count(PullRequest.id)).where(
                    PullRequest.repo_id == repo.id,
                    PullRequest.state == "open",
                )
            )
        ).scalar_one()

        # Count PRs with failing CI
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

        # Count stale PRs (no update in 7 days)
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

        # Count stacks
        stack_count = (
            await session.execute(
                select(func.count(PRStack.id)).where(PRStack.repo_id == repo.id)
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
            )
        )

    return summaries


@router.get("/available", response_model=list[AvailableRepo])
async def list_available_repos(
    session: AsyncSession = Depends(get_session),
) -> list[AvailableRepo]:
    """List org repos not yet tracked (excludes archived repos)."""
    tracked = (await session.execute(select(TrackedRepo.full_name))).scalars().all()
    tracked_set = set(tracked)

    gh = GitHubClient()
    try:
        org_repos = await gh.list_org_repos(settings.github_org)
    finally:
        await gh.close()

    return [
        AvailableRepo(
            name=r["name"],
            full_name=r["full_name"],
            description=r.get("description"),
            private=r.get("private", False),
        )
        for r in org_repos
        if not r.get("archived") and r["full_name"] not in tracked_set
    ]


@router.post("", response_model=RepoDetail, status_code=201)
async def add_repo(
    body: RepoCreate, session: AsyncSession = Depends(get_session)
) -> RepoDetail:
    """Add a repo to track."""
    owner = body.owner or settings.github_org
    full_name = f"{owner}/{body.name}"

    # Check for duplicates
    existing = (
        await session.execute(
            select(TrackedRepo).where(TrackedRepo.full_name == full_name)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"{full_name} is already tracked")

    # Validate repo exists on GitHub
    gh = GitHubClient()
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
    )
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    logger.info(f"Now tracking {full_name}")

    return RepoDetail(
        id=repo.id,
        owner=repo.owner,
        name=repo.name,
        full_name=repo.full_name,
        is_active=repo.is_active,
        default_branch=repo.default_branch,
        last_synced_at=repo.last_synced_at,
        created_at=repo.created_at,
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
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    # Import here to avoid circular import at module level
    from src.services.sync_service import SyncService

    svc = SyncService()
    try:
        await svc.sync_repo(repo.id, repo.owner, repo.name)
    finally:
        await svc.github.close()

    return {"status": "sync complete", "repo": repo.full_name}
