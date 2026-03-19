"""Tests for GitHub API error handling: 5xx retry and error detail extraction."""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.auth import _sign
from src.api.pulls import _github_error_detail
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
from src.services.github_client import GitHubClient

# ── Helpers ──────────────────────────────────────────


def _make_response(
    status_code: int,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    text: str = "",
) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = httpx.Headers(headers or {})
    resp.text = text
    resp.is_success = 200 <= status_code < 300
    if json_body is not None:
        resp.json.return_value = json_body
    else:
        resp.json.side_effect = ValueError("No JSON")
    resp.request = MagicMock(spec=httpx.Request)
    resp.request.url = "https://api.github.com/test"
    return resp


def _make_github_cookie(user_id: int) -> str:
    expires = int(time.time()) + settings.session_max_age_seconds
    return _sign(f"{user_id}:{expires}")


# ── _github_error_detail unit tests ─────────────────


class TestGitHubErrorDetail:
    def test_httpx_error_with_message(self):
        """Extracts GitHub's message field from response body."""
        resp = _make_response(422, json_body={"message": "Validation Failed"})
        exc = httpx.HTTPStatusError("422", request=resp.request, response=resp)
        assert _github_error_detail(exc) == "GitHub API error: Validation Failed"

    def test_httpx_error_with_message_and_errors(self):
        """Extracts message + errors array from response body."""
        resp = _make_response(
            422,
            json_body={
                "message": "Validation Failed",
                "errors": [{"message": "User is not a collaborator"}],
            },
        )
        exc = httpx.HTTPStatusError("422", request=resp.request, response=resp)
        result = _github_error_detail(exc)
        assert result == "GitHub API error: Validation Failed (User is not a collaborator)"

    def test_httpx_error_with_multiple_errors(self):
        """Joins multiple error messages with semicolons."""
        resp = _make_response(
            422,
            json_body={
                "message": "Validation Failed",
                "errors": [
                    {"message": "User is not a collaborator"},
                    {"message": "Reviews may not be requested from authors"},
                ],
            },
        )
        exc = httpx.HTTPStatusError("422", request=resp.request, response=resp)
        result = _github_error_detail(exc)
        assert "User is not a collaborator" in result
        assert "Reviews may not be requested from authors" in result

    def test_httpx_error_no_json_body(self):
        """Falls back to HTTP status code when body is not JSON."""
        resp = _make_response(502, text="Bad Gateway")
        exc = httpx.HTTPStatusError("502", request=resp.request, response=resp)
        assert _github_error_detail(exc) == "GitHub API error (HTTP 502)"

    def test_httpx_error_empty_message(self):
        """Falls back to HTTP status code when message is empty."""
        resp = _make_response(500, json_body={"message": ""})
        exc = httpx.HTTPStatusError("500", request=resp.request, response=resp)
        assert _github_error_detail(exc) == "GitHub API error (HTTP 500)"

    def test_non_httpx_exception(self):
        """Non-HTTP exceptions include the exception text."""
        exc = ConnectionError("Connection reset by peer")
        result = _github_error_detail(exc)
        assert "Connection reset by peer" in result

    def test_httpx_error_with_empty_errors_array(self):
        """Empty errors array falls through to message-only."""
        resp = _make_response(
            422,
            json_body={"message": "Validation Failed", "errors": []},
        )
        exc = httpx.HTTPStatusError("422", request=resp.request, response=resp)
        assert _github_error_detail(exc) == "GitHub API error: Validation Failed"


# ── 5xx retry tests ─────────────────────────────────


