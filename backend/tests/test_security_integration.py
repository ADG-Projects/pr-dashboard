"""Security integration tests.

Covers: password gate middleware, GitHub cookie lifecycle (tampering/expiry),
cross-user isolation for ADO and GitHub accounts, and dev mode gating.
"""

from unittest.mock import patch

import pytest

from src.api.auth import COOKIE_NAME, GITHUB_COOKIE
from src.config.settings import settings
from tests.conftest import make_auth_cookie, make_password_cookie

# ── Password Gate Middleware ──────────────────────────────────


class TestPasswordGateMiddleware:
    """Verify the password gate blocks/allows requests based on the session cookie."""

    @pytest.mark.asyncio
    async def test_valid_password_cookie_allows_access(self, client):
        with patch.object(settings, "dashboard_password", "secret123"):
            cookie = make_password_cookie(expires_offset=3600)
            resp = await client.get(
                "/api/auth/me",
                cookies={COOKIE_NAME: cookie},
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_cookie_blocks_when_password_enabled(self, client):
        with patch.object(settings, "dashboard_password", "secret123"):
            resp = await client.get("/api/ado-accounts")
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_password_cookie_blocks(self, client):
        with patch.object(settings, "dashboard_password", "secret123"):
            cookie = make_password_cookie(expires_offset=-100)
            resp = await client.get(
                "/api/ado-accounts",
                cookies={COOKIE_NAME: cookie},
            )
            assert resp.status_code == 401


# ── GitHub Cookie Lifecycle ───────────────────────────────────


class TestGitHubCookieLifecycle:
    """Verify tampered or expired GitHub identity cookies are rejected."""

    @pytest.mark.asyncio
    async def test_tampered_github_cookie_returns_401(self, client):
        with patch.object(settings, "dashboard_password", ""):
            cookie = make_auth_cookie(user_id=1)
            # Flip the last character to tamper with the HMAC signature
            tampered = cookie[:-1] + ("b" if cookie[-1] != "b" else "a")

            resp = await client.get(
                "/api/ado-accounts",
                cookies={GITHUB_COOKIE: tampered},
            )
            # Password gate is off, so middleware passes.
            # get_github_user_id returns None for a tampered cookie -> 401
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_github_cookie_returns_401(self, client):
        with patch.object(settings, "dashboard_password", ""):
            cookie = make_auth_cookie(user_id=1, expires_offset=-100)
            resp = await client.get(
                "/api/ado-accounts",
                cookies={GITHUB_COOKIE: cookie},
            )
            assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_cookie_nonexistent_user_returns_empty(self, client):
        """A valid cookie for a user_id not in the DB.

        get_github_user_id returns the ID; the ADO list query finds no rows.
        """
        with patch.object(settings, "dashboard_password", ""):
            cookie = make_auth_cookie(user_id=99999)
            resp = await client.get(
                "/api/ado-accounts",
                cookies={GITHUB_COOKIE: cookie},
            )
            assert resp.status_code == 200
            assert resp.json() == []


# ── Cross-User Isolation ──────────────────────────────────────


class TestCrossUserIsolation:
    """Verify users cannot access each other's accounts."""

    @pytest.mark.asyncio
    async def test_user_cannot_list_other_users_ado_accounts(self, client, seed_two_users):
        with patch.object(settings, "dashboard_password", ""):
            data = seed_two_users
            cookie_b = make_auth_cookie(data["user_b"].id)
            resp = await client.get(
                "/api/ado-accounts",
                cookies={GITHUB_COOKIE: cookie_b},
            )
            assert resp.status_code == 200
            accounts = resp.json()
            org_urls = [a["org_url"] for a in accounts]
            assert "https://dev.azure.com/orgA" not in org_urls
            assert all(a["org_url"] == "https://dev.azure.com/orgB" for a in accounts)

    @pytest.mark.asyncio
    async def test_user_cannot_delete_other_users_ado_account(self, client, seed_two_users):
        with patch.object(settings, "dashboard_password", ""):
            data = seed_two_users
            cookie_a = make_auth_cookie(data["user_a"].id)
            resp = await client.delete(
                f"/api/ado-accounts/{data['ado_b'].id}",
                cookies={GITHUB_COOKIE: cookie_a},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_user_cannot_delete_other_users_github_account(self, client, seed_two_users):
        with patch.object(settings, "dashboard_password", ""):
            data = seed_two_users
            cookie_a = make_auth_cookie(data["user_a"].id)
            resp = await client.delete(
                f"/api/accounts/{data['gh_b'].id}",
                cookies={GITHUB_COOKIE: cookie_a},
            )
            assert resp.status_code == 404


# ── Dev Mode Gating ───────────────────────────────────────────


class TestDevModeGating:
    """Verify dev-only endpoints are hidden when DEV_MODE is off."""

    @pytest.mark.asyncio
    async def test_dev_login_404_when_disabled(self, client):
        with (
            patch.object(settings, "dev_mode", False),
            patch.object(settings, "dashboard_password", ""),
        ):
            resp = await client.post("/api/auth/dev-login/1")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_dev_users_404_when_disabled(self, client):
        with (
            patch.object(settings, "dev_mode", False),
            patch.object(settings, "dashboard_password", ""),
        ):
            resp = await client.get("/api/auth/dev-users")
            assert resp.status_code == 404
