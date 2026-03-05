"""API routes for PR stacks."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.pulls import _compute_ci_status, _compute_review_state, _rebased_since_approval
from src.api.schemas import PRSummary, StackMemberOut, StackOut
from src.db.engine import get_session
from src.models.tables import (
    PRStack,
    PRStackMembership,
    PullRequest,
    TrackedRepo,
)

router = APIRouter(prefix="/api/repos/{repo_id}", tags=["stacks"])


def _pr_summary_from_model(pr: PullRequest) -> PRSummary:
    return PRSummary(
        id=pr.id,
        number=pr.number,
        title=pr.title,
        state=pr.state,
        draft=pr.draft,
        head_ref=pr.head_ref,
        base_ref=pr.base_ref,
        author=pr.author,
        additions=pr.additions,
        deletions=pr.deletions,
        changed_files=pr.changed_files,
        mergeable_state=pr.mergeable_state,
        html_url=pr.html_url,
        created_at=pr.created_at,
        updated_at=pr.updated_at,
        ci_status=_compute_ci_status(pr.check_runs),
        review_state=_compute_review_state(pr.reviews),
        rebased_since_approval=_rebased_since_approval(pr),
    )


@router.get("/stacks", response_model=list[StackOut])
async def list_stacks(repo_id: int, session: AsyncSession = Depends(get_session)) -> list[StackOut]:
    """List detected stacks for a repo."""
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    stacks = (
        (
            await session.execute(
                select(PRStack)
                .where(PRStack.repo_id == repo_id)
                .options(
                    selectinload(PRStack.memberships)
                    .selectinload(PRStackMembership.pull_request)
                    .selectinload(PullRequest.check_runs),
                    selectinload(PRStack.memberships)
                    .selectinload(PRStackMembership.pull_request)
                    .selectinload(PullRequest.reviews),
                )
                .order_by(PRStack.detected_at.desc())
            )
        )
        .scalars()
        .all()
    )

    return [
        StackOut(
            id=s.id,
            name=s.name,
            root_pr_id=s.root_pr_id,
            detected_at=s.detected_at,
            members=[
                StackMemberOut(
                    pull_request_id=m.pull_request_id,
                    position=m.position,
                    parent_pr_id=m.parent_pr_id,
                    pr=_pr_summary_from_model(m.pull_request),
                )
                for m in sorted(s.memberships, key=lambda x: x.position)
            ],
        )
        for s in stacks
    ]


@router.get("/stacks/{stack_id}", response_model=StackOut)
async def get_stack(
    repo_id: int, stack_id: int, session: AsyncSession = Depends(get_session)
) -> StackOut:
    """Get a single stack with ordered PRs and quality data."""
    result = await session.execute(
        select(PRStack)
        .where(PRStack.id == stack_id, PRStack.repo_id == repo_id)
        .options(
            selectinload(PRStack.memberships)
            .selectinload(PRStackMembership.pull_request)
            .selectinload(PullRequest.check_runs),
            selectinload(PRStack.memberships)
            .selectinload(PRStackMembership.pull_request)
            .selectinload(PullRequest.reviews),
        )
    )
    stack = result.scalar_one_or_none()
    if not stack:
        raise HTTPException(status_code=404, detail="Stack not found")

    return StackOut(
        id=stack.id,
        name=stack.name,
        root_pr_id=stack.root_pr_id,
        detected_at=stack.detected_at,
        members=[
            StackMemberOut(
                pull_request_id=m.pull_request_id,
                position=m.position,
                parent_pr_id=m.parent_pr_id,
                pr=_pr_summary_from_model(m.pull_request),
            )
            for m in sorted(stack.memberships, key=lambda x: x.position)
        ],
    )
