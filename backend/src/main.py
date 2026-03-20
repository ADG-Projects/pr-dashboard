"""FastAPI application for the PR Dashboard."""

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from src.api.accounts import router as accounts_router
from src.api.ado_accounts import router as ado_accounts_router
from src.api.auth import AuthMiddleware
from src.api.auth import router as auth_router
from src.api.events import router as events_router
from src.api.prioritize import router as prioritize_router
from src.api.pulls import router as pulls_router
from src.api.repos import router as repos_router
from src.api.spaces import router as spaces_router
from src.api.stacks import router as stacks_router
from src.api.team import router as team_router
from src.api.version import router as version_router
from src.api.webhook_admin import router as webhook_admin_router
from src.api.webhooks import router as webhooks_router
from src.api.work_items import pr_router as work_items_pr_router
from src.api.work_items import router as work_items_router
from src.config.settings import settings
from src.services.sync_service import SyncService

# Configure loguru with the app's log level (default handler is DEBUG)
logger.remove()
logger.add(sys.stderr, level=settings.log_level)

sync_service = SyncService(interval_seconds=settings.sync_interval_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Start background sync on startup."""
    await sync_service.start()

    try:
        await sync_service.migrate_webhook_events()
    except Exception as exc:
        logger.warning(f"Webhook event migration failed (non-fatal): {exc}")

    yield

    await sync_service.stop()


app = FastAPI(
    title="PR Dashboard",
    description="GitHub PR management dashboard for organizations",
    version="1.14.4",
    lifespan=lifespan,
)

# Auth middleware — only active when DASHBOARD_PASSWORD is set
if settings.dashboard_password:
    app.add_middleware(AuthMiddleware)

# CORS for local development (Vite dev server on :5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(repos_router)
app.include_router(spaces_router)
app.include_router(pulls_router)
app.include_router(prioritize_router)
app.include_router(team_router)
app.include_router(stacks_router)
app.include_router(events_router)
app.include_router(ado_accounts_router)
app.include_router(work_items_router)
app.include_router(work_items_pr_router)
app.include_router(version_router)
app.include_router(webhooks_router)
app.include_router(webhook_admin_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled error on {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# Serve built frontend in production — MUST be last (catch-all mount)
# Check both local dev layout and Docker layout
_frontend_dist: Path | None = None
for _candidate in [
    Path(__file__).parent.parent.parent / "frontend" / "dist",  # local dev
    Path(__file__).parent.parent / "frontend" / "dist",  # Docker (/app/frontend/dist)
]:
    if _candidate.exists():
        _frontend_dist = _candidate
        break

if _frontend_dist is not None:
    _index_html = _frontend_dist / "index.html"

    # SPA catch-all: serve index.html for any path that isn't an API route or static asset
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        # If the requested file exists on disk (JS, CSS, images), serve it
        # Resolve to prevent path traversal (e.g. ../../.env)
        asset = (_frontend_dist / full_path).resolve()
        is_safe = asset.is_relative_to(_frontend_dist)
        if full_path and is_safe and asset.exists() and asset.is_file():
            return FileResponse(asset)
        return FileResponse(_index_html)

    # Note: Static assets are served by the SPA catch-all above.
    # No separate StaticFiles mount needed.


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
