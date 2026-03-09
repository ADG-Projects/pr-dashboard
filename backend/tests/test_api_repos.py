"""Tests for the repos API endpoints."""

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

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
    """Create a signed github_user cookie for test auth."""
    expires = int(time.time()) + settings.session_max_age_seconds
    return _sign(f"{user_id}:{expires}")


@pytest_asyncio.fixture
async def space_with_account(db_session: AsyncSession):
    """Create a user, GitHub account, and space for repo tests."""
    user = User(github_id=1, login="testuser", name="Test User")
    db_session.add(user)
    await db_session.flush()

    account = GitHubAccount(
        user_id=user.id,
        github_id=1,
        login="testuser",
        encrypted_token=encrypt_token("fake-token"),
        base_url="https://api.github.com",
    )
    db_session.add(account)
    await db_session.flush()

    space = Space(
        slug="org",
        name="org",
        space_type="org",
        github_account_id=account.id,
        user_id=user.id,
        is_active=True,
    )
    db_session.add(space)
    await db_session.commit()

    return {"user": user, "account": account, "space": space}


@pytest_asyncio.fixture
async def authed_client(async_engine, space_with_account) -> AsyncClient:
    """HTTPX test client with a valid github_user cookie set."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    user = space_with_account["user"]
    cookie = _make_github_cookie(user.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"github_user": cookie},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_repos_empty(client):
    resp = await client.get("/api/repos")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_add_repo(authed_client, space_with_account):
    """POST /api/repos should create a tracked repo (mocking GitHub)."""
    space = space_with_account["space"]
    mock_gh_repo = {"default_branch": "main", "full_name": "org/repo"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        resp = await authed_client.post(
            "/api/repos",
            json={"owner": "org", "name": "repo", "space_id": space.id},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "org/repo"
    assert data["is_active"] is True
    assert data["default_branch"] == "main"


@pytest.mark.asyncio
async def test_add_duplicate_repo(authed_client, space_with_account):
    """Adding the same repo twice should return 409."""
    space = space_with_account["space"]
    mock_gh_repo = {"default_branch": "main", "full_name": "dup/repo"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        resp1 = await authed_client.post(
            "/api/repos",
            json={"owner": "dup", "name": "repo", "space_id": space.id},
        )
        assert resp1.status_code == 201

        resp2 = await authed_client.post(
            "/api/repos",
            json={"owner": "dup", "name": "repo", "space_id": space.id},
        )
        assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_list_repos_with_stats(authed_client, db_session: AsyncSession, space_with_account):
    """Repos should include open_pr_count and other stats."""
    user = space_with_account["user"]
    space = space_with_account["space"]

    repo = TrackedRepo(
        owner="stats",
        name="repo",
        full_name="stats/repo",
        default_branch="main",
    )
    db_session.add(repo)
    await db_session.flush()

    # Create a RepoTracker with shared visibility so it shows up
    tracker = RepoTracker(
        user_id=user.id,
        repo_id=repo.id,
        space_id=space.id,
        visibility="shared",
    )
    db_session.add(tracker)

    now = datetime.now(UTC)
    db_session.add(
        PullRequest(
            repo_id=repo.id,
            number=1,
            title="Open PR",
            state="open",
            draft=False,
            head_ref="feature",
            base_ref="main",
            author="dev",
            additions=5,
            deletions=3,
            changed_files=1,
            html_url="https://github.com/stats/repo/pull/1",
            created_at=now,
            updated_at=now,
            last_synced_at=now,
        )
    )
    await db_session.commit()

    resp = await authed_client.get("/api/repos")
    assert resp.status_code == 200
    repos = resp.json()
    assert len(repos) == 1
    assert repos[0]["open_pr_count"] == 1


@pytest.mark.asyncio
async def test_delete_repo_soft_deletes(
    authed_client, db_session: AsyncSession, space_with_account
):
    """DELETE should remove the user's tracker; repo deactivates when no trackers remain."""
    user = space_with_account["user"]
    space = space_with_account["space"]

    repo = TrackedRepo(owner="del", name="repo", full_name="del/repo", default_branch="main")
    db_session.add(repo)
    await db_session.flush()

    tracker = RepoTracker(
        user_id=user.id,
        repo_id=repo.id,
        space_id=space.id,
    )
    db_session.add(tracker)
    await db_session.commit()

    resp = await authed_client.delete(f"/api/repos/{repo.id}")
    assert resp.status_code == 204

    await db_session.refresh(repo)
    assert repo.is_active is False


