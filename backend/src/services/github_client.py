"""Async GitHub API client using httpx."""

import asyncio
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

_MAX_RETRIES = 3
_DEFAULT_RETRY_WAIT = 5  # seconds


class GitHubAuthError(httpx.HTTPStatusError):
    """Raised when GitHub returns 401/403 (bad token, insufficient permissions, etc.)."""


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


def _retry_wait_seconds(resp: httpx.Response) -> float:
    """Extract wait time from Retry-After header, or use default."""
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return max(float(raw), 1.0)
        except ValueError:
            pass
    return _DEFAULT_RETRY_WAIT


def _raise_for_status(resp: httpx.Response) -> None:
    """Like resp.raise_for_status() but raises GitHubAuthError for 401/403."""
    if resp.status_code in (401, 403):
        body_preview = resp.text[:300] if resp.text else "(empty)"
        raise GitHubAuthError(
            f"GitHub auth error {resp.status_code} for {resp.request.url}: {body_preview}",
            request=resp.request,
            response=resp,
        )
    resp.raise_for_status()


class GitHubClient:
    """Thin async wrapper around the GitHub REST API."""

    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com") -> None:
        self._token = token or ""
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

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

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        """Send a request with automatic retry on secondary rate limits and 429s.

        If raise_for_status is False, the raw response is returned without
        checking the status code (caller is responsible for handling errors).
        """
        client = await self._ensure_client()
        for attempt in range(_MAX_RETRIES):
            kwargs: dict[str, Any] = {}
            if params is not None:
                kwargs["params"] = params
            if json is not None:
                kwargs["json"] = json

            resp = await client.request(method, url, **kwargs)

            if resp.status_code == 429 or (
                resp.status_code == 403 and _is_secondary_rate_limit(resp)
            ):
                wait = _retry_wait_seconds(resp)
                logger.warning(
                    f"GitHub secondary rate limit hit ({resp.status_code}) for {url}, "
                    f"retrying in {wait}s (attempt {attempt + 1}/{_MAX_RETRIES})"
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(wait)
                    continue
                # Last attempt exhausted, fall through to raise

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

    async def _patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("PATCH", path, json=json)
        return resp.json()

    async def _post_json(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("POST", path, json=json)
        return resp.json()

    async def _delete_json(self, path: str, json: dict[str, Any] | None = None) -> Any:
        resp = await self._request_with_retry("DELETE", path, json=json)
        return resp.json()

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
