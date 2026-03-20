"""Tests for sync service auth health tracking."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.models.tables import GitHubAccount, TrackedRepo
from src.services.github_client import AuthErrorType, GitHubAuthError
from src.services.sync_service import SyncService


def _make_auth_error(
    status_code: int = 401,
    error_type: AuthErrorType = AuthErrorType.token_expired,
    message: str = "Bad credentials",
) -> GitHubAuthError:
    """Build a GitHubAuthError with a fake request/response."""
    request = httpx.Request("GET", "https://api.github.com/test")
    response = httpx.Response(
        status_code,
        json={"message": message},
        headers={"content-type": "application/json"},
        request=request,
    )
    return GitHubAuthError(
        f"auth error {status_code}: {message}",
        request=request,
        response=response,
        error_type=error_type,
    )


class TestRecordRepoError:
    """Test _record_repo_error updates models correctly."""

    @pytest.mark.asyncio
    async def test_sets_repo_error_columns(self):
        svc = SyncService()
        mock_repo = MagicMock(spec=TrackedRepo)
        mock_repo.last_sync_error = None
        mock_repo.last_sync_error_at = None

        with (
            patch("src.services.sync_service.async_session_factory") as mock_sf,
            patch("src.services.sync_service.broadcast_event"),
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = AsyncMock(return_value=mock_repo)
            mock_session.commit = AsyncMock()
            mock_sf.return_value = mock_session

            await svc._record_repo_error(
                repo_id=1,
                error_message="Bad credentials",
                account_id=None,
                error_type=AuthErrorType.token_expired,
            )

            assert mock_repo.last_sync_error == "Bad credentials"
            assert mock_repo.last_sync_error_at is not None

    @pytest.mark.asyncio
    async def test_updates_account_for_account_level_error(self):
        svc = SyncService()
        mock_repo = MagicMock(spec=TrackedRepo)
        mock_account = MagicMock(spec=GitHubAccount)
        mock_account.token_status = "ok"
        mock_account.login = "testuser"

        def get_side_effect(model, id_):
            if model is TrackedRepo:
                return mock_repo
            if model is GitHubAccount:
                return mock_account
            return None

        with (
            patch("src.services.sync_service.async_session_factory") as mock_sf,
            patch("src.services.sync_service.broadcast_event") as mock_broadcast,
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = AsyncMock(side_effect=get_side_effect)
            mock_session.commit = AsyncMock()
            mock_sf.return_value = mock_session

            await svc._record_repo_error(
                repo_id=1,
                error_message="Bad credentials",
                account_id=42,
                error_type=AuthErrorType.token_expired,
            )

            assert mock_account.token_status == "expired"
            assert mock_account.token_error == "Bad credentials"
            assert mock_account.token_checked_at is not None
            # SSE event broadcast on transition from ok to error
            mock_broadcast.assert_called_once()
            call_args = mock_broadcast.call_args
            assert call_args[0][0] == "auth_issue"
            assert call_args[0][1]["account_id"] == 42

    @pytest.mark.asyncio
    async def test_no_broadcast_when_already_errored(self):
        """No SSE event if account was already in error state."""
        svc = SyncService()
        mock_repo = MagicMock(spec=TrackedRepo)
        mock_account = MagicMock(spec=GitHubAccount)
        mock_account.token_status = "expired"  # Already errored
        mock_account.login = "testuser"

        def get_side_effect(model, id_):
            if model is TrackedRepo:
                return mock_repo
            if model is GitHubAccount:
                return mock_account
            return None

        with (
            patch("src.services.sync_service.async_session_factory") as mock_sf,
            patch("src.services.sync_service.broadcast_event") as mock_broadcast,
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = AsyncMock(side_effect=get_side_effect)
            mock_session.commit = AsyncMock()
            mock_sf.return_value = mock_session

            await svc._record_repo_error(
                repo_id=1,
                error_message="Bad credentials",
                account_id=42,
                error_type=AuthErrorType.token_expired,
            )

            mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_repo_level_error_does_not_update_account(self):
        """insufficient_scope doesn't update GitHubAccount status."""
        svc = SyncService()
        mock_repo = MagicMock(spec=TrackedRepo)

        with (
            patch("src.services.sync_service.async_session_factory") as mock_sf,
            patch("src.services.sync_service.broadcast_event"),
        ):
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = AsyncMock(return_value=mock_repo)
            mock_session.commit = AsyncMock()
            mock_sf.return_value = mock_session

            await svc._record_repo_error(
                repo_id=1,
                error_message="Insufficient permissions",
                account_id=42,
                error_type=AuthErrorType.insufficient_scope,
            )

            # session.get should only be called for TrackedRepo, not GitHubAccount
            calls = mock_session.get.call_args_list
            assert len(calls) == 1
            assert calls[0][0][0] is TrackedRepo


class TestRecordRepoSuccess:
    """Test _record_repo_success clears errors."""

    @pytest.mark.asyncio
    async def test_clears_error_and_sets_success_timestamp(self):
        svc = SyncService()
        mock_repo = MagicMock(spec=TrackedRepo)
        mock_repo.last_sync_error = "Previous error"
        mock_repo.last_sync_error_at = datetime.now(UTC)
        mock_repo.full_name = "org/repo"

        with patch("src.services.sync_service.async_session_factory") as mock_sf:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = AsyncMock(return_value=mock_repo)
            mock_session.commit = AsyncMock()
            mock_sf.return_value = mock_session

            await svc._record_repo_success(repo_id=1)

            assert mock_repo.last_sync_error is None
            assert mock_repo.last_sync_error_at is None
            assert mock_repo.last_successful_sync_at is not None


class TestErrorTypeMapping:
    """Test the error type to status mapping."""

    def test_all_error_types_mapped(self):
        """Every AuthErrorType has a status string."""
        for error_type in AuthErrorType:
            assert error_type in SyncService._ERROR_TYPE_TO_STATUS

    def test_account_level_errors_are_subset(self):
        """Account-level errors should be a subset of all error types."""
        assert SyncService._ACCOUNT_LEVEL_ERRORS.issubset(set(AuthErrorType))
