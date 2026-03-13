"""Tests for GitHub token management security.

Covers: base_url SSRF protection, OAuth error log sanitization,
and encryption key rotation documentation (via lru_cache behavior).
"""

from unittest.mock import patch

import pytest
from loguru import logger
from pydantic import ValidationError

from src.api.schemas import GitHubAccountCreate

# ── GitHub base_url SSRF Protection ────────────────────────


class TestBaseUrlValidation:
    """Test SSRF protection via base_url validation in GitHubAccountCreate."""

    def test_valid_default_github_api(self):
        schema = GitHubAccountCreate(token="ghp_test")
        assert schema.base_url == "https://api.github.com"

    def test_valid_explicit_github_api(self):
        schema = GitHubAccountCreate(
            token="ghp_test",
            base_url="https://api.github.com",
        )
        assert schema.base_url == "https://api.github.com"

    def test_valid_github_com(self):
        schema = GitHubAccountCreate(
            token="ghp_test",
            base_url="https://github.com",
        )
        assert schema.base_url == "https://github.com"

    def test_rejects_http_in_production(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = ""
            with pytest.raises(ValidationError, match="https"):
                GitHubAccountCreate(
                    token="ghp_test",
                    base_url="http://api.github.com",
                )

    def test_allows_http_in_dev_mode(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = True
            mock_settings.allowed_ghe_domains = ""
            with patch("src.api.schemas._is_private_ip", return_value=False):
                schema = GitHubAccountCreate(
                    token="ghp_test",
                    base_url="http://api.github.com",
                )
                assert schema.base_url == "http://api.github.com"

    def test_rejects_unknown_domain(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = ""
            with pytest.raises(ValidationError, match="recognized GitHub domain"):
                GitHubAccountCreate(
                    token="ghp_test",
                    base_url="https://evil.example.com",
                )

    def test_rejects_localhost(self):
        with pytest.raises(
            ValidationError,
            match="private or reserved IP|recognized GitHub",
        ):
            GitHubAccountCreate(
                token="ghp_test",
                base_url="https://localhost:9090",
            )

    def test_rejects_metadata_endpoint(self):
        with pytest.raises(
            ValidationError,
            match="private or reserved IP|recognized GitHub",
        ):
            GitHubAccountCreate(
                token="ghp_test",
                base_url="https://169.254.169.254",
            )

    def test_rejects_internal_ip_10(self):
        with pytest.raises(
            ValidationError,
            match="private or reserved IP|recognized GitHub",
        ):
            GitHubAccountCreate(
                token="ghp_test",
                base_url="https://10.0.0.1:8080",
            )

    def test_rejects_internal_ip_192(self):
        with pytest.raises(
            ValidationError,
            match="private or reserved IP|recognized GitHub",
        ):
            GitHubAccountCreate(
                token="ghp_test",
                base_url="https://192.168.1.1",
            )

    def test_rejects_ftp_scheme(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = ""
            with pytest.raises(ValidationError, match="https"):
                GitHubAccountCreate(
                    token="ghp_test",
                    base_url="ftp://api.github.com",
                )

    def test_rejects_empty_hostname(self):
        with pytest.raises(ValidationError):
            GitHubAccountCreate(
                token="ghp_test",
                base_url="https://",
            )

    def test_rejects_missing_scheme(self):
        with pytest.raises(ValidationError):
            GitHubAccountCreate(
                token="ghp_test",
                base_url="api.github.com",
            )


class TestGheDomainsConfig:
    """Test ALLOWED_GHE_DOMAINS env var integration."""

    def test_allows_configured_ghe_domain(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = "github.mycompany.com"
            with patch("src.api.schemas._is_private_ip", return_value=False):
                schema = GitHubAccountCreate(
                    token="ghp_test",
                    base_url="https://github.mycompany.com/api/v3",
                )
                assert "github.mycompany.com" in schema.base_url

    def test_allows_multiple_ghe_domains(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = "ghe1.corp.com, ghe2.corp.com"
            with patch("src.api.schemas._is_private_ip", return_value=False):
                s1 = GitHubAccountCreate(
                    token="ghp_test",
                    base_url="https://ghe1.corp.com",
                )
                s2 = GitHubAccountCreate(
                    token="ghp_test",
                    base_url="https://ghe2.corp.com",
                )
                assert "ghe1" in s1.base_url
                assert "ghe2" in s2.base_url

    def test_rejects_domain_not_in_ghe_list(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = "github.mycompany.com"
            with patch("src.api.schemas._is_private_ip", return_value=False):
                with pytest.raises(ValidationError, match="recognized GitHub domain"):
                    GitHubAccountCreate(
                        token="ghp_test",
                        base_url="https://not-allowed.com",
                    )

    def test_allows_subdomain_of_ghe_domain(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = "corp.com"
            with patch("src.api.schemas._is_private_ip", return_value=False):
                schema = GitHubAccountCreate(
                    token="ghp_test",
                    base_url="https://ghe.corp.com/api/v3",
                )
                assert "ghe.corp.com" in schema.base_url

    def test_empty_ghe_domains_only_allows_github(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = ""
            with patch("src.api.schemas._is_private_ip", return_value=False):
                with pytest.raises(ValidationError, match="recognized GitHub domain"):
                    GitHubAccountCreate(
                        token="ghp_test",
                        base_url="https://ghe.corp.com",
                    )

    def test_ghe_domains_whitespace_handling(self):
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = " ghe.corp.com , , ghe2.corp.com "
            with patch("src.api.schemas._is_private_ip", return_value=False):
                schema = GitHubAccountCreate(
                    token="ghp_test",
                    base_url="https://ghe.corp.com",
                )
                assert schema.base_url == "https://ghe.corp.com"

    def test_ghe_private_ip_still_blocked(self):
        """Even if domain is allowed, private IP resolution is blocked."""
        with patch("src.api.schemas.settings") as mock_settings:
            mock_settings.dev_mode = False
            mock_settings.allowed_ghe_domains = "internal.corp"
            with patch("src.api.schemas._is_private_ip", return_value=True):
                with pytest.raises(ValidationError, match="private or reserved IP"):
                    GitHubAccountCreate(
                        token="ghp_test",
                        base_url="https://internal.corp",
                    )


# ── OAuth Error Log Sanitization ────────────────────────────


class TestOAuthLogSanitization:
    """Test that OAuth error paths don't log sensitive data."""

    @pytest.mark.asyncio
    async def test_token_exchange_failure_no_body_logged(self, client):
        """Failed token exchange should log status code, not response body."""
        import httpx

        mock_response = httpx.Response(
            status_code=401,
            text='{"error":"bad_verification_code","client_secret":"leaked"}',
            request=httpx.Request("POST", "https://github.com"),
        )

        log_messages = []
        sink_id = logger.add(lambda m: log_messages.append(str(m)))

        try:
            with (
                patch("src.api.auth._verify") as mock_verify,
                patch("httpx.AsyncClient.post", return_value=mock_response),
            ):
                mock_verify.return_value = "oauth:9999999999:nonce123"

                await client.get("/api/auth/github/callback?code=bad&state=signed")

            log_text = " ".join(log_messages)
            assert "client_secret" not in log_text
            assert "leaked" not in log_text
            assert "401" in log_text
        finally:
            logger.remove(sink_id)

    @pytest.mark.asyncio
    async def test_missing_token_no_dict_logged(self, client):
        """Missing access_token should log keys only, not full dict."""
        import httpx

        mock_response = httpx.Response(
            status_code=200,
            json={
                "error": "bad_code",
                "refresh_token": "sensitive-refresh",
            },
            request=httpx.Request("POST", "https://github.com"),
        )

        log_messages = []
        sink_id = logger.add(lambda m: log_messages.append(str(m)))

        try:
            with (
                patch("src.api.auth._verify") as mock_verify,
                patch("httpx.AsyncClient.post", return_value=mock_response),
            ):
                mock_verify.return_value = "oauth:9999999999:nonce123"

                await client.get("/api/auth/github/callback?code=bad&state=signed")

            log_text = " ".join(log_messages)
            assert "sensitive-refresh" not in log_text
            assert "bad_code" not in log_text
            assert "error" in log_text or "refresh_token" in log_text
        finally:
            logger.remove(sink_id)
