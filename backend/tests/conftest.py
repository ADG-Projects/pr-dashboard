"""Shared test fixtures — async SQLite engine, FastAPI test client."""

import asyncio
import time
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import src.api.repos as repos_module
from src.api.auth import _sign
from src.db.base import Base
from src.db.engine import get_session
from src.main import app
from src.models.tables import AdoAccount, GitHubAccount, User
from src.services.crypto import encrypt_token


@pytest.fixture(scope="session")
def event_loop():
    """Use a single event loop for the whole test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def async_engine():
    """Create an async in-memory SQLite engine for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a fresh async session bound to the in-memory DB."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(async_engine) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX async test client with the FastAPI app, using the test DB."""
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    # Patch async_session_factory used by cleanup code in repos.py
    original_factory = repos_module.async_session_factory
    repos_module.async_session_factory = factory

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    repos_module.async_session_factory = original_factory


# ── Auth helpers ─────────────────────────────────────────────


def make_auth_cookie(user_id: int, expires_offset: int = 3600) -> str:
    """Create a signed github_user cookie for the given user ID.

    Args:
        user_id: The user ID to embed in the cookie.
        expires_offset: Seconds from now until expiry (negative for expired).
    """
    expires = int(time.time()) + expires_offset
    return _sign(f"{user_id}:{expires}")


def make_password_cookie(expires_offset: int = 3600) -> str:
    """Create a signed dashboard_session cookie.

    Args:
        expires_offset: Seconds from now until expiry (negative for expired).
    """
    expires = int(time.time()) + expires_offset
    return _sign(str(expires))


# ── Multi-user fixtures ──────────────────────────────────────


@pytest_asyncio.fixture
async def seed_two_users(db_session: AsyncSession):
    """Create two users with GitHub accounts and ADO accounts for isolation tests."""
    user_a = User(github_id=100, login="alice", name="Alice")
    user_b = User(github_id=200, login="bob", name="Bob")
    db_session.add_all([user_a, user_b])
    await db_session.flush()

    gh_a = GitHubAccount(
        user_id=user_a.id,
        github_id=100,
        login="alice",
        encrypted_token=encrypt_token("token-a"),
        base_url="https://api.github.com",
    )
    gh_b = GitHubAccount(
        user_id=user_b.id,
        github_id=200,
        login="bob",
        encrypted_token=encrypt_token("token-b"),
        base_url="https://api.github.com",
    )
    db_session.add_all([gh_a, gh_b])
    await db_session.flush()

    ado_a = AdoAccount(
        user_id=user_a.id,
        encrypted_token=encrypt_token("ado-pat-a"),
        org_url="https://dev.azure.com/orgA",
        project="ProjA",
        display_name="orgA / ProjA",
    )
    ado_b = AdoAccount(
        user_id=user_b.id,
        encrypted_token=encrypt_token("ado-pat-b"),
        org_url="https://dev.azure.com/orgB",
        project="ProjB",
        display_name="orgB / ProjB",
    )
    db_session.add_all([ado_a, ado_b])
    await db_session.commit()

    return {
        "user_a": user_a,
        "user_b": user_b,
        "gh_a": gh_a,
        "gh_b": gh_b,
        "ado_a": ado_a,
        "ado_b": ado_b,
    }
