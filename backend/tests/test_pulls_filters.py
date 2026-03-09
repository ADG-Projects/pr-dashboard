"""Tests for pull request filtering — include_merged_days and state-based queries."""

import time
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.auth import _sign
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


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession):
    """Create user, account, space, repo, tracker, and sample PRs."""
    user = User(github_id=99, login="filteruser", name="Filter User")
    db_session.add(user)
    await db_session.flush()

    account = GitHubAccount(
        user_id=user.id,
        github_id=99,
        login="filteruser",
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

    tracker = RepoTracker(
        user_id=user.id,
        repo_id=repo.id,
        space_id=space.id,
        visibility="shared",
    )
    db_session.add(tracker)
    await db_session.flush()

    now = datetime.now(UTC)

    # Open PR — no reviews, not draft
    open_pr = PullRequest(
        repo_id=repo.id,
        number=1,
        title="Open PR",
        state="open",
        draft=False,
        head_ref="feature-1",
        base_ref="main",
        author="alice",
        html_url="https://github.com/testorg/testrepo/pull/1",
        created_at=now,
        updated_at=now,
    )
    # Draft PR
    draft_pr = PullRequest(
        repo_id=repo.id,
        number=2,
        title="Draft PR",
        state="open",
        draft=True,
        head_ref="feature-2",
        base_ref="main",
        author="bob",
        html_url="https://github.com/testorg/testrepo/pull/2",
        created_at=now,
        updated_at=now,
    )
    # Recently merged PR (2 days ago)
    merged_recent = PullRequest(
        repo_id=repo.id,
        number=3,
        title="Recently Merged",
        state="closed",
        draft=False,
        head_ref="feature-3",
        base_ref="main",
        author="alice",
        html_url="https://github.com/testorg/testrepo/pull/3",
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=2),
        merged_at=now - timedelta(days=2),
    )
    # Old merged PR (30 days ago)
    merged_old = PullRequest(
        repo_id=repo.id,
        number=4,
        title="Old Merged",
        state="closed",
        draft=False,
        head_ref="feature-4",
        base_ref="main",
        author="bob",
        html_url="https://github.com/testorg/testrepo/pull/4",
        created_at=now - timedelta(days=60),
        updated_at=now - timedelta(days=30),
        merged_at=now - timedelta(days=30),
    )
    # Closed PR (not merged)
    closed_pr = PullRequest(
        repo_id=repo.id,
        number=5,
        title="Closed Not Merged",
        state="closed",
        draft=False,
        head_ref="feature-5",
        base_ref="main",
        author="alice",
        html_url="https://github.com/testorg/testrepo/pull/5",
        created_at=now - timedelta(days=5),
        updated_at=now - timedelta(days=3),
    )

    db_session.add_all([open_pr, draft_pr, merged_recent, merged_old, closed_pr])
    await db_session.commit()

    return {
        "user": user,
        "repo": repo,
        "prs": {
            "open": open_pr,
            "draft": draft_pr,
            "merged_recent": merged_recent,
            "merged_old": merged_old,
            "closed": closed_pr,
        },
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


# ── Default behavior: only open PRs ─────────────────


@pytest.mark.asyncio
async def test_list_pulls_default_returns_only_open(authed_client, setup):
    """Without include_merged_days, only open PRs are returned."""
    repo = setup["repo"]
    resp = await authed_client.get(f"/api/repos/{repo.id}/pulls")
    assert resp.status_code == 200
    data = resp.json()
    numbers = {pr["number"] for pr in data}
    # Should include open (#1) and draft (#2), not merged or closed
    assert numbers == {1, 2}


# ── include_merged_days ──────────────────────────────


@pytest.mark.asyncio
async def test_include_merged_days_returns_recent_merged(authed_client, setup):
    """With include_merged_days=7, recently merged PRs are included."""
    repo = setup["repo"]
    resp = await authed_client.get(
        f"/api/repos/{repo.id}/pulls", params={"include_merged_days": "7"}
    )
    assert resp.status_code == 200
    data = resp.json()
    numbers = {pr["number"] for pr in data}
    # Open PRs (#1, #2) + recently merged (#3, within 7 days)
    # NOT old merged (#4, 30 days ago) or closed-not-merged (#5)
    assert numbers == {1, 2, 3}


@pytest.mark.asyncio
async def test_include_merged_days_wider_window(authed_client, setup):
    """With include_merged_days=60, both merged PRs are included."""
    repo = setup["repo"]
    resp = await authed_client.get(
        f"/api/repos/{repo.id}/pulls", params={"include_merged_days": "60"}
    )
    assert resp.status_code == 200
    data = resp.json()
    numbers = {pr["number"] for pr in data}
    # Open PRs + both merged PRs (still not closed-not-merged #5)
    assert numbers == {1, 2, 3, 4}


@pytest.mark.asyncio
async def test_include_merged_days_zero_returns_only_open(authed_client, setup):
    """With include_merged_days=0, no merged PRs are included (cutoff = now)."""
    repo = setup["repo"]
    resp = await authed_client.get(
        f"/api/repos/{repo.id}/pulls", params={"include_merged_days": "0"}
    )
    assert resp.status_code == 200
    data = resp.json()
    numbers = {pr["number"] for pr in data}
    assert numbers == {1, 2}


# ── merged_at field in response ──────────────────────


@pytest.mark.asyncio
async def test_merged_at_field_present(authed_client, setup):
    """PRSummary includes merged_at for merged PRs, null for open ones."""
    repo = setup["repo"]
    resp = await authed_client.get(
        f"/api/repos/{repo.id}/pulls", params={"include_merged_days": "7"}
    )
    data = resp.json()
    by_number = {pr["number"]: pr for pr in data}

    assert by_number[1]["merged_at"] is None
    assert by_number[2]["merged_at"] is None
    assert by_number[3]["merged_at"] is not None


# ── Combined filters ─────────────────────────────────


@pytest.mark.asyncio
async def test_author_filter_with_merged(authed_client, setup):
    """Author filter works together with include_merged_days."""
    repo = setup["repo"]
    resp = await authed_client.get(
        f"/api/repos/{repo.id}/pulls",
        params={"include_merged_days": "7", "author": "alice"},
    )
    data = resp.json()
    numbers = {pr["number"] for pr in data}
    # alice's open PR (#1) + alice's recently merged (#3)
    assert numbers == {1, 3}


@pytest.mark.asyncio
async def test_draft_filter(authed_client, setup):
    """Draft filter still works."""
    repo = setup["repo"]
    resp = await authed_client.get(f"/api/repos/{repo.id}/pulls", params={"draft": "true"})
    data = resp.json()
    numbers = {pr["number"] for pr in data}
    assert numbers == {2}
