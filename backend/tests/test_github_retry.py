"""Tests for GitHub client retry and rate limit logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.github_client import (
    GitHubClient,
    _is_secondary_rate_limit,
    _retry_wait_seconds,
)


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
    # Need a request for _raise_for_status
    resp.request = MagicMock(spec=httpx.Request)
    resp.request.url = "https://api.github.com/test"
    return resp


# ── _is_secondary_rate_limit ─────────────────────────


class TestIsSecondaryRateLimit:
    def test_403_with_retry_after(self):
        """403 with Retry-After header = secondary rate limit."""
        resp = _make_response(403, headers={"retry-after": "60"})
        assert _is_secondary_rate_limit(resp) is True

    def test_403_with_rate_limit_message(self):
        """403 with 'rate limit' in body = secondary rate limit."""
        resp = _make_response(403, json_body={"message": "API rate limit exceeded"})
        assert _is_secondary_rate_limit(resp) is True

    def test_403_with_abuse_message(self):
        """403 with 'abuse' in body = secondary rate limit."""
        resp = _make_response(
            403, json_body={"message": "You have triggered an abuse detection mechanism"}
        )
        assert _is_secondary_rate_limit(resp) is True

    def test_403_real_auth_error(self):
        """403 without rate-limit signals = real auth error."""
        resp = _make_response(403, json_body={"message": "Resource not accessible by integration"})
        assert _is_secondary_rate_limit(resp) is False

    def test_non_403_status(self):
        """Non-403 status codes are never secondary rate limits."""
        resp = _make_response(401)
        assert _is_secondary_rate_limit(resp) is False

    def test_403_no_json_no_header(self):
        """403 with no parseable body and no header = not rate limit."""
        resp = _make_response(403, text="forbidden")
        assert _is_secondary_rate_limit(resp) is False


# ── _retry_wait_seconds ──────────────────────────────


class TestRetryWaitSeconds:
    def test_has_retry_after_header_larger_than_backoff(self):
        """Retry-After header wins when larger than computed backoff."""
        resp = _make_response(429, headers={"retry-after": "30"})
        assert _retry_wait_seconds(resp, attempt=0) == 30.0  # 30 > 5

    def test_backoff_wins_over_small_retry_after(self):
        """Backoff wins when Retry-After is smaller."""
        resp = _make_response(429, headers={"retry-after": "1"})
        assert _retry_wait_seconds(resp, attempt=1) == 15.0  # 5*3^1 = 15 > 1

    def test_no_header_uses_exponential_backoff(self):
        resp = _make_response(429)
        assert _retry_wait_seconds(resp, attempt=0) == 5  # 5 * 3^0
        assert _retry_wait_seconds(resp, attempt=1) == 15  # 5 * 3^1
        assert _retry_wait_seconds(resp, attempt=2) == 45  # 5 * 3^2

    def test_invalid_header_uses_backoff(self):
        resp = _make_response(429, headers={"retry-after": "not-a-number"})
        assert _retry_wait_seconds(resp, attempt=0) == 5


# ── _request_with_retry ──────────────────────────────


class TestRequestWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        """Successful request on first attempt, no retries."""
        client = GitHubClient(token="test-token")
        mock_resp = _make_response(200, json_body={"ok": True})
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.request.return_value = mock_resp
        mock_http.is_closed = False
        client._client = mock_http

        resp = await client._request_with_retry("GET", "/test")
        assert resp.status_code == 200
        assert mock_http.request.call_count == 1

        await client.close()

    @pytest.mark.asyncio
    async def test_429_then_success(self):
        """429 on first attempt, success on retry."""
        client = GitHubClient(token="test-token")
        rate_resp = _make_response(429, headers={"retry-after": "0"})
        ok_resp = _make_response(200, json_body={"ok": True})
        ok_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.request.side_effect = [rate_resp, ok_resp]
        mock_http.is_closed = False
        client._client = mock_http

        with patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request_with_retry("GET", "/test")

        assert resp.status_code == 200
        assert mock_http.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_secondary_rate_limit_then_success(self):
        """Secondary rate limit (403) triggers retry."""
        client = GitHubClient(token="test-token")
        rate_resp = _make_response(
            403,
            headers={"retry-after": "1"},
            json_body={"message": "secondary rate limit"},
        )
        ok_resp = _make_response(200, json_body={"ok": True})
        ok_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.request.side_effect = [rate_resp, ok_resp]
        mock_http.is_closed = False
        client._client = mock_http

        with patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock):
            resp = await client._request_with_retry("GET", "/test")

        assert resp.status_code == 200
        assert mock_http.request.call_count == 2

        await client.close()

    @pytest.mark.asyncio
    async def test_exhausts_all_retries(self):
        """Raises after exhausting all retries on persistent rate limiting."""
        client = GitHubClient(token="test-token")
        rate_resp = _make_response(429, headers={"retry-after": "0"})
        # _raise_for_status will be called; 429 does resp.raise_for_status()
        rate_resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("429", request=rate_resp.request, response=rate_resp)
        )

        mock_http = AsyncMock()
        mock_http.request.return_value = rate_resp
        mock_http.is_closed = False
        client._client = mock_http

        with (
            patch("src.services.github_client.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client._request_with_retry("GET", "/test")

        # Should have tried _MAX_RETRIES times
        assert mock_http.request.call_count == 3

        await client.close()
