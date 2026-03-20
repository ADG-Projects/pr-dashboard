"""Tests for the GitHub API client."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.services.github_client import (
    AuthErrorType,
    GitHubAuthError,
    GitHubClient,
    _raise_for_status,
    parse_gh_datetime,
)


def _mock_response(status_code: int = 200, json_data=None, headers=None):
    """Build an httpx.Response with a fake request attached (needed by raise_for_status)."""
    h = {"content-type": "application/json"}
    if headers:
        h.update(headers)
    resp = httpx.Response(
        status_code,
        json=json_data,
        headers=h,
        request=httpx.Request("GET", "https://api.github.com/test"),
    )
    return resp


class TestParseGhDatetime:
    def test_parses_utc_z(self):
        dt = parse_gh_datetime("2025-06-15T10:30:00Z")
        assert dt is not None
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.hour == 10

    def test_parses_offset(self):
        dt = parse_gh_datetime("2025-06-15T10:30:00+00:00")
        assert dt is not None

    def test_none_returns_none(self):
        assert parse_gh_datetime(None) is None

    def test_empty_string_returns_none(self):
        assert parse_gh_datetime("") is None


class TestRaiseForStatus:
    """Tests for _raise_for_status error classification."""

    def test_401_bad_credentials_classified_as_token_expired(self):
        resp = _mock_response(401, json_data={"message": "Bad credentials"})
        with pytest.raises(GitHubAuthError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.error_type == AuthErrorType.token_expired

    def test_401_revoked_classified_as_token_revoked(self):
        resp = _mock_response(401, json_data={"message": "Token has been revoked"})
        with pytest.raises(GitHubAuthError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.error_type == AuthErrorType.token_revoked

    def test_403_saml_classified_as_sso_required(self):
        resp = _mock_response(403, json_data={"message": "Resource protected by SAML enforcement"})
        with pytest.raises(GitHubAuthError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.error_type == AuthErrorType.sso_required

    def test_403_sso_classified_as_sso_required(self):
        resp = _mock_response(403, json_data={"message": "SSO authorization required"})
        with pytest.raises(GitHubAuthError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.error_type == AuthErrorType.sso_required

    def test_403_scope_classified_as_insufficient_scope(self):
        resp = _mock_response(
            403, json_data={"message": "Insufficient permissions for this resource"}
        )
        with pytest.raises(GitHubAuthError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.error_type == AuthErrorType.insufficient_scope

    def test_403_generic_classified_as_insufficient_scope(self):
        resp = _mock_response(403, json_data={"message": "Forbidden"})
        with pytest.raises(GitHubAuthError) as exc_info:
            _raise_for_status(resp)
        assert exc_info.value.error_type == AuthErrorType.insufficient_scope

    def test_403_rate_limit_not_raised_as_auth_error(self):
        resp = _mock_response(
            403,
            json_data={"message": "API rate limit exceeded"},
            headers={"retry-after": "60"},
        )
        # Should NOT raise GitHubAuthError for rate limits
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            _raise_for_status(resp)
        assert not isinstance(exc_info.value, GitHubAuthError)

    def test_403_abuse_rate_limit_not_raised_as_auth_error(self):
        resp = _mock_response(
            403, json_data={"message": "You have exceeded a secondary rate limit"}
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            _raise_for_status(resp)
        assert not isinstance(exc_info.value, GitHubAuthError)

    def test_200_does_not_raise(self):
        resp = _mock_response(200, json_data={"ok": True})
        _raise_for_status(resp)  # should not raise

    def test_404_raises_generic_http_error(self):
        resp = _mock_response(404, json_data={"message": "Not Found"})
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            _raise_for_status(resp)
        assert not isinstance(exc_info.value, GitHubAuthError)


class TestGitHubClient:
    @pytest.mark.asyncio
    async def test_list_open_pulls(self):
        """Verify list_open_pulls calls the correct endpoint."""
        mock_resp = _mock_response(json_data=[{"number": 1, "title": "test"}])

        with patch.object(
            httpx.AsyncClient, "request", new_callable=AsyncMock, return_value=mock_resp
        ):
            client = GitHubClient(token="fake-token")
            pulls = await client.list_open_pulls("org", "repo")
            assert len(pulls) == 1
            assert pulls[0]["number"] == 1
            await client.close()

    @pytest.mark.asyncio
    async def test_pagination_follows_next_link(self):
        """Verify _get_paginated follows Link headers."""
        page1_resp = _mock_response(
            json_data=[{"id": 1}],
            headers={"link": '<https://api.github.com/next?page=2>; rel="next"'},
        )
        page2_resp = _mock_response(json_data=[{"id": 2}])

        call_count = 0

        async def mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return page1_resp if call_count == 1 else page2_resp

        with patch.object(httpx.AsyncClient, "request", side_effect=mock_request):
            client = GitHubClient(token="fake-token")
            results = await client._get_paginated("/test")
            assert len(results) == 2
            assert results[0]["id"] == 1
            assert results[1]["id"] == 2
            await client.close()

    @pytest.mark.asyncio
    async def test_client_sends_auth_header(self):
        """Verify the Authorization header is set when token is provided."""
        client = GitHubClient(token="my-secret-token")
        http_client = await client._ensure_client()
        assert http_client.headers["Authorization"] == "Bearer my-secret-token"
        await client.close()

    @pytest.mark.asyncio
    async def test_client_reuses_connection(self):
        """Calling _ensure_client twice returns the same client."""
        client = GitHubClient(token="tok")
        c1 = await client._ensure_client()
        c2 = await client._ensure_client()
        assert c1 is c2
        await client.close()
