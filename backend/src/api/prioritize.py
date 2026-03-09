"""API routes for PR prioritization — computes priority scores and optimal review/merge order."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.api.auth import get_github_user_id
from src.api.pulls import (
    _compute_ci_status,
    _compute_review_state,
    _pr_to_summary,
    _rebased_since_approval,
)
from src.api.schemas import PrioritizedPROut, PriorityBreakdown
from src.db.engine import get_session
from src.models.tables import PRStack, PRStackMembership, PullRequest, RepoTracker, TrackedRepo

router = APIRouter(prefix="/api/pulls", tags=["prioritize"])


def compute_priority_score(
    review_state: str,
    ci_status: str,
    total_lines: int,
    mergeable_state: str | None,
    created_at: datetime,
    rebased_since_approval: bool,
    draft: bool,
) -> tuple[int, PriorityBreakdown]:
    """Pure function: compute priority score (0–100) from PR signals."""
    # Review readiness (max 35)
    review_scores = {"approved": 35, "reviewed": 20, "none": 10, "changes_requested": 0}
    review_pts = review_scores.get(review_state, 10)

    # CI status (max 25)
    ci_scores = {"success": 25, "pending": 10, "unknown": 5, "failure": 0}
    ci_pts = ci_scores.get(ci_status, 5)

    # Size — inverse, smaller = higher (max 15)
    if total_lines <= 50:
        size_pts = 15
    elif total_lines <= 200:
        size_pts = 12
    elif total_lines <= 500:
        size_pts = 8
    elif total_lines <= 1000:
        size_pts = 4
    else:
        size_pts = 0

    # Mergeable state (max 10)
    merge_scores = {"clean": 10, "unstable": 5}
    mergeable_pts = merge_scores.get(mergeable_state or "", 0)

    # Age — older PRs get higher priority, linear 0→10 over 7 days (max 10)
    tz = created_at.tzinfo or UTC
    age_days = (datetime.now(UTC) - created_at.replace(tzinfo=tz)).total_seconds() / 86400
    age_pts = min(10, int(age_days * 10 / 7))

    # Rebase status (max 5) — not rebased since approval = needs re-review
    rebase_pts = 5 if rebased_since_approval else 0

    # Draft penalty
    draft_penalty = -30 if draft else 0

    total = max(
        0, review_pts + ci_pts + size_pts + mergeable_pts + age_pts + rebase_pts + draft_penalty
    )

    breakdown = PriorityBreakdown(
        review=review_pts,
        ci=ci_pts,
        size=size_pts,
        mergeable=mergeable_pts,
        age=age_pts,
        rebase=rebase_pts,
        draft_penalty=draft_penalty,
    )
    return total, breakdown


def _build_merge_order(
    scored_prs: list[dict],
    stack_memberships: list[PRStackMembership],
    stacks: list[PRStack],
) -> list[dict]:
    """Sort PRs into optimal review/merge order respecting stack dependencies.

    - Standalone PRs: sorted by priority score descending
    - Stacked PRs: topological order within stack (parent before child).
      Stack position in global list determined by root PR's score.
    """
    # Map PR id → scored entry
    pr_map = {entry["pr_id"]: entry for entry in scored_prs}

    # Map PR id → stack membership
    pr_to_membership: dict[int, PRStackMembership] = {}
    for m in stack_memberships:
        pr_to_membership[m.pull_request_id] = m

    # Group memberships by stack
    stack_members: dict[int, list[PRStackMembership]] = {}
    for m in stack_memberships:
        stack_members.setdefault(m.stack_id, []).append(m)

    # Stack id → name
    stack_info = {s.id: s for s in stacks}

    # Identify which PRs are in stacks
    stacked_pr_ids = set(pr_to_membership.keys()) & set(pr_map.keys())
    standalone = [e for e in scored_prs if e["pr_id"] not in stacked_pr_ids]

    # For each stack, produce topologically sorted PRs (by position)
    stack_groups: list[tuple[int, list[dict]]] = []  # (root_score, ordered_entries)
    seen_stacks: set[int] = set()

    for pr_id in stacked_pr_ids:
        m = pr_to_membership[pr_id]
        if m.stack_id in seen_stacks:
            continue
        seen_stacks.add(m.stack_id)

        members = stack_members.get(m.stack_id, [])
        # Sort by position (parent first)
        members.sort(key=lambda x: x.position)

        ordered = []
        for mem in members:
            if mem.pull_request_id in pr_map:
                entry = pr_map[mem.pull_request_id]
                s = stack_info.get(m.stack_id)
                entry["stack_id"] = m.stack_id
                entry["stack_name"] = s.name if s else None
                entry["blocked_by_pr_id"] = mem.parent_pr_id if mem.parent_pr_id in pr_map else None
                ordered.append(entry)

        # Root score = score of first PR in stack (lowest position)
        root_score = ordered[0]["score"] if ordered else 0
        stack_groups.append((root_score, ordered))

    # Sort standalone by score desc
    standalone.sort(key=lambda e: e["score"], reverse=True)

    # Sort stack groups by root score desc
    stack_groups.sort(key=lambda g: g[0], reverse=True)

    # Merge: interleave stacks and standalone PRs by comparing scores
    result: list[dict] = []
    si = 0  # standalone index
    gi = 0  # stack group index

    while si < len(standalone) or gi < len(stack_groups):
        standalone_score = standalone[si]["score"] if si < len(standalone) else -1
        stack_score = stack_groups[gi][0] if gi < len(stack_groups) else -1

        if standalone_score >= stack_score:
            result.append(standalone[si])
            si += 1
        else:
            for entry in stack_groups[gi][1]:
                result.append(entry)
            gi += 1

    return result


@router.get("/prioritized", response_model=list[PrioritizedPROut])
async def list_prioritized(
    request: Request,
    repo_id: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[PrioritizedPROut]:
    """Return open PRs ranked by priority score. Optionally scoped to a single repo."""
    user_id = get_github_user_id(request)

    # Reuse visibility logic from repos.py
    repo_stmt = select(TrackedRepo.id).where(TrackedRepo.is_active.is_(True))
    if user_id:
        user_tracker_ids = select(RepoTracker.repo_id).where(RepoTracker.user_id == user_id)
        shared_ids = select(RepoTracker.repo_id).where(RepoTracker.visibility == "shared")
        repo_stmt = repo_stmt.where(
            or_(
                TrackedRepo.id.in_(user_tracker_ids),
                TrackedRepo.id.in_(shared_ids),
            )
        )
    else:
        shared_ids = select(RepoTracker.repo_id).where(RepoTracker.visibility == "shared")
        repo_stmt = repo_stmt.where(TrackedRepo.id.in_(shared_ids))

    visible_repo_ids = (await session.execute(repo_stmt)).scalars().all()
    if not visible_repo_ids:
        return []

    # Optionally scope to a single repo (must still be visible)
    if repo_id is not None:
        if repo_id not in visible_repo_ids:
            return []
        visible_repo_ids = [repo_id]

    # Fetch all open PRs from visible repos
    prs = (
        (
            await session.execute(
                select(PullRequest)
                .options(
                    selectinload(PullRequest.check_runs),
                    selectinload(PullRequest.reviews),
                    joinedload(PullRequest.assignee),
                    joinedload(PullRequest.repo),
                )
                .where(
                    PullRequest.repo_id.in_(visible_repo_ids),
                    PullRequest.state == "open",
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )

    if not prs:
        return []

    pr_ids = [pr.id for pr in prs]

    # Fetch stack memberships for these PRs
    memberships = (
        (
            await session.execute(
                select(PRStackMembership).where(PRStackMembership.pull_request_id.in_(pr_ids))
            )
        )
        .scalars()
        .all()
    )

    # Fetch relevant stacks
    stack_ids = {m.stack_id for m in memberships}
    stacks = []
    if stack_ids:
        stacks = (
            (await session.execute(select(PRStack).where(PRStack.id.in_(stack_ids))))
            .scalars()
            .all()
        )

    # Build stack_id map for _pr_to_summary
    stack_map = {m.pull_request_id: m.stack_id for m in memberships}

    # Compute scores
    scored: list[dict] = []
    for pr in prs:
        ci_status = _compute_ci_status(pr.check_runs)
        review_state = _compute_review_state(pr.reviews)
        rebased = _rebased_since_approval(pr)
        total_lines = pr.additions + pr.deletions

        score, breakdown = compute_priority_score(
            review_state=review_state,
            ci_status=ci_status,
            total_lines=total_lines,
            mergeable_state=pr.mergeable_state,
            created_at=pr.created_at,
            rebased_since_approval=rebased,
            draft=pr.draft,
        )

        scored.append(
            {
                "pr_id": pr.id,
                "pr": pr,
                "repo_full_name": pr.repo.full_name,
                "repo_id": pr.repo_id,
                "score": score,
                "breakdown": breakdown,
                "stack_id": stack_map.get(pr.id),
                "stack_name": None,
                "blocked_by_pr_id": None,
            }
        )

    # Partition into 3 tiers by manual_priority
    high = [e for e in scored if e["pr"].manual_priority == "high"]
    low = [e for e in scored if e["pr"].manual_priority == "low"]
    normal = [e for e in scored if e["pr"].manual_priority not in ("high", "low")]

    # Build merge order independently per tier, then concatenate
    ordered: list[tuple[str, dict]] = []
    for tier_name, tier_entries in [("high", high), ("normal", normal), ("low", low)]:
        tier_ordered = _build_merge_order(tier_entries, memberships, stacks)
        for entry in tier_ordered:
            ordered.append((tier_name, entry))

    # Convert to response with global merge_position
    result: list[PrioritizedPROut] = []
    for position, (tier_name, entry) in enumerate(ordered, start=1):
        pr = entry["pr"]
        summary = _pr_to_summary(pr, entry.get("stack_id"))

        result.append(
            PrioritizedPROut(
                pr=summary,
                repo_full_name=entry["repo_full_name"],
                repo_id=entry["repo_id"],
                priority_score=entry["score"],
                priority_breakdown=entry["breakdown"],
                merge_position=position,
                blocked_by_pr_id=entry.get("blocked_by_pr_id"),
                stack_id=entry.get("stack_id"),
                stack_name=entry.get("stack_name"),
                priority_tier=tier_name,
            )
        )

    return result
