"""Async GitHub API client using httpx."""

import asyncio
import enum
import time
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

_MAX_RETRIES = 3
_BASE_RETRY_WAIT = 5  # seconds
_RETRY_MULTIPLIER = 3  # exponential backoff multiplier
_CONCURRENCY_LIMIT = 10  # max concurrent requests
_MIN_REQUEST_INTERVAL = 0.1  # 100ms between requests
_RATE_LIMIT_WARNING_THRESHOLD = 200  # warn when remaining drops below this


class AuthErrorType(str, enum.Enum):
    """Classification of GitHub authentication/authorization errors."""

    token_expired = "token_expired"
    token_revoked = "token_revoked"
    insufficient_scope = "insufficient_scope"
    sso_required = "sso_required"
    decrypt_failed = "decrypt_failed"
    repo_not_accessible = "repo_not_accessible"


class GitHubAuthError(httpx.HTTPStatusError):
    """Raised when GitHub returns 401/403 (bad token, insufficient permissions, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        error_type: AuthErrorType = AuthErrorType.insufficient_scope,
    ) -> None:
        super().__init__(message, request=request, response=response)
        self.error_type = error_type


def _is_secondary_rate_limit(resp: httpx.Response) -> bool:
    """Check if a 403 is GitHub's secondary (abuse) rate limit, not a real auth error."""
    if resp.status_code != 403:
        return False
    if resp.headers.get("retry-after"):
        return True
    try:
        body = resp.json()
        msg = body.get("message", "").lower()
        if "rate limit" in msg or "abuse" in msg:
            return True
    except Exception:
        pass
    return False


def _retry_wait_seconds(resp: httpx.Response, attempt: int) -> float:
    """Compute wait time with exponential backoff, respecting Retry-After header."""
    backoff = _BASE_RETRY_WAIT * (_RETRY_MULTIPLIER**attempt)
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return max(float(raw), backoff)
        except ValueError:
            pass
    return backoff


def _classify_auth_error(resp: httpx.Response) -> AuthErrorType:
    """Determine the specific auth error type from a 401/403 response."""
    body_text = ""
    try:
        body = resp.json()
        body_text = body.get("message", "").lower()
    except (ValueError, UnicodeDecodeError):
        body_text = (resp.text or "").lower()

    if resp.status_code == 401:
        if "revoke" in body_text:
            return AuthErrorType.token_revoked
        return AuthErrorType.token_expired

    # 403 - check for SSO/SAML first
    if "saml" in body_text or "sso" in body_text:
        return AuthErrorType.sso_required

    return AuthErrorType.insufficient_scope


def _raise_for_status(resp: httpx.Response) -> None:
    """Like resp.raise_for_status() but raises GitHubAuthError for 401/403.

    Rate-limit 403s are excluded and raised as plain HTTPStatusError.
    """
    if resp.status_code == 401:
        error_type = _classify_auth_error(resp)
        body_preview = resp.text[:300] if resp.text else "(empty)"
        raise GitHubAuthError(
            f"GitHub auth error {resp.status_code} for {resp.request.url}: {body_preview}",
            request=resp.request,
            response=resp,
            error_type=error_type,
        )
    if resp.status_code == 403:
        if _is_secondary_rate_limit(resp):
            resp.raise_for_status()
            return
        error_type = _classify_auth_error(resp)
        body_preview = resp.text[:300] if resp.text else "(empty)"
        raise GitHubAuthError(
            f"GitHub auth error {resp.status_code} for {resp.request.url}: {body_preview}",
            request=resp.request,
            response=resp,
            error_type=error_type,
        )
    resp.raise_for_status()


