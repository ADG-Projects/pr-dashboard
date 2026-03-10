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
from src.models.tables import (
    GitHubAccount,
    PRStack,
    PRStackMembership,
    PullRequest,
    RepoTracker,
    Review,
    TrackedRepo,
    User,
)

router = APIRouter(prefix="/api/pulls", tags=["prioritize"])


async def _resolve_user_logins(session: AsyncSession, user_id: int) -> set[str]:
    """Return all GitHub logins for a user (primary login + linked accounts)."""
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        return set()
    logins = {user.login}
    accounts = (
        (await session.execute(select(GitHubAccount).where(GitHubAccount.user_id == user_id)))
        .scalars()
        .all()
    )
    for acct in accounts:
        logins.add(acct.login)
    return logins


def _compute_age_pts(created_at: datetime, max_pts: int, days: int = 7) -> int:
    """Linear age scoring: 0 to max_pts over the given number of days."""
    tz = created_at.tzinfo or UTC
    age_days = (datetime.now(UTC) - created_at.replace(tzinfo=tz)).total_seconds() / 86400
    return min(max_pts, int(age_days * max_pts / days))


def _compute_size_pts(total_lines: int) -> int:
    """Size scoring for review mode (max 15): smaller PRs = quick wins."""
    if total_lines <= 50:
        return 15
    if total_lines <= 200:
        return 12
    if total_lines <= 500:
        return 7
    if total_lines <= 1000:
        return 3
    return 0


def _is_my_review(pr: PullRequest, user_logins: set[str]) -> bool:
    """Check if a PR belongs in my review queue.

    True if:
    - I'm explicitly requested as a reviewer (in github_requested_reviewers), OR
    - I've submitted reviews on this PR but haven't approved it yet
      (commenting removes you from requested reviewers, but you're still a reviewer)
    """
    # Explicitly requested
    if any(r.get("login") in user_logins for r in (pr.github_requested_reviewers or [])):
        return True

    # I've reviewed but not yet approved - still my responsibility
    my_reviews = [r for r in (pr.reviews or []) if r.reviewer in user_logins]
    if not my_reviews:
        return False

    # Find my latest review state
    latest = max(my_reviews, key=lambda r: r.submitted_at)
    # If I already approved, it's not in my queue (unless author rebased, handled by scoring)
    if latest.state == "APPROVED":
        # But if author pushed new commits since my approval, bring it back
        if pr.head_sha and latest.commit_id and latest.commit_id != pr.head_sha:
            return True
        return False

    # I commented or requested changes but PR is still open - keep in queue
    return True


def _compute_ball_in_my_court(
    reviews: list[Review],
    user_logins: set[str],
    head_sha: str | None,
) -> int:
    """Compute "ball in my court" score (max 35) for review mode.

    - Never reviewed this PR → 35 (I'm blocking)
    - I reviewed, author rebased/pushed since → 30 (my turn again)
    - I approved but rebased since my approval → 30 (needs re-review)
    - I reviewed/approved and nothing changed since → 0 (ball in author's court)
    """
    # Find my latest review on this PR
    my_reviews = [r for r in reviews if r.reviewer in user_logins]
    if not my_reviews:
        return 35  # Never reviewed, I'm blocking

    latest = max(my_reviews, key=lambda r: r.submitted_at)

    # If head_sha differs from the commit I last reviewed, author pushed new changes
    if head_sha and latest.commit_id and latest.commit_id != head_sha:
        return 30  # Author pushed after my review, my turn again

    # I reviewed but nothing changed since
    return 0


def compute_review_score(
    reviews: list[Review],
    user_logins: set[str],
    ci_status: str,
    total_lines: int,
    mergeable_state: str | None,
    created_at: datetime,
    draft: bool,
    head_sha: str | None,
) -> tuple[int, PriorityBreakdown]:
    """Scoring for review mode (max 100): "What should I review next?"."""
    # Ball in my court (max 35)
    ball_pts = _compute_ball_in_my_court(reviews, user_logins, head_sha)

    # CI passing (max 20) — no point reviewing if CI is red
    ci_scores = {"success": 20, "pending": 8, "unknown": 4, "failure": 0}
    ci_pts = ci_scores.get(ci_status, 4)

    # Small diff (max 15) — quick wins
    size_pts = _compute_size_pts(total_lines)

    # Age (max 15) — linear over 7 days
    age_pts = _compute_age_pts(created_at, 15)

    # Mergeable (max 10)
    merge_scores = {"clean": 10, "unstable": 5}
    mergeable_pts = merge_scores.get(mergeable_state or "", 0)

    # Not a draft (max 5) — draft penalty
    draft_penalty = -30 if draft else 0

    total = max(0, ball_pts + ci_pts + size_pts + age_pts + mergeable_pts + draft_penalty)

    breakdown = PriorityBreakdown(
        review=ball_pts,
        ci=ci_pts,
        size=size_pts,
        mergeable=mergeable_pts,
        age=age_pts,
        rebase=0,
        draft_penalty=draft_penalty,
    )
    return total, breakdown


