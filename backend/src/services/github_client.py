"""Async GitHub API client using httpx."""

from datetime import datetime
from typing import Any

import httpx

from src.config.settings import settings

BASE_URL = "https://api.github.com"


class GitHubClient:
    """Thin async wrapper around the GitHub REST API."""

    def __init__(self, token: str | None = None) -> None:
        self._token = token or settings.github_token
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
                base_url=BASE_URL, headers=headers, timeout=30.0
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        client = await self._ensure_client()
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_paginated(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated GitHub API endpoint."""
        client = await self._ensure_client()
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results: list[dict[str, Any]] = []

        url: str | None = path
        while url:
            resp = await client.get(url, params=params if url == path else None)
            resp.raise_for_status()
            results.extend(resp.json())
            # Follow Link: <...>; rel="next"
            link = resp.headers.get("link", "")
            url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip(" <>")
                    break
        return results

    # ── Public API ──────────────────────────────────────────────

    async def list_open_pulls(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List all open PRs for a repo."""
        return await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": "open", "sort": "updated", "direction": "desc"},
        )

    async def get_pull(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Get full PR detail (includes mergeable_state, diff stats)."""
        return await self._get(f"/repos/{owner}/{repo}/pulls/{number}")

    async def get_check_runs(
        self, owner: str, repo: str, ref: str
    ) -> list[dict[str, Any]]:
        """Get check runs for a commit ref."""
        data = await self._get(f"/repos/{owner}/{repo}/commits/{ref}/check-runs")
        return data.get("check_runs", [])

    async def get_reviews(
        self, owner: str, repo: str, number: int
    ) -> list[dict[str, Any]]:
        """Get reviews for a PR."""
        return await self._get_paginated(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews"
        )

    async def list_org_repos(self, org: str) -> list[dict[str, Any]]:
        """List all repos in an organization."""
        return await self._get_paginated(
            f"/orgs/{org}/repos",
            params={"type": "all", "sort": "full_name", "direction": "asc"},
        )

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        """Get repo metadata (for default_branch, etc.)."""
        return await self._get(f"/repos/{owner}/{repo}")

    async def get_rate_limit(self) -> dict[str, Any]:
        """Check current rate limit status."""
        return await self._get("/rate_limit")


def parse_gh_datetime(value: str | None) -> datetime | None:
    """Parse GitHub's ISO 8601 datetime string."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
