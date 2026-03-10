"""API routes for pull requests."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.api.schemas import (
    AssigneeUpdate,
    CheckRunOut,
    PRDetail,
    PriorityUpdate,
    PRSummary,
    ReviewerUpdate,
    ReviewOut,
)
from src.db.engine import get_session
from src.models.tables import (
    CheckRun,
    GitHubAccount,
    PRStackMembership,
    PullRequest,
    RepoTracker,
    Review,
    Space,
    TrackedRepo,
    User,
)
from src.services.crypto import decrypt_token
from src.services.events import broadcast_event
from src.services.github_client import GitHubClient

router = APIRouter(prefix="/api/repos/{repo_id}", tags=["pulls"])


async def _get_github_client_for_pr(
    session: AsyncSession, repo_id: int
) -> tuple[GitHubClient, TrackedRepo]:
    """Resolve the GitHub token for a tracked repo via its trackers and return a client + repo."""
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    # Try each tracker's space → github_account for a valid token
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

    for tracker in trackers:
        if tracker.space and tracker.space.github_account:
            account = tracker.space.github_account
            if account.encrypted_token:
                token = decrypt_token(account.encrypted_token)
                if token:
                    return GitHubClient(token=token, base_url=account.base_url), repo

    raise HTTPException(status_code=400, detail="No GitHub token available for this repo")


def _compute_ci_status(checks: list[CheckRun]) -> str:
    """Derive an overall CI status from individual check runs."""
    if not checks:
        return "unknown"
    conclusions = [c.conclusion for c in checks if c.conclusion]
    if any(c == "failure" for c in conclusions):
        return "failure"
    if any(c == "action_required" for c in conclusions):
        return "action_required"
    statuses = [c.status for c in checks]
    if any(s in ("queued", "in_progress") for s in statuses):
        return "pending"
    if all(c == "success" for c in conclusions):
        return "success"
    return "unknown"


def _compute_review_state(reviews: list[Review]) -> str:
    """Derive overall review state from individual reviews."""
    if not reviews:
        return "none"
    # Latest review per reviewer wins
    latest: dict[str, str] = {}
    for r in sorted(reviews, key=lambda x: x.submitted_at):
        latest[r.reviewer] = r.state
    states = set(latest.values())
    has_changes = "CHANGES_REQUESTED" in states
    has_approved = "APPROVED" in states
    if has_changes and has_approved:
        return "mixed"
    if has_changes:
        return "changes_requested"
    if has_approved:
        return "approved"
    if states - {"COMMENTED", "DISMISSED"}:
        return "reviewed"
    return "reviewed" if states else "none"


def _rebased_since_approval(pr: PullRequest) -> bool:
    """Check if HEAD changed (rebase or force-push) after the most recent GitHub approval."""
    if not pr.head_sha or not pr.reviews:
        return False
    latest: dict[str, Review] = {}
    for r in sorted(pr.reviews, key=lambda x: x.submitted_at):
        latest[r.reviewer] = r
    approved = [r for r in latest.values() if r.state == "APPROVED"]
    if not approved:
        return False
    newest_approval = max(approved, key=lambda r: r.submitted_at)
    return newest_approval.commit_id is not None and newest_approval.commit_id != pr.head_sha


def _commenters_without_review(pr: PullRequest) -> list[str]:
    """Return commenters who haven't submitted a formal review or been requested as reviewers."""
    commenters = set(pr.commenters or [])
    if not commenters:
        return []
    # Formal reviewers (anyone who submitted a review)
    formal_reviewers = {r.reviewer for r in (pr.reviews or [])}
    # Requested reviewers
    requested = {r.get("login") for r in (pr.github_requested_reviewers or []) if r.get("login")}
    return sorted(commenters - formal_reviewers - requested)


_REVIEWER_STATE_ORDER = {
    "changes_requested": 0,
    "pending": 1,
    "commented": 2,
    "reviewed": 2,
    "approved": 3,
}