class GitHubClient:
    """Thin async wrapper around the GitHub REST API."""

    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com") -> None:
        self._token = token or ""
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(_CONCURRENCY_LIMIT)
        self._timing_lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._rate_limited = False

    @property
    def rate_limited(self) -> bool:
        """True when retries were exhausted on a rate limit error."""
        return self._rate_limited

    def reset_rate_limited(self) -> None:
        """Reset the rate_limited flag (call at the start of each sync)."""
        self._rate_limited = False

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers=headers, timeout=30.0, follow_redirects=True
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _throttle(self) -> None:
        """Ensure minimum interval between requests to avoid secondary rate limits."""
        async with self._timing_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < _MIN_REQUEST_INTERVAL:
                await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.monotonic()

    def _check_rate_limit_headers(self, resp: httpx.Response) -> None:
        """Monitor primary rate limit from response headers."""
        remaining_raw = resp.headers.get("x-ratelimit-remaining")
        reset_raw = resp.headers.get("x-ratelimit-reset")
        if remaining_raw is None:
            return
        try:
            remaining = int(remaining_raw)
        except ValueError:
            return
        if remaining <= 0 and reset_raw:
            try:
                reset_at = int(reset_raw)
                sleep_for = max(reset_at - time.time() + 1, 1)
                logger.warning(
                    f"GitHub primary rate limit exhausted, sleeping {sleep_for:.0f}s until reset"
                )
                # Schedule the sleep in the calling coroutine via a flag;
                # we can't await here since this is sync, so we store it.
                self._rate_limit_sleep = sleep_for
            except ValueError:
                pass
        elif remaining < _RATE_LIMIT_WARNING_THRESHOLD:
            logger.warning(f"GitHub rate limit remaining: {remaining}")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        """Send a request with concurrency limiting, throttling, and retry.

        If raise_for_status is False, the raw response is returned without
        checking the status code (caller is responsible for handling errors).
        """
        client = await self._ensure_client()
        async with self._semaphore:
            for attempt in range(_MAX_RETRIES):
                await self._throttle()

                # Check if we need to sleep for primary rate limit reset
                sleep_needed = getattr(self, "_rate_limit_sleep", None)
                if sleep_needed:
                    self._rate_limit_sleep = None
                    await asyncio.sleep(sleep_needed)

                kwargs: dict[str, Any] = {}
                if params is not None:
                    kwargs["params"] = params
                if json is not None:
                    kwargs["json"] = json
                if extra_headers is not None:
                    kwargs["headers"] = extra_headers

                resp = await client.request(method, url, **kwargs)
                self._check_rate_limit_headers(resp)

                # Handle primary rate limit sleep that was just detected
                sleep_needed = getattr(self, "_rate_limit_sleep", None)
                if sleep_needed:
                    self._rate_limit_sleep = None
                    await asyncio.sleep(sleep_needed)

                # Retry on rate limits
                if resp.status_code == 429 or (
                    resp.status_code == 403 and _is_secondary_rate_limit(resp)
                ):
                    wait = _retry_wait_seconds(resp, attempt)
                    logger.warning(
                        f"GitHub rate limit hit ({resp.status_code}) for {url}, "
                        f"retrying in {wait:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                    )
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(wait)
                        continue
                    # Last attempt exhausted, mark as rate limited
                    self._rate_limited = True

                # Retry on transient server errors (500, 502, 503)
                if resp.status_code in (500, 502, 503) and attempt < _MAX_RETRIES - 1:
                    wait = _BASE_RETRY_WAIT * (_RETRY_MULTIPLIER**attempt)
                    logger.warning(
                        f"GitHub server error ({resp.status_code}) for {url}, "
                        f"retrying in {wait:.0f}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                    )
                    await asyncio.sleep(wait)
                    continue

                if raise_for_status:
                    _raise_for_status(resp)
                return resp

        # Should not reach here, but satisfy type checker
        if raise_for_status:
            _raise_for_status(resp)  # type: ignore[possibly-undefined]
        return resp  # type: ignore[possibly-undefined]

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("GET", path, params=params)
        return resp.json()

    async def _get_with_etag(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> tuple[Any, str | None, bool]:
        """GET with conditional request via ETag.

        Returns (data, new_etag, was_modified). If the server returns 304,
        data is None and was_modified is False.
        """
        extra_headers: dict[str, str] = {}
        if etag:
            extra_headers["If-None-Match"] = etag
        resp = await self._request_with_retry(
            "GET",
            path,
            params=params,
            extra_headers=extra_headers or None,
            raise_for_status=False,
        )
        if resp.status_code == 304:
            return None, etag, False
        _raise_for_status(resp)
        new_etag = resp.headers.get("etag")
        return resp.json(), new_etag, True

    async def _get_paginated(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated GitHub API endpoint."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results: list[dict[str, Any]] = []

        url: str | None = path
        while url:
            resp = await self._request_with_retry(
                "GET", url, params=params if url == path else None
            )
            results.extend(resp.json())
            # Follow Link: <...>; rel="next"
            link = resp.headers.get("link", "")
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break
        return results

    async def _get_paginated_with_etag(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        etag: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """Paginated GET with conditional request on the first page.

        Returns (results, new_etag, was_modified). If the first page returns
        304 Not Modified, results is [] and was_modified is False.
        """
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results: list[dict[str, Any]] = []

        extra_headers: dict[str, str] = {}
        if etag:
            extra_headers["If-None-Match"] = etag

        # First page with ETag
        resp = await self._request_with_retry(
            "GET",
            path,
            params=params,
            extra_headers=extra_headers or None,
            raise_for_status=False,
        )
        if resp.status_code == 304:
            return [], etag, False
        _raise_for_status(resp)
        new_etag = resp.headers.get("etag")
        results.extend(resp.json())

        # Remaining pages (no ETag, normal fetch)
        link = resp.headers.get("link", "")
        url: str | None = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip(" <>")
                break

        while url:
            resp = await self._request_with_retry("GET", url)
            results.extend(resp.json())
            link = resp.headers.get("link", "")
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break

        return results, new_etag, True

    async def _patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("PATCH", path, json=json)
        return resp.json()

    async def _post_json(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("POST", path, json=json)
        return resp.json()

    async def _delete_json(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("DELETE", path, json=json)
        return resp.json()

    async def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GitHub GraphQL query."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = await self._request_with_retry("POST", "/graphql", json=payload)
        data = resp.json()
        if "errors" in data:
            logger.warning(f"GraphQL errors: {data['errors']}")
        return data.get("data", {})

    # ── Public API ──────────────────────────────────────────────

    async def list_open_pulls(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List all open PRs for a repo."""
        return await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": "open",
                "sort": "updated",
                "direction": "desc",
            },
        )

    async def list_open_pulls_with_etag(
        self, owner: str, repo: str, etag: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """List open PRs with ETag support. Returns (pulls, new_etag, was_modified)."""
        return await self._get_paginated_with_etag(
            f"/repos/{owner}/{repo}/pulls",
            params={
                "state": "open",
                "sort": "updated",
                "direction": "desc",
            },
            etag=etag,
        )

    async def list_recently_closed_pulls(
        self, owner: str, repo: str, cutoff: datetime
    ) -> list[dict[str, Any]]:
        """List closed PRs updated after *cutoff*, paginating until we pass it.

        GitHub's Pulls API doesn't support a ``since`` parameter, so we fetch
        ``state=closed`` sorted by ``updated`` descending and stop once we see
        a PR whose ``updated_at`` is older than the cutoff.
        """
        params: dict[str, Any] = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }
        results: list[dict[str, Any]] = []

        url: str | None = f"/repos/{owner}/{repo}/pulls"
        while url:
            resp = await self._request_with_retry(
                "GET", url, params=params if url.startswith("/") else None
            )
            page: list[dict[str, Any]] = resp.json()
            if not page:
                break

            for pr in page:
                updated = parse_gh_datetime(pr.get("updated_at"))
                if updated and updated < cutoff:
                    return results
                results.append(pr)

            # Follow Link: <...>; rel="next"
            link = resp.headers.get("link", "")
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break

        return results

    async def list_recently_closed_pulls_with_etag(
        self, owner: str, repo: str, cutoff: datetime, etag: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, bool]:
        """Like list_recently_closed_pulls but with ETag on the first page.

        Returns (results, new_etag, was_modified). A 304 means nothing changed.
        """
        params: dict[str, Any] = {
            "state": "closed",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }

        extra_headers: dict[str, str] = {}
        if etag:
            extra_headers["If-None-Match"] = etag

        path = f"/repos/{owner}/{repo}/pulls"
        resp = await self._request_with_retry(
            "GET",
            path,
            params=params,
            extra_headers=extra_headers or None,
            raise_for_status=False,
        )
        if resp.status_code == 304:
            return [], etag, False
        _raise_for_status(resp)
        new_etag = resp.headers.get("etag")

        results: list[dict[str, Any]] = []
        page: list[dict[str, Any]] = resp.json()
        if not page:
            return results, new_etag, True

        for pr in page:
            updated = parse_gh_datetime(pr.get("updated_at"))
            if updated and updated < cutoff:
                return results, new_etag, True
            results.append(pr)

        # Remaining pages (no ETag)
        link = resp.headers.get("link", "")
        url: str | None = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip(" <>")
                break

        while url:
            resp = await self._request_with_retry("GET", url)
            page = resp.json()
            if not page:
                break
            for pr in page:
                updated = parse_gh_datetime(pr.get("updated_at"))
                if updated and updated < cutoff:
                    return results, new_etag, True
                results.append(pr)
            link = resp.headers.get("link", "")
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break

        return results, new_etag, True

    async def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Get full PR detail (includes mergeable_state, diff stats)."""
        return await self._get(f"/repos/{owner}/{repo}/pulls/{number}")

    async def get_workflow_runs(self, owner: str, repo: str, head_sha: str) -> list[dict[str, Any]]:
        """Get Actions workflow runs for a commit SHA."""
        data = await self._get(
            f"/repos/{owner}/{repo}/actions/runs",
            params={"head_sha": head_sha},
        )
        return data.get("workflow_runs", [])

    async def get_reviews(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """Get reviews for a PR."""
        return await self._get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/reviews")

    async def get_issue_comments(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """Get conversation comments on an issue/PR."""
        return await self._get_paginated(f"/repos/{owner}/{repo}/issues/{number}/comments")

    async def get_review_comments(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """Get inline review comments on a PR."""
        return await self._get_paginated(f"/repos/{owner}/{repo}/pulls/{number}/comments")

    async def list_user_orgs(self) -> list[dict[str, Any]]:
        """List orgs the authenticated user belongs to."""
        return await self._get_paginated("/user/orgs")

    async def get_authenticated_user(self) -> dict[str, Any]:
        """Get the authenticated user's profile."""
        return await self._get("/user")

    async def list_all_repos(self) -> list[dict[str, Any]]:
        """List all repos accessible to the authenticated user."""
        return await self._get_paginated(
            "/user/repos",
            params={"per_page": 100, "sort": "pushed", "direction": "desc"},
        )

    async def list_org_repos(self, org: str) -> list[dict[str, Any]]:
        """List all repos in an organization."""
        return await self._get_paginated(
            f"/orgs/{org}/repos",
            params={"type": "all", "sort": "pushed", "direction": "desc"},
        )

    async def list_user_repos(self, username: str) -> list[dict[str, Any]]:
        """List all repos for a user."""
        return await self._get_paginated(
            f"/users/{username}/repos",
            params={"type": "all", "sort": "pushed", "direction": "desc"},
        )

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        """Get repo metadata (for default_branch, etc.)."""
        return await self._get(f"/repos/{owner}/{repo}")

    async def get_user(self, login: str) -> dict[str, Any]:
        """Get a user's public profile (includes name, bio, etc.)."""
        return await self._get(f"/users/{login}")

    async def get_rate_limit(self) -> dict[str, Any]:
        """Check current rate limit status."""
        return await self._get("/rate_limit")

    async def get_unresolved_thread_counts(
        self, owner: str, repo: str, pr_numbers: list[int]
    ) -> dict[int, int]:
        """Fetch unresolved review thread counts for multiple PRs via GraphQL.

        Returns {pr_number: unresolved_count}. On failure (e.g., GHE without
        GraphQL), returns empty dict.
        """
        if not pr_numbers:
            return {}

        fragments = []
        for num in pr_numbers:
            fragments.append(
                f"pr{num}: pullRequest(number: {num}) {{ "
                f"reviewThreads(first: 100) {{ nodes {{ isResolved }} }} }}"
            )
        query = f'{{ repository(owner: "{owner}", name: "{repo}") {{ {" ".join(fragments)} }} }}'

        try:
            data = await self._graphql(query)
        except Exception as exc:
            logger.warning(f"GraphQL thread count fetch failed for {owner}/{repo}: {exc}")
            return {}

        repo_data = data.get("repository", {})
        result: dict[int, int] = {}
        for num in pr_numbers:
            pr_data = repo_data.get(f"pr{num}", {})
            threads = pr_data.get("reviewThreads", {}).get("nodes", [])
            result[num] = sum(1 for t in threads if not t.get("isResolved", True))
        return result

    # ── Write operations ──────────────────────────────────────

    async def set_assignees(
        self, owner: str, repo: str, issue_number: int, logins: list[str]
    ) -> dict[str, Any]:
        """Set assignees on an issue/PR."""
        return await self._patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            json={"assignees": logins},
        )

    async def request_reviewers(
        self, owner: str, repo: str, pr_number: int, logins: list[str]
    ) -> dict[str, Any]:
        """Request reviewers for a PR."""
        return await self._post_json(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            json={"reviewers": logins},
        )

    async def remove_reviewers(
        self, owner: str, repo: str, pr_number: int, logins: list[str]
    ) -> dict[str, Any]:
        """Remove requested reviewers from a PR."""
        return await self._delete_json(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            json={"reviewers": logins},
        )

    async def ensure_label(
        self, owner: str, repo: str, name: str, color: str, description: str = ""
    ) -> None:
        """Create a label if it doesn't exist, or update its color if it does."""
        resp = await self._request_with_retry(
            "GET", f"/repos/{owner}/{repo}/labels/{name}", raise_for_status=False
        )
        if resp.status_code == 404:
            await self._post_json(
                f"/repos/{owner}/{repo}/labels",
                json={"name": name, "color": color, "description": description},
            )
        elif resp.is_success and resp.json().get("color") != color:
            await self._patch(
                f"/repos/{owner}/{repo}/labels/{name}",
                json={"color": color, "description": description},
            )

    async def add_labels(
        self, owner: str, repo: str, issue_number: int, labels: list[str]
    ) -> list[dict[str, Any]]:
        """Add labels to an issue/PR."""
        return await self._post_json(
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )

    # ── Webhook management ─────────────────────────────────────

    async def create_webhook(
        self,
        owner: str,
        repo: str,
        callback_url: str,
        secret: str,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a webhook on a repo. Returns the created hook payload."""
        if events is None:
            events = [
                "pull_request",
                "pull_request_review",
                "pull_request_review_thread",
                "check_suite",
                "check_run",
                "issue_comment",
                "pull_request_review_comment",
            ]
        return await self._post_json(
            f"/repos/{owner}/{repo}/hooks",
            json={
                "name": "web",
                "active": True,
                "events": events,
                "config": {
                    "url": callback_url,
                    "content_type": "json",
                    "secret": secret,
                    "insecure_ssl": "0",
                },
            },
        )

    async def delete_webhook(self, owner: str, repo: str, hook_id: int) -> None:
        """Delete a webhook from a repo."""
        resp = await self._request_with_retry(
            "DELETE", f"/repos/{owner}/{repo}/hooks/{hook_id}", raise_for_status=False
        )
        if resp.status_code not in (204, 404):
            _raise_for_status(resp)

    async def list_webhooks(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List all webhooks on a repo."""
        return await self._get_paginated(f"/repos/{owner}/{repo}/hooks")

    async def update_webhook_events(
        self, owner: str, repo: str, hook_id: int, events: list[str]
    ) -> dict[str, Any]:
        """Update the events list on an existing webhook."""
        return await self._patch(
            f"/repos/{owner}/{repo}/hooks/{hook_id}",
            json={"events": events},
        )

    async def remove_label(self, owner: str, repo: str, issue_number: int, label: str) -> None:
        """Remove a single label from an issue/PR."""
        resp = await self._request_with_retry(
            "DELETE",
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
            raise_for_status=False,
        )
        # 404 means the label wasn't present — that's fine
        if resp.status_code != 404:
            _raise_for_status(resp)


def parse_gh_datetime(value: str | None) -> datetime | None:
    """Parse GitHub's ISO 8601 datetime string."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
