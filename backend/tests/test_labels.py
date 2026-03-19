"""Tests for the PATCH /api/repos/{id}/pulls/{number}/labels endpoint."""

import time
from datetime import UTC, datetime, timedelta
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
    expires = int(time.time()) + settings.session_max_age_seconds
    return _sign(f"{user_id}:{expires}")


@pytest_asyncio.fixture
async def setup(db_session: AsyncSession):
    user = User(github_id=300, login="labeluser", name="Label User")
    db_session.add(user)
    await db_session.flush()

    account = GitHubAccount(
        user_id=user.id,
        github_id=300,
        login="labeluser",
        encrypted_token=encrypt_token("fake-token"),
        base_url="https://api.github.com",
    )
    db_session.add(account)
    await db_session.flush()

    space = Space(
        slug="labelorg",
        name="labelorg",
        space_type="org",
        github_account_id=account.id,
        user_id=user.id,
        is_active=True,
    )
    db_session.add(space)
    await db_session.flush()

    repo = TrackedRepo(
        owner="labelorg", name="labelrepo", full_name="labelorg/labelrepo", is_active=True
    )
    db_session.add(repo)
    await db_session.flush()

    tracker = RepoTracker(user_id=user.id, repo_id=repo.id, space_id=space.id, visibility="shared")
    db_session.add(tracker)
    await db_session.flush()

    now = datetime.now(UTC)
    pr = PullRequest(
        repo_id=repo.id,
        number=42,
        title="Label test PR",
        state="open",
        draft=False,
        head_ref="feature-labels",
        base_ref="main",
        author="alice",
        html_url="https://github.com/labelorg/labelrepo/pull/42",
        created_at=now - timedelta(days=1),
        updated_at=now,
        labels=[],
    )
    db_session.add(pr)
    await db_session.commit()

    return {"user": user, "repo": repo, "pr": pr}


@pytest_asyncio.fixture
async def authed_client(async_engine, setup) -> AsyncClient:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    cookie = _make_github_cookie(setup["user"].id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"github_user": cookie},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_add_valid_label(authed_client, setup):
    """Adding a valid label syncs to GitHub and updates local state."""
    repo = setup["repo"]
    pr = setup["pr"]

    with patch("src.api.pulls._get_github_client_for_user", new_callable=AsyncMock) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/labels",
            json={"add": ["bug"], "remove": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    label_names = [lbl["name"] for lbl in data["labels"]]
    assert "bug" in label_names
    mock_gh.ensure_label.assert_called_once()
    mock_gh.add_labels.assert_called_once_with("labelorg", "labelrepo", pr.number, ["bug"])
    mock_gh.close.assert_called_once()


@pytest.mark.asyncio
async def test_remove_label(authed_client, setup):
    """Removing a label syncs to GitHub."""
    repo = setup["repo"]
    pr = setup["pr"]

    with patch("src.api.pulls._get_github_client_for_user", new_callable=AsyncMock) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/labels",
            json={"add": [], "remove": ["enhancement"]},
        )

    assert resp.status_code == 200
    mock_gh.remove_label.assert_called_once_with("labelorg", "labelrepo", pr.number, "enhancement")


@pytest.mark.asyncio
async def test_invalid_label_rejected(authed_client, setup):
    """Unknown label names are rejected with 422."""
    repo = setup["repo"]
    pr = setup["pr"]

    resp = await authed_client.patch(
        f"/api/repos/{repo.id}/pulls/{pr.number}/labels",
        json={"add": ["not-a-real-label"], "remove": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_label_pr_not_found(authed_client, setup):
    """Labeling a nonexistent PR returns 404."""
    repo = setup["repo"]

    resp = await authed_client.patch(
        f"/api/repos/{repo.id}/pulls/9999/labels",
        json={"add": ["bug"], "remove": []},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_and_remove_in_same_request(authed_client, setup):
    """Adding and removing labels in a single request works."""
    repo = setup["repo"]
    pr = setup["pr"]

    with patch("src.api.pulls._get_github_client_for_user", new_callable=AsyncMock) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await authed_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/labels",
            json={"add": ["bug", "testing"], "remove": ["documentation"]},
        )

    assert resp.status_code == 200
    data = resp.json()
    label_names = [lbl["name"] for lbl in data["labels"]]
    assert "bug" in label_names
    assert "testing" in label_names
    mock_gh.add_labels.assert_called_once()
    mock_gh.remove_label.assert_called_once()
