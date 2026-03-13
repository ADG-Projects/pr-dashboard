"""Tests for ADO token management security fixes.

Covers: SSRF URL validation, token traceback leakage, auth consistency,
and separate encryption key support.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import GITHUB_COOKIE
from src.api.schemas import AdoAccountCreate, _is_private_ip
from src.models.tables import User
from src.services.crypto import _get_fernet, decrypt_token, encrypt_token
from tests.conftest import make_auth_cookie

# ── Fixtures ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_user(db_session: AsyncSession):
    user = User(github_id=42, login="testuser", name="Test User")
    db_session.add(user)
    await db_session.commit()
    return user


# ── SSRF URL Validation ─────────────────────────────────────


class TestOrgUrlValidation:
    """Test SSRF protection via org_url validation in AdoAccountCreate."""

    def test_valid_https_dev_azure(self):
        schema = AdoAccountCreate(
            token="pat-token",
            org_url="https://dev.azure.com/myorg",
            project="MyProject",
        )
        assert schema.org_url == "https://dev.azure.com/myorg"

    def test_valid_https_visualstudio(self):
        schema = AdoAccountCreate(
            token="pat-token",
            org_url="https://myorg.visualstudio.com",
            project="MyProject",
        )
        assert schema.org_url == "https://myorg.visualstudio.com"

    def test_rejects_http_in_production(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            with pytest.raises(ValidationError, match="https"):
                AdoAccountCreate(
                    token="pat-token",
                    org_url="http://dev.azure.com/myorg",
                    project="MyProject",
                )

    def test_allows_http_in_dev_mode(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = True
            with patch("src.api.schemas._is_private_ip", return_value=False):
                schema = AdoAccountCreate(
                    token="pat-token",
                    org_url="http://dev.azure.com/myorg",
                    project="MyProject",
                )
                assert schema.org_url == "http://dev.azure.com/myorg"

    def test_rejects_unknown_domain(self):
        with pytest.raises(ValidationError, match="recognized Azure DevOps domain"):
            AdoAccountCreate(
                token="pat-token",
                org_url="https://evil.example.com",
                project="MyProject",
            )

    def test_rejects_localhost(self):
        with pytest.raises(ValidationError, match="private or reserved IP|recognized Azure DevOps"):
            AdoAccountCreate(
                token="pat-token",
                org_url="https://localhost:9090",
                project="MyProject",
            )

    def test_rejects_metadata_endpoint(self):
        with pytest.raises(ValidationError, match="private or reserved IP|recognized Azure DevOps"):
            AdoAccountCreate(
                token="pat-token",
                org_url="https://169.254.169.254",
                project="MyProject",
            )

    def test_rejects_internal_ip(self):
        with pytest.raises(ValidationError, match="private or reserved IP|recognized Azure DevOps"):
            AdoAccountCreate(
                token="pat-token",
                org_url="https://10.0.0.1:8080",
                project="MyProject",
            )

    def test_rejects_missing_scheme(self):
        with pytest.raises(ValidationError):
            AdoAccountCreate(
                token="pat-token",
                org_url="dev.azure.com/myorg",
                project="MyProject",
            )

    def test_rejects_ftp_scheme(self):
        with pytest.raises(ValidationError, match="https"):
            AdoAccountCreate(
                token="pat-token",
                org_url="ftp://dev.azure.com/myorg",
                project="MyProject",
            )

    def test_rejects_empty_hostname(self):
        with pytest.raises(ValidationError):
            AdoAccountCreate(
                token="pat-token",
                org_url="https://",
                project="MyProject",
            )


class TestIsPrivateIp:
    """Test the _is_private_ip helper directly."""

    def test_localhost_is_private(self):
        assert _is_private_ip("localhost") is True

    def test_loopback_is_private(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_link_local_is_private(self):
        assert _is_private_ip("169.254.169.254") is True

    def test_rfc1918_is_private(self):
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("192.168.1.1") is True

    def test_unresolvable_is_not_private(self):
        # Can't resolve, so getaddrinfo raises, returns False
        assert _is_private_ip("this-host-does-not-exist.invalid") is False

    def test_public_domain_is_not_private(self):
        assert _is_private_ip("dev.azure.com") is False


# ── Auth Consistency ─────────────────────────────────────────


class TestAdoEndpointAuth:
    """Test that all ADO account endpoints enforce authentication consistently."""

    @pytest.mark.asyncio
    async def test_list_returns_401_without_auth(self, client):
        resp = await client.get("/api/ado-accounts")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_post_returns_401_without_auth(self, client):
        resp = await client.post(
            "/api/ado-accounts",
            json={
                "token": "pat",
                "org_url": "https://dev.azure.com/myorg",
                "project": "Proj",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_returns_401_without_auth(self, client):
        resp = await client.delete("/api/ado-accounts/1")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_returns_200_with_auth(self, client, seed_user):
        cookie = make_auth_cookie(seed_user.github_id)
        resp = await client.get(
            "/api/ado-accounts",
            cookies={GITHUB_COOKIE: cookie},
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ── Token Traceback Leakage ──────────────────────────────────


class TestTokenLeakage:
    """Test that the plaintext token doesn't leak in tracebacks."""

    @pytest.mark.asyncio
    async def test_validate_ado_token_no_chain(self, client, seed_user):
        """The HTTPException raised on validation failure should not chain
        the original exception (which may contain the token)."""
        cookie = make_auth_cookie(seed_user.github_id)
        # Use a valid ADO domain but mock httpx to fail
        with patch("src.api.ado_accounts.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = ConnectionError("connection refused")
            mock_client_cls.return_value = mock_client

            resp = await client.post(
                "/api/ado-accounts",
                json={
                    "token": "super-secret-pat-token",
                    "org_url": "https://dev.azure.com/myorg",
                    "project": "Proj",
                },
                cookies={GITHUB_COOKIE: cookie},
            )
            assert resp.status_code == 400
            body = resp.json()
            assert "super-secret-pat-token" not in str(body)

    @pytest.mark.asyncio
    async def test_link_success_creates_account(self, client, seed_user):
        """Successful token validation creates an account."""
        cookie = make_auth_cookie(seed_user.github_id)

        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None

        with patch("src.api.ado_accounts.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_cls.return_value = mock_client

            resp = await client.post(
                "/api/ado-accounts",
                json={
                    "token": "valid-pat",
                    "org_url": "https://dev.azure.com/myorg",
                    "project": "Proj",
                },
                cookies={GITHUB_COOKIE: cookie},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["org_url"] == "https://dev.azure.com/myorg"
            assert data["has_token"] is True
            # Token must never appear in the response
            assert "valid-pat" not in str(data)


# ── Separate Encryption Key ─────────────────────────────────


class TestEncryptionKeyFallback:
    """Test that encryption_key falls back to secret_key."""

    def test_encrypt_decrypt_roundtrip(self):
        _get_fernet.cache_clear()
        plaintext = "my-secret-pat"
        encrypted = encrypt_token(plaintext)
        assert encrypted != plaintext
        assert decrypt_token(encrypted) == plaintext

    def test_separate_encryption_key(self):
        """When ENCRYPTION_KEY is set, it should be used instead of SECRET_KEY."""
        _get_fernet.cache_clear()
        # Encrypt with default key
        encrypted_default = encrypt_token("test-token")

        # Patch to use a different encryption key
        with patch("src.services.crypto.settings") as mock_settings:
            mock_settings.encryption_key = "different-encryption-key"
            mock_settings.secret_key = "change-me-in-production"
            _get_fernet.cache_clear()
            encrypted_different = encrypt_token("test-token")

        _get_fernet.cache_clear()

        # Tokens encrypted with different keys should differ
        # (the Fernet ciphertext includes the key in its MAC)
        assert decrypt_token(encrypted_default) == "test-token"
        # The one encrypted with a different key can't be decrypted with the default
        assert decrypt_token(encrypted_different) is None

    def test_fallback_to_secret_key_when_no_encryption_key(self):
        """When encryption_key is None, secret_key is used."""
        _get_fernet.cache_clear()
        with patch("src.services.crypto.settings") as mock_settings:
            mock_settings.encryption_key = None
            mock_settings.secret_key = "change-me-in-production"
            _get_fernet.cache_clear()
            encrypted = encrypt_token("test-token")

        _get_fernet.cache_clear()
        # Should decrypt fine since the default secret_key is the same
        assert decrypt_token(encrypted) == "test-token"
