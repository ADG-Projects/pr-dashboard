"""Tests for PR manual priority — endpoint, queue partitioning, and sync label reading."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.auth import _sign
from src.api.prioritize import _build_merge_order, compute_priority_score
from src.config.settings import settings
from src.db.engine import get_session
from src.main import app
from src.models.tables import (
    GitHubAccount,
    PullRequest,
    RepoTracker,
    Space,
    TrackedRepo,
    User,
)
from src.services.crypto import encrypt_token


def _make_github_cookie(user_id: int) -> str:
    expires = int(time.time()) + settings.session_max_age_seconds
    return _sign(f"{user_id}:{expires}")


def _make_pr(
    repo_id: int,
    number: int,
    title: str = "Test PR",
    manual_priority: str | None = None,
    created_days_ago: int = 3,
    draft: bool = False,
) -> PullRequest:
    now = datetime.now(UTC)
    return PullRequest(
        repo_id=repo_id,
        number=number,
        title=title,
        state="open",
        draft=draft,
        head_ref=f"feature-{number}",
        base_ref="main",
        author="alice",
        html_url=f"https://github.com/testorg/testrepo/pull/{number}",
        created_at=now - timedelta(days=created_days_ago),
        updated_at=now,
        manual_priority=manual_priority,
    )


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession):
    """Create user, account, space, repo, tracker, and sample PRs with priorities."""
    user = User(github_id=200, login="priouser", name="Prio User")
    db_session.add(user)
    await db_session.flush()

    account = GitHubAccount(
        user_id=user.id,
        github_id=200,
        login="priouser",
        encrypted_token=encrypt_token("fake-token"),
        base_url="https://api.github.com",
    )
    db_session.add(account)
    await db_session.flush()

    space = Space(
        slug="testorg",
        name="testorg",
        space_type="org",
        github_account_id=account.id,
        user_id=user.id,
        is_active=True,
    )
    db_session.add(space)
    await db_session.flush()

    repo = TrackedRepo(
        owner="testorg", name="testrepo", full_name="testorg/testrepo", is_active=True
    )
    db_session.add(repo)
    await db_session.flush()

    tracker = RepoTracker(user_id=user.id, repo_id=repo.id, space_id=space.id, visibility="shared")
    db_session.add(tracker)
    await db_session.flush()

    # PRs with different priorities
    pr_high = _make_pr(repo.id, 1, "High prio PR", manual_priority="high")
    pr_normal = _make_pr(repo.id, 2, "Normal PR", manual_priority=None)
    pr_low = _make_pr(repo.id, 3, "Low prio PR", manual_priority="low")
    pr_normal2 = _make_pr(repo.id, 4, "Another normal PR", manual_priority=None)

    db_session.add_all([pr_high, pr_normal, pr_low, pr_normal2])
    await db_session.commit()

    return {
        "user": user,
        "repo": repo,
        "prs": {"high": pr_high, "normal": pr_normal, "low": pr_low, "normal2": pr_normal2},
    }


@pytest_asyncio.fixture
async def authed_client(async_engine, setup) -> AsyncClient:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    user = setup["user"]
    cookie = _make_github_cookie(user.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"github_user": cookie},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ── PATCH priority endpoint ──────────────────────────


@pytest.mark.asyncio
async def test_set_priority_high(authed_client, setup):
    """Setting priority to high updates the PR and syncs to GitHub."""
    repo = setup["repo"]
    pr = setup["prs"]["normal"]

    with (
        patch(
            "src.api.pulls._get_github_client_for_user",
            new_callable=AsyncMock,
        ) as mock_get_gh,
    ):
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/priority",
            json={"priority": "high"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["manual_priority"] == "high"
    # Verify GitHub label sync was called
    mock_gh.ensure_label.assert_called_once_with(
        "testorg", "testrepo", "priority:high", "D73A4A", "High priority — review/merge first"
    )
    mock_gh.add_labels.assert_called_once_with("testorg", "testrepo", pr.number, ["priority:high"])
    mock_gh.close.assert_called_once()


@pytest.mark.asyncio
async def test_set_priority_low(authed_client, setup):
    """Setting priority to low works."""
    repo = setup["repo"]
    pr = setup["prs"]["normal2"]

    with patch(
        "src.api.pulls._get_github_client_for_user",
        new_callable=AsyncMock,
    ) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/priority",
            json={"priority": "low"},
        )

    assert resp.status_code == 200
    assert resp.json()["manual_priority"] == "low"
    mock_gh.ensure_label.assert_called_once_with(
        "testorg", "testrepo", "priority:low", "6B7280", "Low priority — review/merge last"
    )


@pytest.mark.asyncio
async def test_clear_priority(authed_client, setup):
    """Setting priority to null removes the old label."""
    repo = setup["repo"]
    pr = setup["prs"]["high"]  # already high

    with patch(
        "src.api.pulls._get_github_client_for_user",
        new_callable=AsyncMock,
    ) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/priority",
            json={"priority": None},
        )

    assert resp.status_code == 200
    assert resp.json()["manual_priority"] is None
    mock_gh.remove_label.assert_called_once_with("testorg", "testrepo", pr.number, "priority:high")
    mock_gh.add_labels.assert_not_called()


@pytest.mark.asyncio
async def test_change_priority_high_to_low(authed_client, setup):
    """Changing from high to low removes old label and adds new one."""
    repo = setup["repo"]
    pr = setup["prs"]["high"]

    with patch(
        "src.api.pulls._get_github_client_for_user",
        new_callable=AsyncMock,
    ) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/priority",
            json={"priority": "low"},
        )

    assert resp.status_code == 200
    assert resp.json()["manual_priority"] == "low"
    mock_gh.remove_label.assert_called_once_with("testorg", "testrepo", pr.number, "priority:high")
    mock_gh.ensure_label.assert_called_once()
    mock_gh.add_labels.assert_called_once_with("testorg", "testrepo", pr.number, ["priority:low"])


@pytest.mark.asyncio
async def test_invalid_priority_rejected(authed_client, setup):
    """Invalid priority values are rejected with 422."""
    repo = setup["repo"]
    pr = setup["prs"]["normal"]

    resp = await authed_client.patch(
        f"/api/repos/{repo.id}/pulls/{pr.number}/priority",
        json={"priority": "critical"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_priority_404_nonexistent_pr(authed_client, setup):
    """Setting priority on a nonexistent PR returns 404."""
    repo = setup["repo"]

    with patch(
        "src.api.pulls._get_github_client_for_user",
        new_callable=AsyncMock,
    ):
        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/9999/priority",
            json={"priority": "high"},
        )
    assert resp.status_code == 404


# ── Prioritize queue partitioning ─────────────────────


@pytest.mark.asyncio
async def test_prioritized_queue_tier_ordering(authed_client, setup):
    """High-priority PRs appear before normal, which appear before low."""
    resp = await authed_client.get("/api/pulls/prioritized")
    assert resp.status_code == 200
    data = resp.json()

    tiers = [item["priority_tier"] for item in data]
    # All high items should come before normal, which come before low
    high_positions = [i for i, t in enumerate(tiers) if t == "high"]
    normal_positions = [i for i, t in enumerate(tiers) if t == "normal"]
    low_positions = [i for i, t in enumerate(tiers) if t == "low"]

    if high_positions and normal_positions:
        assert max(high_positions) < min(normal_positions)
    if normal_positions and low_positions:
        assert max(normal_positions) < min(low_positions)
    if high_positions and low_positions:
        assert max(high_positions) < min(low_positions)


@pytest.mark.asyncio
async def test_prioritized_queue_merge_positions_are_global(authed_client, setup):
    """merge_position is 1..N across all tiers."""
    resp = await authed_client.get("/api/pulls/prioritized")
    assert resp.status_code == 200
    data = resp.json()

    positions = [item["merge_position"] for item in data]
    assert positions == list(range(1, len(data) + 1))


@pytest.mark.asyncio
async def test_prioritized_queue_includes_priority_tier(authed_client, setup):
    """Each item in the response has a priority_tier field."""
    resp = await authed_client.get("/api/pulls/prioritized")
    data = resp.json()

    for item in data:
        assert item["priority_tier"] in ("high", "normal", "low")


# ── _build_merge_order unit tests ─────────────────────


class TestBuildMergeOrder:
    def test_standalone_prs_sorted_by_score(self):
        """Standalone PRs (no stacks) are sorted by score descending."""
        scored = [
            {
                "pr_id": 1,
                "score": 50,
                "stack_id": None,
                "stack_name": None,
                "blocked_by_pr_id": None,
            },
            {
                "pr_id": 2,
                "score": 80,
                "stack_id": None,
                "stack_name": None,
                "blocked_by_pr_id": None,
            },
            {
                "pr_id": 3,
                "score": 30,
                "stack_id": None,
                "stack_name": None,
                "blocked_by_pr_id": None,
            },
        ]
        result = _build_merge_order(scored, [], [])
        ids = [e["pr_id"] for e in result]
        assert ids == [2, 1, 3]

    def test_empty_input(self):
        """Empty input returns empty output."""
        assert _build_merge_order([], [], []) == []


# ── Sync: reading priority labels ─────────────────────


@pytest.mark.asyncio
async def test_sync_reads_priority_high_label(db_session: AsyncSession):
    """_upsert_pr sets manual_priority='high' when priority:high label is present."""
    from src.services.sync_service import SyncService

    repo = TrackedRepo(owner="org", name="repo", full_name="org/repo", is_active=True)
    db_session.add(repo)
    await db_session.flush()

    svc = SyncService()
    gh_pr = {
        "number": 10,
        "title": "Labeled PR",
        "state": "open",
        "draft": False,
        "head": {"ref": "feat-10", "sha": "abc123"},
        "base": {"ref": "main"},
        "user": {"login": "alice"},
        "html_url": "https://github.com/org/repo/pull/10",
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-09T00:00:00Z",
        "merged_at": None,
        "requested_reviewers": [],
        "assignees": [],
        "labels": [{"name": "priority:high"}, {"name": "bug"}],
    }

    pr = await svc._upsert_pr(db_session, repo.id, gh_pr)
    assert pr.manual_priority == "high"


@pytest.mark.asyncio
async def test_sync_reads_priority_low_label(db_session: AsyncSession):
    """_upsert_pr sets manual_priority='low' when priority:low label is present."""
    from src.services.sync_service import SyncService

    repo = TrackedRepo(owner="org", name="repo", full_name="org/repo2", is_active=True)
    db_session.add(repo)
    await db_session.flush()

    svc = SyncService()
    gh_pr = {
        "number": 11,
        "title": "Low prio PR",
        "state": "open",
        "draft": False,
        "head": {"ref": "feat-11", "sha": "def456"},
        "base": {"ref": "main"},
        "user": {"login": "bob"},
        "html_url": "https://github.com/org/repo2/pull/11",
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-09T00:00:00Z",
        "merged_at": None,
        "requested_reviewers": [],
        "assignees": [],
        "labels": [{"name": "priority:low"}],
    }

    pr = await svc._upsert_pr(db_session, repo.id, gh_pr)
    assert pr.manual_priority == "low"


@pytest.mark.asyncio
async def test_sync_no_priority_label_sets_null(db_session: AsyncSession):
    """_upsert_pr sets manual_priority=None when no priority label is present."""
    from src.services.sync_service import SyncService

    repo = TrackedRepo(owner="org", name="repo", full_name="org/repo3", is_active=True)
    db_session.add(repo)
    await db_session.flush()

    svc = SyncService()
    gh_pr = {
        "number": 12,
        "title": "Normal PR",
        "state": "open",
        "draft": False,
        "head": {"ref": "feat-12", "sha": "ghi789"},
        "base": {"ref": "main"},
        "user": {"login": "alice"},
        "html_url": "https://github.com/org/repo3/pull/12",
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-09T00:00:00Z",
        "merged_at": None,
        "requested_reviewers": [],
        "assignees": [],
        "labels": [{"name": "enhancement"}],
    }

    pr = await svc._upsert_pr(db_session, repo.id, gh_pr)
    assert pr.manual_priority is None


@pytest.mark.asyncio
async def test_sync_updates_priority_on_existing_pr(db_session: AsyncSession):
    """_upsert_pr updates manual_priority when re-syncing an existing PR."""
    from src.services.sync_service import SyncService

    repo = TrackedRepo(owner="org", name="repo", full_name="org/repo4", is_active=True)
    db_session.add(repo)
    await db_session.flush()

    svc = SyncService()
    base_pr = {
        "number": 13,
        "title": "Evolving PR",
        "state": "open",
        "draft": False,
        "head": {"ref": "feat-13", "sha": "jkl012"},
        "base": {"ref": "main"},
        "user": {"login": "alice"},
        "html_url": "https://github.com/org/repo4/pull/13",
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-09T00:00:00Z",
        "merged_at": None,
        "requested_reviewers": [],
        "assignees": [],
        "labels": [],
    }

    # First sync: no priority label
    pr = await svc._upsert_pr(db_session, repo.id, base_pr)
    assert pr.manual_priority is None

    # Second sync: priority:high label added
    base_pr["labels"] = [{"name": "priority:high"}]
    pr = await svc._upsert_pr(db_session, repo.id, base_pr)
    assert pr.manual_priority == "high"

    # Third sync: label removed
    base_pr["labels"] = []
    pr = await svc._upsert_pr(db_session, repo.id, base_pr)
    assert pr.manual_priority is None


# ── compute_priority_score unit test ──────────────────


class TestComputePriorityScore:
    def test_perfect_score(self):
        """Approved + CI pass + small + clean + old + rebased = max score."""
        score, breakdown = compute_priority_score(
            review_state="approved",
            ci_status="success",
            total_lines=10,
            mergeable_state="clean",
            created_at=datetime.now(UTC) - timedelta(days=14),
            rebased_since_approval=True,
            draft=False,
        )
        assert score == 100
        assert breakdown.review == 35
        assert breakdown.ci == 25
        assert breakdown.size == 10
        assert breakdown.mergeable == 15
        assert breakdown.age == 10
        assert breakdown.rebase == 5

    def test_draft_penalty(self):
        """Draft PRs get a -30 penalty, floored at 0."""
        score, breakdown = compute_priority_score(
            review_state="none",
            ci_status="unknown",
            total_lines=10,
            mergeable_state=None,
            created_at=datetime.now(UTC),
            rebased_since_approval=False,
            draft=True,
        )
        assert breakdown.draft_penalty == -30
        assert score == 0  # 15 + 5 + 10 + 0 + 0 + 0 - 30 = 0

    def test_large_pr_low_size_score(self):
        """Very large PRs get 0 size points."""
        _, breakdown = compute_priority_score(
            review_state="none",
            ci_status="unknown",
            total_lines=2000,
            mergeable_state=None,
            created_at=datetime.now(UTC),
            rebased_since_approval=False,
            draft=False,
        )
        assert breakdown.size == 0


# ── manual_priority in PR list response ───────────────


@pytest.mark.asyncio
async def test_pulls_list_includes_manual_priority(authed_client, setup):
    """The /pulls endpoint includes manual_priority in the response."""
    repo = setup["repo"]
    resp = await authed_client.get(f"/api/repos/{repo.id}/pulls")
    assert resp.status_code == 200
    data = resp.json()

    by_number = {pr["number"]: pr for pr in data}
    assert by_number[1]["manual_priority"] == "high"
    assert by_number[2]["manual_priority"] is None
    assert by_number[3]["manual_priority"] == "low"
    assert by_number[4]["manual_priority"] is None
