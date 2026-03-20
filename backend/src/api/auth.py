"""Authentication routes and middleware (HMAC-signed cookies + GitHub OAuth)."""

import asyncio
import hashlib
import hmac
import secrets
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from loguru import logger
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.api.schemas import AuthStatus, LoginRequest
from src.config.settings import settings
from src.db.engine import async_session_factory
from src.models.tables import GitHubAccount, User
from src.services.crypto import encrypt_token
from src.services.discovery import discover_spaces_for_account
from src.services.events import broadcast_event
from src.services.repo_cleanup import delete_orphaned_repos

OAUTH_STATE_MAX_AGE = 600  # 10 minutes

# Strong references to background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    """Add task to the set and register cleanup callback."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


router = APIRouter(prefix="/api/auth", tags=["auth"])

# Paths that don't require authentication
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/github/callback",
    "/api/auth/dev-users",
    "/api/health",
}
# (path, method) pairs that are public only for specific HTTP methods
PUBLIC_PATH_METHODS = {
    ("/api/auth/me", "GET"),
}
PUBLIC_PREFIXES = ("/api/auth/dev-login/", "/api/webhooks/github")


class AuthMiddleware(BaseHTTPMiddleware):
    """Block unauthenticated requests to /api/* routes (except public paths)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        path = request.url.path
        if (
            path.startswith("/api/")
            and path not in PUBLIC_PATHS
            and (path, request.method) not in PUBLIC_PATH_METHODS
            and not path.startswith(PUBLIC_PREFIXES)
            and not is_authenticated(request)
        ):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        return await call_next(request)


# ── Password gate (unchanged) ──────────────────────────────

COOKIE_NAME = "dashboard_session"
GITHUB_COOKIE = "github_user"


def _sign(payload: str) -> str:
    sig = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify(token: str) -> str | None:
    if "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return payload


def is_authenticated(request: Request) -> bool:
    """Check if the request has a valid session cookie."""
    if not settings.dashboard_password:
        return True  # Auth disabled
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    payload = _verify(token)
    if payload is None:
        return False
    try:
        expires = int(payload)
    except ValueError:
        return False
    return time.time() < expires


def get_github_user_id(request: Request) -> int | None:
    """Extract GitHub user ID from the identity cookie."""
    token = request.cookies.get(GITHUB_COOKIE)
    if not token:
        return None
    payload = _verify(token)
    if payload is None:
        return None
    try:
        user_id_str, expires_str = payload.split(":", 1)
        if time.time() >= int(expires_str):
            return None
        return int(user_id_str)
    except (ValueError, IndexError):
        return None


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> AuthStatus:
    """Authenticate with password and set session cookie."""
    if not settings.dashboard_password:
        return AuthStatus(authenticated=True, auth_enabled=False)

    if not hmac.compare_digest(body.password, settings.dashboard_password):
        return JSONResponse(  # type: ignore[return-value]
            status_code=401, content={"detail": "Invalid password"}
        )

    expires = int(time.time()) + settings.session_max_age_seconds
    token = _sign(str(expires))
    is_https = not settings.frontend_url or settings.frontend_url.startswith("https")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return AuthStatus(authenticated=True, auth_enabled=True)


@router.get("/me")
async def auth_status(request: Request) -> AuthStatus:
    """Check current auth status."""
    user_info = None
    user_id = get_github_user_id(request)
    if user_id:
        async with async_session_factory() as session:
            user = await session.get(User, user_id)
            if user:
                user_info = {
                    "id": user.id,
                    "login": user.login,
                    "name": user.name,
                    "avatar_url": user.avatar_url,
                }
    return AuthStatus(
        authenticated=is_authenticated(request),
        auth_enabled=bool(settings.dashboard_password),
        oauth_configured=bool(settings.github_oauth_client_id),
        user=user_info,
    )


@router.delete("/me")
async def delete_my_account(request: Request, response: Response):
    """Delete the current user's account.

    Cascades to GitHubAccount and RepoTracker; SET NULLs PR assignee refs.
    """
    user_id = get_github_user_id(request)
    if not user_id:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if not user:
            return JSONResponse(status_code=404, content={"detail": "User not found"})
        await session.delete(user)
        await session.flush()  # Trigger CASCADE deletes for RepoTracker rows
        deleted = await delete_orphaned_repos(session)
        if deleted:
            logger.info(f"Deleted {deleted} orphaned repo(s) after deleting user {user_id}")
        await session.commit()

    response.delete_cookie(GITHUB_COOKIE, path="/")
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "deleted"}


@router.post("/logout")
async def logout(response: Response) -> AuthStatus:
    """Clear session cookie."""
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(GITHUB_COOKIE, path="/")
    return AuthStatus(authenticated=False, auth_enabled=bool(settings.dashboard_password))


# ── GitHub OAuth ────────────────────────────────────────────

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


@router.get("/github")
async def github_oauth_start(request: Request, link: bool = False) -> RedirectResponse:
    """Redirect to GitHub OAuth authorization page.

    Pass ?link=true to add a new GitHub account to the current user
    instead of signing in as a different user.
    """
    if not settings.github_oauth_client_id:
        return JSONResponse(  # type: ignore[return-value]
            status_code=400,
            content={"detail": "GitHub OAuth not configured"},
        )
    # Encode link mode, timestamp, and nonce in the OAuth state
    nonce = secrets.token_urlsafe(16)
    mode = "oauth_link" if link else "oauth"
    state_payload = f"{mode}:{int(time.time())}:{nonce}"
    params = {
        "client_id": settings.github_oauth_client_id,
        "scope": "repo read:org admin:repo_hook",
        "state": _sign(state_payload),
    }
    from urllib.parse import urlencode

    url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    return RedirectResponse(url=url)


@router.get("/github/callback")
async def github_oauth_callback(code: str, state: str, request: Request) -> RedirectResponse:
    """Exchange OAuth code for token, upsert user + account, auto-discover spaces."""
    base = settings.frontend_url or ""

    # Verify state — includes mode, timestamp, and nonce
    state_payload = _verify(state)
    if not state_payload:
        return RedirectResponse(url=f"{base}/?error=invalid_state")

    try:
        mode, ts_str, _nonce = state_payload.split(":", 2)
    except ValueError:
        return RedirectResponse(url=f"{base}/?error=invalid_state")

    if mode not in ("oauth", "oauth_link"):
        return RedirectResponse(url=f"{base}/?error=invalid_state")

    if time.time() - int(ts_str) > OAUTH_STATE_MAX_AGE:
        return RedirectResponse(url=f"{base}/?error=state_expired")

    link_mode = mode == "oauth_link"

    # Exchange code for access token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            json={
                "client_id": settings.github_oauth_client_id,
                "client_secret": settings.github_oauth_client_secret,
                "code": code,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.error(f"GitHub OAuth token exchange failed with status {resp.status_code}")
            return RedirectResponse(url=f"{base}/?error=token_exchange_failed")

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            logger.error(f"No access_token in response keys: {list(token_data.keys())}")
            return RedirectResponse(url=f"{base}/?error=no_token")

        # Fetch user info
        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if user_resp.status_code != 200:
            logger.error(f"GitHub user fetch failed: {user_resp.text}")
            return RedirectResponse(url=f"{base}/?error=user_fetch_failed")

        gh_user = user_resp.json()

    # Upsert user + github account in DB
    from datetime import UTC, datetime

    encrypted = encrypt_token(access_token)
    linked_to_existing = False

    async with async_session_factory() as session:
        # In link mode, attach to the currently signed-in user
        # In sign-in mode, create/find user from GitHub identity
        # Auto-upgrade to link mode if user already has a valid session cookie
        existing_user_id = get_github_user_id(request)
        if existing_user_id:
            existing_user = await session.get(User, existing_user_id)
            if not existing_user:
                # Stale cookie (e.g. DB was reset), fall back to sign-in mode
                existing_user_id = None
                logger.info("Ignoring stale session cookie, user no longer exists")
            elif not link_mode:
                link_mode = True
                logger.info(f"Auto-linking: user {existing_user_id} already authenticated")

        if existing_user_id and link_mode:
            # Link mode: add this GitHub account to the existing user
            user = existing_user
        else:
            # Sign-in mode: find/create user from GitHub identity
            result = await session.execute(select(User).where(User.github_id == gh_user["id"]))
            user = result.scalar_one_or_none()

            if user is None:
                # Check if this GitHub identity is already linked as a GitHubAccount
                result = await session.execute(
                    select(GitHubAccount).where(GitHubAccount.github_id == gh_user["id"])
                )
                existing_account = result.scalars().first()
                if existing_account:
                    user = await session.get(User, existing_account.user_id)
                    linked_to_existing = True
                    logger.info(
                        f"OAuth identity {gh_user['login']} already linked to user {user.id}, "
                        f"signing in as existing user"
                    )

            if user is None:
                user = User(
                    github_id=gh_user["id"],
                    login=gh_user["login"],
                    name=gh_user.get("name"),
                    avatar_url=gh_user.get("avatar_url"),
                    last_login_at=datetime.now(UTC),
                )
                session.add(user)
                await session.flush()
            else:
                user.login = gh_user["login"]
                user.name = gh_user.get("name")
                user.avatar_url = gh_user.get("avatar_url")
                user.last_login_at = datetime.now(UTC)

        # Upsert GitHubAccount (linked to the resolved user)
        result = await session.execute(
            select(GitHubAccount).where(
                GitHubAccount.user_id == user.id,
                GitHubAccount.github_id == gh_user["id"],
            )
        )
        account = result.scalar_one_or_none()

        if account is None:
            account = GitHubAccount(
                user_id=user.id,
                github_id=gh_user["id"],
                login=gh_user["login"],
                avatar_url=gh_user.get("avatar_url"),
                encrypted_token=encrypted,
                base_url="https://api.github.com",
                last_login_at=datetime.now(UTC),
            )
            session.add(account)
        else:
            account.login = gh_user["login"]
            account.avatar_url = gh_user.get("avatar_url")
            account.last_login_at = datetime.now(UTC)
            account.is_active = True
            if linked_to_existing:
                # Preserve the existing token (e.g. PAT with SSO authorization).
                # The OAuth token is only used for authentication, not API access.
                logger.info(
                    f"Preserving existing token for account {account.id} "
                    f"({account.login}) during OAuth merge"
                )
            else:
                account.encrypted_token = encrypted

        await session.commit()
        await session.refresh(user)
        await session.refresh(account)
        user_id = user.id
        account_id = account.id

    # Auto-discover spaces (orgs + personal) in background, but skip when
    # signing in via an already-linked account since spaces are already set up
    # and the OAuth token may lack org SSO authorization.
    if not linked_to_existing:
        _track_task(asyncio.create_task(_discover_spaces_background(account_id)))
    else:
        logger.info(f"Skipping space discovery for linked account {account_id}")

    # Set identity cookie
    expires = int(time.time()) + settings.session_max_age_seconds
    cookie_payload = f"{user_id}:{expires}"
    token = _sign(cookie_payload)

    redirect_base = f"{settings.frontend_url}/" if settings.frontend_url else "/"
    redirect_url = f"{redirect_base}?linked_existing=true" if linked_to_existing else redirect_base
    is_https = not settings.frontend_url or settings.frontend_url.startswith("https")
    response = RedirectResponse(url=redirect_url)
    response.set_cookie(
        GITHUB_COOKIE,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=is_https,
        samesite="lax",
        path="/",
    )
    return response


async def _discover_spaces_background(account_id: int) -> None:
    """Run space discovery in background after OAuth login."""
    try:
        async with async_session_factory() as session:
            account = await session.get(GitHubAccount, account_id)
            if account:
                await discover_spaces_for_account(session, account)
                await session.commit()
                await broadcast_event("spaces_discovered", {"account_id": account_id})
    except Exception:
        logger.exception(f"Failed to auto-discover spaces for account {account_id}")
        await broadcast_event(
            "discovery_error",
            {"account_id": account_id, "error": f"Space discovery failed for account {account_id}"},
        )


# ── Auth Health ─────────────────────────────────────────────


def _remediation_for_status(token_status: str, *, account_id: int | None = None) -> dict:
    """Map a token_status string to a remediation action."""
    if token_status in ("expired", "revoked"):
        return {
            "action": "re_authenticate",
            "label": "Re-authenticate",
            "url": f"/api/auth/github?link=true&account_id={account_id}" if account_id else None,
            "description": "Your GitHub token is no longer valid. Please re-authenticate.",
        }
    if token_status == "decrypt_failed":
        return {
            "action": "re_authenticate",
            "label": "Re-authenticate",
            "url": f"/api/auth/github?link=true&account_id={account_id}" if account_id else None,
            "description": "Token cannot be decrypted. The server encryption key may have changed.",
        }
    if token_status == "sso_required":
        return {
            "action": "authorize_sso",
            "label": "Authorize SSO",
            "description": "Your token needs SSO/SAML authorization for this organization.",
        }
    if token_status == "insufficient_scope":
        return {
            "action": "check_permissions",
            "label": "Check permissions",
            "description": (
                "Your token may lack required scopes (repo, read:org). "
                "Re-authenticate or update your token."
            ),
        }
    return {
        "action": "check_permissions",
        "label": "Investigate",
        "description": f"Unexpected token status: {token_status}",
    }


@router.get("/health")
async def auth_health(request: Request):
    """Return auth health summary for the current user."""
    user_id = get_github_user_id(request)
    if not user_id:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    async with async_session_factory() as session:
        from src.models.tables import RepoTracker, Space, TrackedRepo

        # Get user's accounts with issues
        accounts = (
            (
                await session.execute(
                    select(GitHubAccount).where(
                        GitHubAccount.user_id == user_id,
                        GitHubAccount.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )

        account_issues = []
        for acct in accounts:
            if acct.token_status != "ok":
                # Find affected repos through spaces -> repo_trackers
                trackers = (
                    (
                        await session.execute(
                            select(RepoTracker)
                            .join(Space, RepoTracker.space_id == Space.id)
                            .join(TrackedRepo, RepoTracker.repo_id == TrackedRepo.id)
                            .where(
                                Space.github_account_id == acct.id,
                                TrackedRepo.is_active.is_(True),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                affected_repo_ids = [t.repo_id for t in trackers]
                affected_repos = []
                for rid in affected_repo_ids:
                    repo = await session.get(TrackedRepo, rid)
                    if repo:
                        affected_repos.append(repo.full_name)

                account_issues.append(
                    {
                        "id": acct.id,
                        "login": acct.login,
                        "token_status": acct.token_status,
                        "token_error": acct.token_error,
                        "token_checked_at": (
                            acct.token_checked_at.isoformat() if acct.token_checked_at else None
                        ),
                        "affected_repos": affected_repos,
                        "remediation": _remediation_for_status(
                            acct.token_status, account_id=acct.id
                        ),
                    }
                )

        # Get repos with sync errors (through user's trackers)
        user_trackers = (
            (await session.execute(select(RepoTracker).where(RepoTracker.user_id == user_id)))
            .scalars()
            .all()
        )
        stale_repos = []
        for tracker in user_trackers:
            repo = await session.get(TrackedRepo, tracker.repo_id)
            if repo and repo.is_active and repo.last_sync_error:
                stale_repos.append(
                    {
                        "id": repo.id,
                        "full_name": repo.full_name,
                        "last_sync_error": repo.last_sync_error,
                        "last_sync_error_at": (
                            repo.last_sync_error_at.isoformat() if repo.last_sync_error_at else None
                        ),
                        "last_successful_sync_at": (
                            repo.last_successful_sync_at.isoformat()
                            if repo.last_successful_sync_at
                            else None
                        ),
                        "remediation": {
                            "action": "check_repo_access",
                            "label": "Check repository",
                            "description": ("Verify the repo exists and your token has access"),
                        },
                    }
                )

        return {
            "has_issues": bool(account_issues or stale_repos),
            "accounts": account_issues,
            "stale_repos": stale_repos,
        }


@router.post("/health/check")
async def auth_health_check(request: Request):
    """On-demand token validation: call GET /user on GitHub for each account."""
    from src.services.crypto import decrypt_token
    from src.services.github_client import GitHubAuthError, GitHubClient

    user_id = get_github_user_id(request)
    if not user_id:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    from datetime import UTC, datetime

    # Collect account IDs first, then check each in a short-lived session
    async with async_session_factory() as session:
        accounts = (
            (
                await session.execute(
                    select(GitHubAccount).where(
                        GitHubAccount.user_id == user_id,
                        GitHubAccount.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        account_ids = [(a.id, a.encrypted_token, a.login, a.base_url) for a in accounts]

    logger.info(f"Running on-demand health check for user {user_id} ({len(account_ids)} accounts)")

    _status_map = {
        "token_expired": "expired",
        "token_revoked": "revoked",
        "sso_required": "sso_required",
        "insufficient_scope": "insufficient_scope",
    }

    now = datetime.now(UTC)
    for acct_id, encrypted_token, login, base_url in account_ids:
        if not encrypted_token:
            continue
        token = decrypt_token(encrypted_token)
        if not token:
            logger.warning(f"Decrypt failed for account {login} (id={acct_id}) during health check")
            async with async_session_factory() as session:
                acct = await session.get(GitHubAccount, acct_id)
                if acct:
                    acct.token_status = "decrypt_failed"
                    acct.token_error = (
                        "Token cannot be decrypted. The server encryption key may have changed."
                    )
                    acct.token_checked_at = now
                    await session.commit()
            continue

        gh = GitHubClient(token=token, base_url=base_url)
        try:
            await gh.get_authenticated_user()
            async with async_session_factory() as session:
                acct = await session.get(GitHubAccount, acct_id)
                if acct:
                    was_errored = acct.token_status != "ok"
                    acct.token_status = "ok"
                    acct.token_error = None
                    acct.token_checked_at = now
                    await session.commit()
                    if was_errored:
                        logger.info(f"Account {login} (id={acct_id}) recovered from error state")
                        await broadcast_event(
                            "auth_resolved",
                            {"account_id": acct_id, "login": login},
                        )
        except GitHubAuthError as exc:
            status = _status_map.get(exc.error_type.value, "expired")
            logger.warning(
                f"Health check failed for account {login} (id={acct_id}): {exc.error_type.value}"
            )
            async with async_session_factory() as session:
                acct = await session.get(GitHubAccount, acct_id)
                if acct:
                    acct.token_status = status
                    acct.token_error = str(exc)[:500]
                    acct.token_checked_at = now
                    await session.commit()
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.warning(f"Network error checking account {login} (id={acct_id}): {exc}")
            # Transient - don't change token status
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                logger.warning(
                    f"GitHub server error checking account {login} (id={acct_id}): {exc}"
                )
                # GitHub outage - don't change token status
            else:
                logger.warning(f"HTTP error checking account {login} (id={acct_id}): {exc}")
                async with async_session_factory() as session:
                    acct = await session.get(GitHubAccount, acct_id)
                    if acct:
                        acct.token_status = "expired"
                        acct.token_error = str(exc)[:500]
                        acct.token_checked_at = now
                        await session.commit()
        finally:
            await gh.close()

    # Return updated health status
    return await auth_health(request)


@router.get("/user")
async def get_current_user(request: Request):
    """Return current GitHub user info or null if not connected."""
    user_id = get_github_user_id(request)
    if not user_id:
        return None
    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if not user:
            return None
        return {
            "id": user.id,
            "login": user.login,
            "name": user.name,
            "avatar_url": user.avatar_url,
        }


@router.post("/github/disconnect")
async def github_disconnect(response: Response):
    """Clear GitHub identity cookie."""
    response.delete_cookie(GITHUB_COOKIE, path="/")
    return {"status": "disconnected"}


# ── Dev impersonation ───────────────────────────────────────


@router.post("/dev-login/{user_id}")
async def dev_login(user_id: int, response: Response):
    """Impersonate any user by ID. Only available when DEV_MODE=true."""
    if not settings.dev_mode:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    async with async_session_factory() as session:
        user = await session.get(User, user_id)
        if not user:
            return JSONResponse(status_code=404, content={"detail": "User not found"})

        # Set identity cookie as that user
        expires = int(time.time()) + settings.session_max_age_seconds
        cookie_payload = f"{user.id}:{expires}"
        token = _sign(cookie_payload)

        is_https = not settings.frontend_url or settings.frontend_url.startswith("https")
        response.set_cookie(
            GITHUB_COOKIE,
            token,
            max_age=settings.session_max_age_seconds,
            httponly=True,
            secure=is_https,
            samesite="lax",
            path="/",
        )

        return {
            "id": user.id,
            "login": user.login,
            "name": user.name,
            "avatar_url": user.avatar_url,
        }


@router.get("/dev-users")
async def list_dev_users():
    """List all users for the dev switcher. Only available when DEV_MODE=true."""
    if not settings.dev_mode:
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    async with async_session_factory() as session:
        users = (
            (await session.execute(select(User).where(User.is_active.is_(True)).order_by(User.id)))
            .scalars()
            .all()
        )
        return [
            {
                "id": u.id,
                "login": u.login,
                "name": u.name,
                "avatar_url": u.avatar_url,
            }
            for u in users
        ]