def _compute_all_reviewers(pr: PullRequest) -> list[dict]:
    """Merge requested reviewers and review authors into a sorted list.

    Sort order: changes_requested, pending, commented/reviewed, approved.
    Alphabetical within each group.
    """
    # Determine latest review state per reviewer
    latest_state: dict[str, str] = {}
    for r in sorted(pr.reviews or [], key=lambda x: x.submitted_at):
        latest_state[r.reviewer] = r.state

    entries: list[dict] = []
    seen: set[str] = set()

    # Requested reviewers who haven't submitted a review yet are "pending"
    for r in pr.github_requested_reviewers or []:
        login = r.get("login")
        if not login:
            continue
        seen.add(login)
        raw = latest_state.get(login)
        state = "pending" if raw is None else raw.lower().replace(" ", "_")
        entries.append({"login": login, "avatar_url": r.get("avatar_url"), "review_state": state})

    # Reviewers from reviews who aren't in the requested list
    for login, raw_state in latest_state.items():
        if login in seen:
            continue
        seen.add(login)
        state = raw_state.lower().replace(" ", "_")
        entries.append({"login": login, "avatar_url": None, "review_state": state})

    entries.sort(key=lambda e: (_REVIEWER_STATE_ORDER.get(e["review_state"], 2), e["login"]))
    return entries


def _pr_to_summary(pr: PullRequest, stack_id: int | None = None) -> PRSummary:
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
        stack_id=stack_id,
        assignee_id=pr.assignee_id,
        assignee_name=(pr.assignee.name or pr.assignee.login) if pr.assignee else None,
        github_requested_reviewers=pr.github_requested_reviewers or [],
        all_reviewers=_compute_all_reviewers(pr),
        rebased_since_approval=_rebased_since_approval(pr),
        merged_at=pr.merged_at,
        manual_priority=pr.manual_priority,
        commenters_without_review=_commenters_without_review(pr),
    )