class TestServerErrorRetry:
    @pytest.mark.asyncio
    async def test_500_then_success(self):
        """500 on first attempt triggers retry, succeeds on second."""
        client = GitHubClient(token="test-token")
        error_resp = _make_response(500, json_body={"message": "Internal Server Error"})
        ok_resp = _make_response(200, json_body={"ok": True})
        ok_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.request.side_effect = [error_resp, ok_resp]
        mock_http.is_closed = False
        client._client = mock_http

        with patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request_with_retry("GET", "/test")

        assert resp.status_code == 200
        assert mock_http.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_502_then_success(self):
        """502 on first attempt triggers retry."""
        client = GitHubClient(token="test-token")
        error_resp = _make_response(502, json_body={"message": "Bad Gateway"})
        ok_resp = _make_response(200, json_body={"ok": True})
        ok_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.request.side_effect = [error_resp, ok_resp]
        mock_http.is_closed = False
        client._client = mock_http

        with patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request_with_retry("GET", "/test")

        assert resp.status_code == 200
        assert mock_http.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_503_then_success(self):
        """503 on first attempt triggers retry."""
        client = GitHubClient(token="test-token")
        error_resp = _make_response(503, json_body={"message": "Service Unavailable"})
        ok_resp = _make_response(200, json_body={"ok": True})
        ok_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.request.side_effect = [error_resp, ok_resp]
        mock_http.is_closed = False
        client._client = mock_http

        with patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request_with_retry("GET", "/test")

        assert resp.status_code == 200
        assert mock_http.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_5xx_exhausts_retries(self):
        """Persistent 500 raises after exhausting all retries."""
        client = GitHubClient(token="test-token")
        error_resp = _make_response(500, json_body={"message": "Internal Server Error"})
        err = httpx.HTTPStatusError("500", request=error_resp.request, response=error_resp)
        error_resp.raise_for_status = MagicMock(side_effect=err)

        mock_http = AsyncMock()
        mock_http.request.return_value = error_resp
        mock_http.is_closed = False
        client._client = mock_http

        with (
            patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client._request_with_retry("GET", "/test")

        # Should retry twice then fail on third (total 3 attempts)
        assert mock_http.request.call_count == 3

        await client.close()

    @pytest.mark.asyncio
    async def test_404_not_retried(self):
        """4xx errors (other than rate limits) are NOT retried."""
        client = GitHubClient(token="test-token")
        error_resp = _make_response(404, json_body={"message": "Not Found"})
        err = httpx.HTTPStatusError("404", request=error_resp.request, response=error_resp)
        error_resp.raise_for_status = MagicMock(side_effect=err)

        mock_http = AsyncMock()
        mock_http.request.return_value = error_resp
        mock_http.is_closed = False
        client._client = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client._request_with_retry("GET", "/test")

        # Should fail immediately, no retries
        assert mock_http.request.call_count == 1

        await client.close()


# ── Reviewer endpoint error detail surfacing ─────────


@pytest_asyncio.fixture
async def reviewer_setup(db_session: AsyncSession):
    user = User(github_id=500, login="reviewer-tester", name="Reviewer Tester")
    db_session.add(user)
    await db_session.flush()

    account = GitHubAccount(
        user_id=user.id,
        github_id=500,
        login="reviewer-tester",
        encrypted_token=encrypt_token("fake-token"),
        base_url="https://api.github.com",
    )
    db_session.add(account)
    await db_session.flush()

    space = Space(
        slug="errorg",
        name="errorg",
        space_type="org",
        github_account_id=account.id,
        user_id=user.id,
        is_active=True,
    )
    db_session.add(space)
    await db_session.flush()

    repo = TrackedRepo(owner="errorg", name="errrepo", full_name="errorg/errrepo", is_active=True)
    db_session.add(repo)
    await db_session.flush()

    tracker = RepoTracker(user_id=user.id, repo_id=repo.id, space_id=space.id, visibility="shared")
    db_session.add(tracker)
    await db_session.flush()

    now = datetime.now(UTC)
    pr = PullRequest(
        repo_id=repo.id,
        number=99,
        title="Error test PR",
        state="open",
        draft=False,
        head_ref="feature-err",
        base_ref="main",
        author="alice",
        html_url="https://github.com/errorg/errrepo/pull/99",
        created_at=now - timedelta(days=1),
        updated_at=now,
        github_requested_reviewers=[],
    )
    db_session.add(pr)
    await db_session.commit()

    return {"user": user, "repo": repo, "pr": pr}


@pytest_asyncio.fixture
async def reviewer_client(async_engine, reviewer_setup) -> AsyncClient:
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    cookie = _make_github_cookie(reviewer_setup["user"].id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"github_user": cookie},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_reviewer_error_surfaces_github_detail(reviewer_client, reviewer_setup):
    """When GitHub rejects a reviewer request, the error detail is surfaced to the client."""
    repo = reviewer_setup["repo"]
    pr = reviewer_setup["pr"]
    user = reviewer_setup["user"]

    mock_resp = _make_response(
        422,
        json_body={
            "message": "Validation Failed",
            "errors": [{"message": "Reviews may not be requested from pull request authors"}],
        },
    )
    gh_error = httpx.HTTPStatusError("422", request=mock_resp.request, response=mock_resp)

    with patch("src.api.pulls._get_github_client_for_user", new_callable=AsyncMock) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_gh.request_reviewers.side_effect = gh_error
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await reviewer_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/reviewers",
            json={"add_user_ids": [user.id], "remove_logins": []},
        )

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Validation Failed" in detail
    assert "Reviews may not be requested from pull request authors" in detail


@pytest.mark.asyncio
async def test_label_error_surfaces_github_detail(reviewer_client, reviewer_setup):
    """When GitHub rejects a label operation, the error detail is surfaced."""
    repo = reviewer_setup["repo"]
    pr = reviewer_setup["pr"]

    mock_resp = _make_response(404, json_body={"message": "Not Found"})
    gh_error = httpx.HTTPStatusError("404", request=mock_resp.request, response=mock_resp)

    with patch("src.api.pulls._get_github_client_for_user", new_callable=AsyncMock) as mock_get_gh:
        mock_gh = AsyncMock()
        mock_gh.ensure_label.side_effect = gh_error
        mock_get_gh.return_value = (mock_gh, repo)

        resp = await reviewer_client.patch(
            f"/api/repos/{repo.id}/pulls/{pr.number}/labels",
            json={"add": ["bug"], "remove": []},
        )

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "Not Found" in detail
