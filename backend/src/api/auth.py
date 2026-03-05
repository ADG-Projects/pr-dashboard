"""Authentication routes and middleware (HMAC-signed cookies)."""

import hashlib
import hmac
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.schemas import AuthStatus, LoginRequest
from src.config.settings import settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Paths that don't require authentication
PUBLIC_PATHS = {"/api/auth/login", "/api/auth/me", "/api/health"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Block unauthenticated requests to /api/* routes (except public paths)."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path.startswith("/api/")
            and path not in PUBLIC_PATHS
            and not is_authenticated(request)
        ):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        return await call_next(request)

COOKIE_NAME = "dashboard_session"


def _sign(payload: str) -> str:
    sig = hmac.new(
        settings.secret_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{sig}"


def _verify(token: str) -> str | None:
    if "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(
        settings.secret_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
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
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
    )
    return AuthStatus(authenticated=True, auth_enabled=True)


@router.get("/me")
async def auth_status(request: Request) -> AuthStatus:
    """Check current auth status."""
    return AuthStatus(
        authenticated=is_authenticated(request),
        auth_enabled=bool(settings.dashboard_password),
    )


@router.post("/logout")
async def logout(response: Response) -> AuthStatus:
    """Clear session cookie."""
    response.delete_cookie(COOKIE_NAME)
    return AuthStatus(authenticated=False, auth_enabled=bool(settings.dashboard_password))