def compute_owner_score(
    review_state: str,
    ci_status: str,
    total_lines: int,
    mergeable_state: str | None,
    created_at: datetime,
    draft: bool,
) -> tuple[int, PriorityBreakdown]:
    """Scoring for owner mode (max 100): "What needs my attention on my PRs?"."""
    # Changes requested (max 30) — someone sent it back
    review_pts = 30 if review_state == "changes_requested" else 0

    # CI status inverted (max 25) — fix failures first
    ci_scores = {"failure": 25, "action_required": 20, "pending": 5, "success": 0, "unknown": 5}
    ci_pts = ci_scores.get(ci_status, 5)

    # Conflicts inverted (max 15) — fix conflicts first
    merge_scores = {"clean": 0, "unstable": 8}
    mergeable_pts = merge_scores.get(mergeable_state or "", 15)  # unknown/conflicts = 15

    # Ready to merge (max 15) — approved + CI passing + clean merge
    ready_pts = 0
    is_approved = review_state == "approved"
    is_ci_green = ci_status == "success"
    is_clean = mergeable_state == "clean"
    if is_approved and is_ci_green and is_clean:
        ready_pts = 15
    elif is_approved and is_ci_green:
        ready_pts = 10
    elif is_approved:
        ready_pts = 5

    # Has new feedback (max 5) — reviewed/commented but not approved/changes_requested
    feedback_pts = 5 if review_state == "reviewed" else 0

    # Age (max 10) — linear over 7 days
    age_pts = _compute_age_pts(created_at, 10)

    # Draft penalty (lighter than review mode)
    draft_penalty = -20 if draft else 0

    total = max(
        0, review_pts + ci_pts + mergeable_pts + ready_pts + feedback_pts + age_pts + draft_penalty
    )

    # Reuse PriorityBreakdown fields with owner semantics:
    # review = changes_requested pts, ci = inverted ci, size = ready_to_merge,
    # mergeable = conflicts inverted, rebase = feedback
    breakdown = PriorityBreakdown(
        review=review_pts,
        ci=ci_pts,
        size=ready_pts,
        mergeable=mergeable_pts,
        age=age_pts,
        rebase=feedback_pts,
        draft_penalty=draft_penalty,
    )
    return total, breakdown


def compute_priority_score(
    review_state: str,
    ci_status: str,
    total_lines: int,
    mergeable_state: str | None,
    created_at: datetime,
    rebased_since_approval: bool,
    draft: bool,
) -> tuple[int, PriorityBreakdown]:
    """Legacy scoring function (0-100) for unauthenticated users."""
    # Review readiness (max 35)
    review_scores = {"approved": 35, "reviewed": 15, "none": 15, "changes_requested": 0}
    review_pts = review_scores.get(review_state, 15)

    # CI status (max 25)
    ci_scores = {"success": 25, "pending": 10, "unknown": 5, "failure": 0}
    ci_pts = ci_scores.get(ci_status, 5)

    # Size — inverse, smaller = higher (max 10)
    if total_lines <= 50:
        size_pts = 10
    elif total_lines <= 200:
        size_pts = 8
    elif total_lines <= 500:
        size_pts = 5
    elif total_lines <= 1000:
        size_pts = 2
    else:
        size_pts = 0

    # Mergeable state (max 15)
    merge_scores = {"clean": 15, "unstable": 8}
    mergeable_pts = merge_scores.get(mergeable_state or "", 0)

    # Age — older PRs get higher priority, linear 0→10 over 7 days (max 10)
    age_pts = _compute_age_pts(created_at, 10)

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
    mode: str = Query("review"),
    session: AsyncSession = Depends(get_session),
) -> list[PrioritizedPROut]:
    """Return open PRs ranked by priority score. Optionally scoped to a single repo.

    Modes:
    - "review": PRs where I'm a requested reviewer, scored by "ball in my court" logic
    - "owner": PRs I authored, scored by action-required signals
    - default/unauth: legacy scoring, no filtering
    """
    user_id = get_github_user_id(request)

    # Resolve user logins for filtering and scoring
    user_logins: set[str] = set()
    if user_id and mode in ("review", "owner"):
        user_logins = await _resolve_user_logins(session, user_id)

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

    # Mode-based filtering (only when authenticated with logins)
    if user_logins:
        if mode == "review":
            prs = [pr for pr in prs if _is_my_review(pr, user_logins)]
        elif mode == "owner":
            prs = [pr for pr in prs if pr.author in user_logins]

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
        total_lines = pr.additions + pr.deletions

        if user_logins and mode == "review":
            score, breakdown = compute_review_score(
                reviews=pr.reviews,
                user_logins=user_logins,
                ci_status=ci_status,
                total_lines=total_lines,
                mergeable_state=pr.mergeable_state,
                created_at=pr.created_at,
                draft=pr.draft,
                head_sha=pr.head_sha,
            )
        elif user_logins and mode == "owner":
            score, breakdown = compute_owner_score(
                review_state=review_state,
                ci_status=ci_status,
                total_lines=total_lines,
                mergeable_state=pr.mergeable_state,
                created_at=pr.created_at,
                draft=pr.draft,
            )
        else:
            rebased = _rebased_since_approval(pr)
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
    active_mode = mode if user_logins else "default"
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
                mode=active_mode,
            )
        )

    return result
