"""Authentication routes and middleware (HMAC-signed cookies + GitHub OAuth)."""

import hashlib
import hmac
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

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Paths that don't require authentication
PUBLIC_PATHS = {
    "/api/auth/login",
    "/api/auth/me",
    "/api/auth/github/callback",
    "/api/health",
}
PUBLIC_PREFIXES = ("/api/events", "/api/auth/dev-login/")


class AuthMiddleware(BaseHTTPMiddleware):
    """Block unauthenticated requests to /api/* routes (except public paths)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        path = request.url.path
        if (
            path.startswith("/api/")
            and path not in PUBLIC_PATHS
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
    # Encode link mode in the OAuth state so the callback knows
    state_payload = "oauth_link" if link else "oauth"
    params = {
        "client_id": settings.github_oauth_client_id,
        "scope": "repo read:org",
        "state": _sign(state_payload),
    }
    from urllib.parse import urlencode

    url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    return RedirectResponse(url=url)


@router.get("/github/callback")
async def github_oauth_callback(code: str, state: str, request: Request) -> RedirectResponse:
    """Exchange OAuth code for token, upsert user + account, auto-discover spaces."""
    base = settings.frontend_url or ""

    # Verify state — supports both "oauth" (sign-in) and "oauth_link" (link account)
    state_payload = _verify(state)
    if state_payload not in ("oauth", "oauth_link"):
        return RedirectResponse(url=f"{base}/?error=invalid_state")

    link_mode = state_payload == "oauth_link"

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
            logger.error(f"GitHub OAuth token exchange failed: {resp.text}")
            return RedirectResponse(url=f"{base}/?error=token_exchange_failed")

        token_data = resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            logger.error(f"No access_token in response: {token_data}")
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

    async with async_session_factory() as session:
        # In link mode, attach to the currently signed-in user
        # In sign-in mode, create/find user from GitHub identity
        existing_user_id = get_github_user_id(request) if link_mode else None

        if existing_user_id and link_mode:
            # Link mode: add this GitHub account to the existing user
            user = await session.get(User, existing_user_id)
            if not user:
                return RedirectResponse(url=f"{base}/?error=user_not_found")
        else:
            # Sign-in mode: find/create user from GitHub identity
            result = await session.execute(select(User).where(User.github_id == gh_user["id"]))
            user = result.scalar_one_or_none()

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
            account.encrypted_token = encrypted
            account.last_login_at = datetime.now(UTC)
            account.is_active = True

        await session.commit()
        await session.refresh(user)
        await session.refresh(account)
        user_id = user.id
        account_id = account.id

    # Auto-discover spaces (orgs + personal) in background
    import asyncio

    asyncio.create_task(_discover_spaces_background(account_id))

    # Set identity cookie
    expires = int(time.time()) + settings.session_max_age_seconds
    cookie_payload = f"{user_id}:{expires}"
    token = _sign(cookie_payload)

    redirect_url = f"{settings.frontend_url}/" if settings.frontend_url else "/"
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