@router.get("/pulls", response_model=list[PRSummary])
async def list_pulls(
    repo_id: int,
    author: str | None = Query(None),
    ci_status: str | None = Query(None),
    draft: bool | None = Query(None),
    include_merged_days: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[PRSummary]:
    """List PRs for a repo with optional filters.

    Includes merged PRs when include_merged_days is set.
    """
    repo = await session.get(TrackedRepo, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    state_condition = PullRequest.state == "open"
    if include_merged_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=include_merged_days)
        state_condition = or_(
            PullRequest.state == "open",
            PullRequest.merged_at >= cutoff,
        )

    stmt = (
        select(PullRequest)
        .options(
            selectinload(PullRequest.check_runs),
            selectinload(PullRequest.reviews),
            joinedload(PullRequest.assignee),
        )
        .where(PullRequest.repo_id == repo_id, state_condition)
        .order_by(PullRequest.updated_at.desc())
    )
    if author:
        stmt = stmt.where(PullRequest.author == author)
    if draft is not None:
        stmt = stmt.where(PullRequest.draft == draft)

    prs = (await session.execute(stmt)).scalars().all()

    # Build stack_id map
    memberships = (
        (
            await session.execute(
                select(PRStackMembership).where(
                    PRStackMembership.pull_request_id.in_([pr.id for pr in prs])
                )
            )
        )
        .scalars()
        .all()
    )
    stack_map = {m.pull_request_id: m.stack_id for m in memberships}

    summaries = [_pr_to_summary(pr, stack_map.get(pr.id)) for pr in prs]

    # Post-filter by computed ci_status if requested
    if ci_status:
        summaries = [s for s in summaries if s.ci_status == ci_status]

    return summaries


@router.get("/pulls/{number}", response_model=PRDetail)
async def get_pull(
    repo_id: int, number: int, session: AsyncSession = Depends(get_session)
) -> PRDetail:
    """Get full PR detail with checks and reviews."""
    result = await session.execute(
        select(PullRequest)
        .options(
            selectinload(PullRequest.check_runs),
            selectinload(PullRequest.reviews),
            joinedload(PullRequest.assignee),
        )
        .where(PullRequest.repo_id == repo_id, PullRequest.number == number)
    )
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")

    return PRDetail(
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
        assignee_id=pr.assignee_id,
        assignee_name=(pr.assignee.name or pr.assignee.login) if pr.assignee else None,
        github_requested_reviewers=pr.github_requested_reviewers or [],
        all_reviewers=_compute_all_reviewers(pr),
        rebased_since_approval=_rebased_since_approval(pr),
        manual_priority=pr.manual_priority,
        commenters_without_review=_commenters_without_review(pr),
        check_runs=[
            CheckRunOut(
                id=c.id,
                name=c.name,
                status=c.status,
                conclusion=c.conclusion,
                details_url=c.details_url,
            )
            for c in pr.check_runs
        ],
        reviews=[
            ReviewOut(
                id=r.id,
                reviewer=r.reviewer,
                state=r.state,
                submitted_at=r.submitted_at,
            )
            for r in pr.reviews
        ],
    )


@router.patch("/pulls/{number}/assignee", response_model=PRSummary)
async def update_assignee(
    repo_id: int,
    number: int,
    body: AssigneeUpdate,
    session: AsyncSession = Depends(get_session),
) -> PRSummary:
    """Set or clear the assignee for a PR — writes to GitHub first."""
    result = await session.execute(
        select(PullRequest)
        .options(
            selectinload(PullRequest.check_runs),
            selectinload(PullRequest.reviews),
            joinedload(PullRequest.assignee),
        )
        .where(PullRequest.repo_id == repo_id, PullRequest.number == number)
    )
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")

    # Resolve login for GitHub API
    logins: list[str] = []
    if body.assignee_id is not None:
        user = await session.get(User, body.assignee_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        logins = [user.login]

    # Write to GitHub first
    gh, repo = await _get_github_client_for_pr(session, repo_id)
    try:
        await gh.set_assignees(repo.owner, repo.name, number, logins)
    except Exception as exc:
        logger.warning(f"Failed to set assignees on GitHub for PR #{number}: {exc}")
        raise HTTPException(status_code=502, detail="GitHub API error") from exc
    finally:
        await gh.close()

    pr.assignee_id = body.assignee_id
    await session.commit()
    await session.refresh(pr, attribute_names=["assignee"])

    membership = (
        await session.execute(
            select(PRStackMembership).where(PRStackMembership.pull_request_id == pr.id)
        )
    ).scalar_one_or_none()

    await broadcast_event(
        "assignee_update",
        {"repo_id": repo_id, "number": number, "assignee_id": body.assignee_id},
    )

    return _pr_to_summary(pr, membership.stack_id if membership else None)


async def _resolve_login_for_repo(
    session: AsyncSession, user: User, repo: TrackedRepo
) -> tuple[str, str | None]:
    """Resolve the correct GitHub login and avatar for a user in the context of a repo's space.

    Looks at the repo's trackers to find a space slug, then checks if the user
    has a GitHubAccount linked to a space with the same slug.
    """
    # Find the repo owner's space slug from any tracker
    tracker = (
        await session.execute(
            select(RepoTracker)
            .options(selectinload(RepoTracker.space))
            .where(RepoTracker.repo_id == repo.id, RepoTracker.space_id.isnot(None))
            .limit(1)
        )
    ).scalar_one_or_none()

    if tracker and tracker.space:
        result = await session.execute(
            select(GitHubAccount)
            .join(Space, Space.github_account_id == GitHubAccount.id)
            .where(
                GitHubAccount.user_id == user.id,
                Space.slug == tracker.space.slug,
            )
        )
        account = result.scalar_one_or_none()
        if account:
            return account.login, account.avatar_url
    return user.login, user.avatar_url


@router.patch("/pulls/{number}/reviewers")
async def update_reviewers(
    repo_id: int,
    number: int,
    body: ReviewerUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[dict]]:
    """Add or remove requested reviewers — writes to GitHub first."""
    result = await session.execute(
        select(PullRequest).where(PullRequest.repo_id == repo_id, PullRequest.number == number)
    )
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")

    gh, repo = await _get_github_client_for_pr(session, repo_id)

    # Resolve user_ids → GitHub logins + avatars in the repo's context
    add_entries: list[tuple[str, str | None]] = []
    for uid in body.add_user_ids:
        user = await session.get(User, uid)
        if not user:
            raise HTTPException(status_code=404, detail=f"User {uid} not found")
        add_entries.append(await _resolve_login_for_repo(session, user, repo))
    add_logins = [login for login, _ in add_entries]

    try:
        if add_logins:
            await gh.request_reviewers(repo.owner, repo.name, number, add_logins)
        if body.remove_logins:
            await gh.remove_reviewers(repo.owner, repo.name, number, body.remove_logins)
    except Exception as exc:
        logger.warning(f"Failed to update reviewers on GitHub for PR #{number}: {exc}")
        raise HTTPException(status_code=502, detail="GitHub API error") from exc
    finally:
        await gh.close()

    # Update local JSONB: add new, remove old
    current = list(pr.github_requested_reviewers or [])
    remove_set = set(body.remove_logins)
    current = [r for r in current if r.get("login") not in remove_set]
    existing_logins = {r.get("login") for r in current}
    for login, avatar_url in add_entries:
        if login not in existing_logins:
            current.append({"login": login, "avatar_url": avatar_url, "github_id": None})
    pr.github_requested_reviewers = current
    await session.commit()

    await broadcast_event(
        "reviewers_update",
        {"repo_id": repo_id, "number": number},
    )

    return {"github_requested_reviewers": pr.github_requested_reviewers or []}


@router.patch("/pulls/{number}/priority", response_model=PRSummary)
async def update_priority(
    repo_id: int,
    number: int,
    body: PriorityUpdate,
    session: AsyncSession = Depends(get_session),
) -> PRSummary:
    """Set or clear the manual priority for a PR — syncs labels to GitHub."""
    if body.priority is not None and body.priority not in ("high", "low"):
        raise HTTPException(status_code=422, detail="priority must be 'high', 'low', or null")

    result = await session.execute(
        select(PullRequest)
        .options(
            selectinload(PullRequest.check_runs),
            selectinload(PullRequest.reviews),
            joinedload(PullRequest.assignee),
        )
        .where(PullRequest.repo_id == repo_id, PullRequest.number == number)
    )
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")

    old_priority = pr.manual_priority

    # Sync labels to GitHub
    priority_label_colors = {"high": "D73A4A", "low": "6B7280"}
    priority_label_descriptions = {
        "high": "High priority — review/merge first",
        "low": "Low priority — review/merge last",
    }

    gh, repo = await _get_github_client_for_pr(session, repo_id)
    try:
        # Remove old priority label if it changed
        if old_priority and old_priority != body.priority:
            await gh.remove_label(repo.owner, repo.name, number, f"priority:{old_priority}")

        # Ensure the label exists with the right color, then add it
        if body.priority:
            label_name = f"priority:{body.priority}"
            await gh.ensure_label(
                repo.owner,
                repo.name,
                label_name,
                priority_label_colors[body.priority],
                priority_label_descriptions[body.priority],
            )
            await gh.add_labels(repo.owner, repo.name, number, [label_name])
    except Exception as exc:
        logger.warning(f"Failed to sync priority labels on GitHub for PR #{number}: {exc}")
        raise HTTPException(status_code=502, detail="GitHub API error") from exc
    finally:
        await gh.close()

    pr.manual_priority = body.priority
    await session.commit()

    membership = (
        await session.execute(
            select(PRStackMembership).where(PRStackMembership.pull_request_id == pr.id)
        )
    ).scalar_one_or_none()

    await broadcast_event(
        "priority_update",
        {"repo_id": repo_id, "number": number, "priority": body.priority},
    )

    return _pr_to_summary(pr, membership.stack_id if membership else None)