@pytest.mark.asyncio
async def test_delete_nonexistent_repo(client):
    resp = await client.delete("/api/repos/99999")
    assert resp.status_code == 404


# ── Edge case tests for RepoTracker ──────────────────────────


@pytest_asyncio.fixture
async def second_user(db_session: AsyncSession, space_with_account):
    """Create a second user with their own account and space."""
    user2 = User(github_id=2, login="user2", name="User Two")
    db_session.add(user2)
    await db_session.flush()

    account2 = GitHubAccount(
        user_id=user2.id,
        github_id=2,
        login="user2",
        encrypted_token=encrypt_token("fake-token-2"),
        base_url="https://api.github.com",
    )
    db_session.add(account2)
    await db_session.flush()

    space2 = Space(
        slug="org",
        name="org",
        space_type="org",
        github_account_id=account2.id,
        user_id=user2.id,
        is_active=True,
    )
    db_session.add(space2)
    await db_session.commit()

    return {"user": user2, "account": account2, "space": space2}


@pytest_asyncio.fixture
async def authed_client_user2(async_engine, second_user) -> AsyncClient:
    """HTTPX test client authenticated as user2."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    user2 = second_user["user"]
    cookie = _make_github_cookie(user2.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"github_user": cookie},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_two_users_track_same_repo(
    authed_client, authed_client_user2, space_with_account, second_user
):
    """Two users can independently track the same repo without conflict."""
    space1 = space_with_account["space"]
    space2 = second_user["space"]
    mock_gh_repo = {"default_branch": "main", "full_name": "org/shared-repo"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        # User 1 adds the repo
        resp1 = await authed_client.post(
            "/api/repos",
            json={"owner": "org", "name": "shared-repo", "space_id": space1.id},
        )
        assert resp1.status_code == 201
        repo_id = resp1.json()["id"]

        # User 2 adds the same repo — should succeed (creates new tracker)
        resp2 = await authed_client_user2.post(
            "/api/repos",
            json={"owner": "org", "name": "shared-repo", "space_id": space2.id},
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == repo_id  # Same TrackedRepo row

    # Verify tracker_count=2
    repos_resp = await authed_client.get("/api/repos")
    repos = repos_resp.json()
    shared = [r for r in repos if r["full_name"] == "org/shared-repo"]
    assert len(shared) == 1
    assert shared[0]["tracker_count"] == 2


@pytest.mark.asyncio
async def test_delete_one_tracker_keeps_repo_active(
    authed_client, authed_client_user2, db_session, space_with_account, second_user
):
    """Deleting one user's tracker keeps the repo active if another tracker remains."""
    space1 = space_with_account["space"]
    space2 = second_user["space"]
    mock_gh_repo = {"default_branch": "main", "full_name": "org/multi-track"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        resp1 = await authed_client.post(
            "/api/repos",
            json={"owner": "org", "name": "multi-track", "space_id": space1.id},
        )
        repo_id = resp1.json()["id"]

        await authed_client_user2.post(
            "/api/repos",
            json={"owner": "org", "name": "multi-track", "space_id": space2.id},
        )

    # User 1 deletes — repo should remain active
    del_resp = await authed_client.delete(f"/api/repos/{repo_id}")
    assert del_resp.status_code == 204

    # Refresh from DB to check
    db_session.expire_all()
    repo = await db_session.get(TrackedRepo, repo_id)
    assert repo.is_active is True

    # User 2 still sees the repo
    repos_resp = await authed_client_user2.get("/api/repos")
    repos = repos_resp.json()
    assert any(r["full_name"] == "org/multi-track" for r in repos)


@pytest.mark.asyncio
async def test_visibility_private_hides_from_other_user(
    authed_client, authed_client_user2, space_with_account, second_user
):
    """A private repo tracked only by user1 should not be visible to user2."""
    space1 = space_with_account["space"]
    mock_gh_repo = {"default_branch": "main", "full_name": "org/private-repo"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        resp = await authed_client.post(
            "/api/repos",
            json={"owner": "org", "name": "private-repo", "space_id": space1.id},
        )
        assert resp.status_code == 201

    # Default visibility is "private" — user2 should not see it
    repos_resp = await authed_client_user2.get("/api/repos")
    repos = repos_resp.json()
    assert not any(r["full_name"] == "org/private-repo" for r in repos)


@pytest.mark.asyncio
async def test_visibility_shared_shows_to_other_user(
    authed_client, authed_client_user2, space_with_account, second_user
):
    """A shared repo should be visible to all users."""
    space1 = space_with_account["space"]
    mock_gh_repo = {"default_branch": "main", "full_name": "org/shared-vis"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        resp = await authed_client.post(
            "/api/repos",
            json={"owner": "org", "name": "shared-vis", "space_id": space1.id},
        )
        repo_id = resp.json()["id"]

    # Set to shared
    vis_resp = await authed_client.patch(
        f"/api/repos/{repo_id}/visibility",
        json={"visibility": "shared"},
    )
    assert vis_resp.status_code == 200

    # User2 should see it now
    repos_resp = await authed_client_user2.get("/api/repos")
    repos = repos_resp.json()
    assert any(r["full_name"] == "org/shared-vis" for r in repos)


@pytest.mark.asyncio
async def test_visibility_requires_tracker(authed_client_user2, db_session, space_with_account):
    """Setting visibility on a repo you don't track returns 403."""
    repo = TrackedRepo(owner="vis", name="repo", full_name="vis/repo", default_branch="main")
    db_session.add(repo)
    await db_session.flush()

    # Only user1 has a tracker (via space_with_account fixture dependency)
    user1 = space_with_account["user"]
    space1 = space_with_account["space"]
    tracker = RepoTracker(
        user_id=user1.id,
        repo_id=repo.id,
        space_id=space1.id,
    )
    db_session.add(tracker)
    await db_session.commit()

    # User2 tries to set visibility — should fail
    resp = await authed_client_user2.patch(
        f"/api/repos/{repo.id}/visibility",
        json={"visibility": "shared"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_repo_without_space_id_returns_400(authed_client):
    """POST /api/repos without space_id returns 400."""
    resp = await authed_client.post(
        "/api/repos",
        json={"owner": "org", "name": "repo"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_reactivate_inactive_repo(authed_client, db_session, space_with_account):
    """Adding a previously deactivated repo should reactivate it."""
    space = space_with_account["space"]

    # Create an inactive repo
    repo = TrackedRepo(
        owner="react",
        name="repo",
        full_name="react/repo",
        default_branch="main",
        is_active=False,
    )
    db_session.add(repo)
    await db_session.commit()

    mock_gh_repo = {"default_branch": "main", "full_name": "react/repo"}

    with (
        patch(
            "src.api.repos.GitHubClient.get_repo",
            new_callable=AsyncMock,
            return_value=mock_gh_repo,
        ),
        patch(
            "src.api.repos.GitHubClient.close",
            new_callable=AsyncMock,
        ),
    ):
        resp = await authed_client.post(
            "/api/repos",
            json={"owner": "react", "name": "repo", "space_id": space.id},
        )

    assert resp.status_code == 201
    assert resp.json()["is_active"] is True

    await db_session.refresh(repo)
    assert repo.is_active is True
