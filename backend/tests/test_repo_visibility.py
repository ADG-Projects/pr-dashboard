"""Tests for repo-level visibility (private/shared) filtering and ownership enforcement."""

import time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import _sign
from src.models.tables import GitHubAccount, RepoTracker, Space, TrackedRepo, User
from src.services.discovery import _upsert_space


def _auth_cookie(user_id: int) -> dict:
    """Return cookies dict that injects a signed github_user identity cookie."""
    expires = int(time.time()) + 3600
    payload = f"{user_id}:{expires}"
    return {"cookies": {"github_user": _sign(payload)}}


async def _make_user(session: AsyncSession, github_id: int, login: str) -> User:
    user = User(github_id=github_id, login=login, name=login)
    session.add(user)
    await session.flush()
    return user


async def _make_account(session: AsyncSession, user: User) -> GitHubAccount:
    account = GitHubAccount(
        user_id=user.id,
        github_id=user.github_id,
        login=user.login,
        encrypted_token="fake-encrypted",
        base_url="https://api.github.com",
    )
    session.add(account)
    await session.flush()
    return account


async def _make_space(
    session: AsyncSession,
    name: str,
    slug: str,
    user_id: int,
    account_id: int,
    space_type: str = "org",
) -> Space:
    space = Space(
        name=name,
        slug=slug,
        space_type=space_type,
        github_account_id=account_id,
        user_id=user_id,
        is_active=True,
    )
    session.add(space)
    await session.flush()
    return space


async def _make_repo(
    session: AsyncSession,
    owner: str,
    name: str,
    space_id: int | None = None,
    user_id: int | None = None,
    visibility: str = "private",
) -> TrackedRepo:
    repo = TrackedRepo(
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        default_branch="main",
    )
    session.add(repo)
    await session.flush()

    if user_id is not None:
        tracker = RepoTracker(
            user_id=user_id,
            repo_id=repo.id,
            space_id=space_id,
            visibility=visibility,
        )
        session.add(tracker)
        await session.flush()

    return repo


# ── A. Visibility filtering — GET /api/repos ──────────────────


@pytest.mark.asyncio
async def test_user_sees_own_private_repos(client, db_session: AsyncSession):
    """User sees their own private repos."""
    user = await _make_user(db_session, 100, "alice")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org1", "org1", user.id, acct.id)
    await _make_repo(db_session, "org1", "r1", space.id, user.id, "private")
    await db_session.commit()

    resp = await client.get("/api/repos", **_auth_cookie(user.id))
    assert resp.status_code == 200
    names = {r["full_name"] for r in resp.json()}
    assert "org1/r1" in names


@pytest.mark.asyncio
async def test_user_cannot_see_other_private_repos(client, db_session: AsyncSession):
    """User B does NOT see User A's private repos."""
    user_a = await _make_user(db_session, 101, "alice2")
    user_b = await _make_user(db_session, 201, "bob2")
    acct_a = await _make_account(db_session, user_a)
    space = await _make_space(db_session, "org2", "org2", user_a.id, acct_a.id)
    await _make_repo(db_session, "org2", "secret", space.id, user_a.id, "private")
    await db_session.commit()

    resp = await client.get("/api/repos", **_auth_cookie(user_b.id))
    assert resp.status_code == 200
    names = {r["full_name"] for r in resp.json()}
    assert "org2/secret" not in names


@pytest.mark.asyncio
async def test_shared_repos_visible_to_all(client, db_session: AsyncSession):
    """Shared repos are visible to everyone including other users."""
    user_a = await _make_user(db_session, 102, "alice3")
    user_b = await _make_user(db_session, 202, "bob3")
    acct_a = await _make_account(db_session, user_a)
    space = await _make_space(db_session, "org3", "org3", user_a.id, acct_a.id)
    await _make_repo(db_session, "org3", "public", space.id, user_a.id, "shared")
    await db_session.commit()

    resp = await client.get("/api/repos", **_auth_cookie(user_b.id))
    assert resp.status_code == 200
    names = {r["full_name"] for r in resp.json()}
    assert "org3/public" in names


@pytest.mark.asyncio
async def test_anonymous_sees_only_shared_repos(client, db_session: AsyncSession):
    """Anonymous sees only shared repos."""
    user = await _make_user(db_session, 103, "alice4")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org4", "org4", user.id, acct.id)
    await _make_repo(db_session, "org4", "priv", space.id, user.id, "private")
    await _make_repo(db_session, "org4", "pub", space.id, user.id, "shared")
    await db_session.commit()

    resp = await client.get("/api/repos")  # no cookie
    assert resp.status_code == 200
    names = {r["full_name"] for r in resp.json()}
    assert "org4/pub" in names
    assert "org4/priv" not in names


