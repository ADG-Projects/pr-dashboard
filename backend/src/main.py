"""FastAPI application for the PR Dashboard."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.api.auth import AuthMiddleware
from src.api.auth import router as auth_router
from src.api.events import router as events_router
from src.api.progress import router as progress_router
from src.api.pulls import router as pulls_router
from src.api.repos import router as repos_router
from src.api.stacks import router as stacks_router
from src.api.team import router as team_router
from src.config.settings import settings
from src.db.base import Base
from src.db.engine import engine
from src.services.sync_service import SyncService

sync_service = SyncService(interval_seconds=settings.sync_interval_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Create tables and start background sync on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created")

    await sync_service.start()

    yield

    await sync_service.stop()


app = FastAPI(
    title="PR Dashboard",
    description="GitHub PR management dashboard for organizations",
    version="0.1.0",
    lifespan=lifespan,
)

# Auth middleware — only active when DASHBOARD_PASSWORD is set
if settings.dashboard_password:
    app.add_middleware(AuthMiddleware)

# CORS for local development (Vite dev server on :5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(auth_router)
app.include_router(repos_router)
app.include_router(pulls_router)
app.include_router(team_router)
app.include_router(progress_router)
app.include_router(stacks_router)
app.include_router(events_router)


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
for _candidate in [
    Path(__file__).parent.parent.parent / "frontend" / "dist",  # local dev
    Path(__file__).parent.parent / "frontend" / "dist",  # Docker (/app/frontend/dist)
]:
    if _candidate.exists():
        app.mount("/", StaticFiles(directory=str(_candidate), html=True), name="frontend")
        break


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
