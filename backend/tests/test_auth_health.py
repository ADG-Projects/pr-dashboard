"""Tests for auth health API endpoints."""

from src.api.auth import _remediation_for_status


class TestRemediationMapping:
    """Test that error types map to correct remediation actions."""

    def test_expired_maps_to_re_authenticate(self):
        rem = _remediation_for_status("expired", account_id=1)
        assert rem["action"] == "re_authenticate"
        assert "/api/auth/github" in rem["url"]

    def test_revoked_maps_to_re_authenticate(self):
        rem = _remediation_for_status("revoked", account_id=1)
        assert rem["action"] == "re_authenticate"

    def test_decrypt_failed_maps_to_re_authenticate(self):
        rem = _remediation_for_status("decrypt_failed", account_id=1)
        assert rem["action"] == "re_authenticate"
        assert "encryption key" in rem["description"].lower()

    def test_sso_required_maps_to_authorize_sso(self):
        rem = _remediation_for_status("sso_required", account_id=1)
        assert rem["action"] == "authorize_sso"
        assert "sso" in rem["description"].lower()

    def test_insufficient_scope_maps_to_check_permissions(self):
        rem = _remediation_for_status("insufficient_scope", account_id=1)
        assert rem["action"] == "check_permissions"
        assert "scope" in rem["description"].lower()

    def test_unknown_status_returns_fallback(self):
        rem = _remediation_for_status("something_weird", account_id=1)
        assert rem["action"] == "check_permissions"

    def test_no_account_id_omits_url(self):
        rem = _remediation_for_status("expired")
        assert rem.get("url") is None