@pytest.mark.asyncio
async def test_repo_response_includes_visibility_fields(client, db_session: AsyncSession):
    """Repo response includes visibility and user_id fields."""
    user = await _make_user(db_session, 104, "alice5")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org5", "org5", user.id, acct.id)
    await _make_repo(db_session, "org5", "r1", space.id, user.id, "private")
    await db_session.commit()

    resp = await client.get("/api/repos", **_auth_cookie(user.id))
    assert resp.status_code == 200
    repo_data = resp.json()[0]
    assert repo_data["visibility"] == "private"
    assert repo_data["user_id"] == user.id


# ── B. Ownership enforcement — PATCH /api/repos/{id}/visibility ──


@pytest.mark.asyncio
async def test_owner_can_change_repo_visibility(client, db_session: AsyncSession):
    """Owner can switch repo visibility to shared."""
    user = await _make_user(db_session, 120, "owner1")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org-v1", "org-v1", user.id, acct.id)
    repo = await _make_repo(db_session, "org-v1", "r1", space.id, user.id, "private")
    await db_session.commit()

    resp = await client.patch(
        f"/api/repos/{repo.id}/visibility",
        json={"visibility": "shared"},
        **_auth_cookie(user.id),
    )
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "shared"


@pytest.mark.asyncio
async def test_non_owner_gets_403(client, db_session: AsyncSession):
    """Non-owner cannot change repo visibility."""
    user_a = await _make_user(db_session, 121, "owner2")
    user_b = await _make_user(db_session, 221, "intruder")
    acct_a = await _make_account(db_session, user_a)
    space = await _make_space(db_session, "org-v2", "org-v2", user_a.id, acct_a.id)
    repo = await _make_repo(db_session, "org-v2", "r2", space.id, user_a.id, "private")
    await db_session.commit()

    resp = await client.patch(
        f"/api/repos/{repo.id}/visibility",
        json={"visibility": "shared"},
        **_auth_cookie(user_b.id),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_unauthenticated_visibility_change_gets_401(client, db_session: AsyncSession):
    """No cookie → 401."""
    user = await _make_user(db_session, 122, "owner3")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org-v3", "org-v3", user.id, acct.id)
    repo = await _make_repo(db_session, "org-v3", "r3", space.id, user.id, "private")
    await db_session.commit()

    resp = await client.patch(
        f"/api/repos/{repo.id}/visibility",
        json={"visibility": "shared"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_visibility_value_rejected(client, db_session: AsyncSession):
    """Invalid visibility value → 400."""
    user = await _make_user(db_session, 123, "owner4")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org-v4", "org-v4", user.id, acct.id)
    repo = await _make_repo(db_session, "org-v4", "r4", space.id, user.id, "private")
    await db_session.commit()

    resp = await client.patch(
        f"/api/repos/{repo.id}/visibility",
        json={"visibility": "public"},
        **_auth_cookie(user.id),
    )
    assert resp.status_code == 400


# ── C. Security / penetration scenarios ──────────────────


@pytest.mark.asyncio
async def test_forged_cookie_treated_as_anonymous(client, db_session: AsyncSession):
    """Tampered HMAC cookie → treated as anonymous (only shared repos)."""
    user = await _make_user(db_session, 130, "victim")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org-sec", "org-sec", user.id, acct.id)
    await _make_repo(db_session, "org-sec", "secret", space.id, user.id, "private")
    await db_session.commit()

    forged = f"{user.id}:{int(time.time()) + 3600}.forgedsignature"
    resp = await client.get(
        "/api/repos",
        cookies={"github_user": forged},
    )
    assert resp.status_code == 200
    names = {r["full_name"] for r in resp.json()}
    assert "org-sec/secret" not in names


@pytest.mark.asyncio
async def test_expired_cookie_treated_as_anonymous(client, db_session: AsyncSession):
    """Signed cookie with past timestamp → anonymous."""
    user = await _make_user(db_session, 131, "expired-user")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org-exp", "org-exp", user.id, acct.id)
    await _make_repo(db_session, "org-exp", "hidden", space.id, user.id, "private")
    await db_session.commit()

    expired_ts = int(time.time()) - 3600
    payload = f"{user.id}:{expired_ts}"
    expired_cookie = _sign(payload)

    resp = await client.get(
        "/api/repos",
        cookies={"github_user": expired_cookie},
    )
    assert resp.status_code == 200
    names = {r["full_name"] for r in resp.json()}
    assert "org-exp/hidden" not in names


@pytest.mark.asyncio
async def test_sql_injection_via_visibility_field(client, db_session: AsyncSession):
    """SQL injection attempt in visibility value → 400."""
    user = await _make_user(db_session, 132, "sqli-user")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "org-sqli", "org-sqli", user.id, acct.id)
    repo = await _make_repo(db_session, "org-sqli", "r1", space.id, user.id, "private")
    await db_session.commit()

    resp = await client.patch(
        f"/api/repos/{repo.id}/visibility",
        json={"visibility": "shared' OR 1=1--"},
        **_auth_cookie(user.id),
    )
    assert resp.status_code == 400


# ── D. Spaces — simplified (owner-only, no visibility) ──────────────────


@pytest.mark.asyncio
async def test_spaces_only_show_own(client, db_session: AsyncSession):
    """User only sees their own spaces (no shared concept for spaces anymore)."""
    user_a = await _make_user(db_session, 140, "alice-sp")
    user_b = await _make_user(db_session, 240, "bob-sp")
    acct_a = await _make_account(db_session, user_a)
    acct_b = await _make_account(db_session, user_b)
    await _make_space(db_session, "a-space", "a-space", user_a.id, acct_a.id)
    await _make_space(db_session, "b-space", "b-space", user_b.id, acct_b.id)
    await db_session.commit()

    resp = await client.get("/api/spaces", **_auth_cookie(user_a.id))
    assert resp.status_code == 200
    names = {s["name"] for s in resp.json()}
    assert "a-space" in names
    assert "b-space" not in names


@pytest.mark.asyncio
async def test_anonymous_sees_no_spaces(client, db_session: AsyncSession):
    """Anonymous sees no spaces."""
    user = await _make_user(db_session, 141, "anon-sp")
    acct = await _make_account(db_session, user)
    await _make_space(db_session, "some-space", "some-space", user.id, acct.id)
    await db_session.commit()

    resp = await client.get("/api/spaces")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_owner_can_toggle_own_space(client, db_session: AsyncSession):
    """Owner can toggle their own space."""
    user = await _make_user(db_session, 150, "toggler1")
    acct = await _make_account(db_session, user)
    space = await _make_space(db_session, "my-toggle", "my-toggle", user.id, acct.id)
    await db_session.commit()

    resp = await client.patch(
        f"/api/spaces/{space.id}/toggle",
        json={"is_active": False},
        **_auth_cookie(user.id),
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_non_owner_cannot_toggle_space(client, db_session: AsyncSession):
    """Non-owner cannot toggle another user's space."""
    user_a = await _make_user(db_session, 151, "toggler2")
    user_b = await _make_user(db_session, 251, "intruder2")
    acct_a = await _make_account(db_session, user_a)
    space = await _make_space(db_session, "a-toggle", "a-toggle", user_a.id, acct_a.id)
    await db_session.commit()

    resp = await client.patch(
        f"/api/spaces/{space.id}/toggle",
        json={"is_active": False},
        **_auth_cookie(user_b.id),
    )
    assert resp.status_code == 403


# ── E. Discovery integration ──────────────────────────────


@pytest.mark.asyncio
async def test_upsert_space_sets_user_id(db_session: AsyncSession):
    """_upsert_space sets user_id on new spaces."""
    user = await _make_user(db_session, 160, "disco1")
    acct = await _make_account(db_session, user)

    space = await _upsert_space(
        db_session,
        account_id=acct.id,
        user_id=user.id,
        slug="disco-org",
        name="Disco Org",
        space_type="org",
    )
    await db_session.flush()
    assert space.user_id == user.id


@pytest.mark.asyncio
async def test_upsert_space_backfills_user_id(db_session: AsyncSession):
    """_upsert_space backfills user_id on existing space if null."""
    user = await _make_user(db_session, 161, "disco2")
    acct = await _make_account(db_session, user)

    space = Space(
        name="Orphan",
        slug="orphan-org",
        space_type="org",
        github_account_id=acct.id,
        user_id=None,
        is_active=False,
    )
    db_session.add(space)
    await db_session.flush()
    assert space.user_id is None

    updated = await _upsert_space(
        db_session,
        account_id=acct.id,
        user_id=user.id,
        slug="orphan-org",
        name="Orphan Updated",
        space_type="org",
    )
    await db_session.flush()
    assert updated.user_id == user.id
