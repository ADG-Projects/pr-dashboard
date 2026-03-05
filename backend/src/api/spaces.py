"""API routes for space management (auto-discovered GitHub connections)."""

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.schemas import SpaceOut, SpaceToggle
from src.db.engine import get_session
from src.models.tables import Space, TrackedRepo
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient

router = APIRouter(prefix="/api/spaces", tags=["spaces"])


def _token_for_space(space: Space) -> str | None:
    """Get the decrypted token for a space via its github_account."""
    if space.github_account and space.github_account.encrypted_token:
        return decrypt_token(space.github_account.encrypted_token)
    return None


def _base_url_for_space(space: Space) -> str:
    """Get the base_url for a space via its github_account."""
    if space.github_account:
        return space.github_account.base_url
    return "https://api.github.com"


@router.get("", response_model=list[SpaceOut])
async def list_spaces(
    session: AsyncSession = Depends(get_session),
) -> list[SpaceOut]:
    """List all spaces."""
    spaces = (
        (
            await session.execute(
                select(Space).options(selectinload(Space.github_account)).order_by(Space.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        SpaceOut(
            id=s.id,
            name=s.name,
            slug=s.slug,
            space_type=s.space_type,
            base_url=_base_url_for_space(s),
            is_active=s.is_active,
            has_token=bool(s.github_account and s.github_account.encrypted_token),
            created_at=s.created_at,
            github_account_id=s.github_account_id,
            github_account_login=s.github_account.login if s.github_account else None,
        )
        for s in spaces
    ]


@router.patch("/{space_id}/toggle", response_model=SpaceOut)
async def toggle_space(
    space_id: int,
    body: SpaceToggle,
    session: AsyncSession = Depends(get_session),
) -> SpaceOut:
    """Enable or disable a space (controls whether its repos are synced)."""
    result = await session.execute(
        select(Space).options(selectinload(Space.github_account)).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    space.is_active = body.is_active
    await session.commit()
    await session.refresh(space)

    logger.info(f"Space '{space.name}' {'enabled' if space.is_active else 'disabled'}")

    return SpaceOut(
        id=space.id,
        name=space.name,
        slug=space.slug,
        space_type=space.space_type,
        base_url=_base_url_for_space(space),
        is_active=space.is_active,
        has_token=bool(space.github_account and space.github_account.encrypted_token),
        created_at=space.created_at,
        github_account_id=space.github_account_id,
        github_account_login=space.github_account.login if space.github_account else None,
    )


@router.delete("/{space_id}", status_code=204)
async def delete_space(space_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """Soft-delete a space."""
    space = await session.get(Space, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    space.is_active = False
    await session.commit()


@router.get("/{space_id}/available-repos")
async def list_available_repos(space_id: int, session: AsyncSession = Depends(get_session)):
    """List repos in a space's org/user that are not yet tracked."""
    result = await session.execute(
        select(Space).options(selectinload(Space.github_account)).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    token = _token_for_space(space)
    if not token:
        raise HTTPException(status_code=400, detail="Space has no token")

    base_url = _base_url_for_space(space)
    gh = GitHubClient(token=token, base_url=base_url)
    try:
        if space.space_type == "org":
            repos = await gh.list_org_repos(space.slug)
        else:
            repos = await gh.list_user_repos(space.slug)
    finally:
        await gh.close()

    tracked = set((await session.execute(select(TrackedRepo.full_name))).scalars().all())

    return [
        {
            "name": r["name"],
            "full_name": r["full_name"],
            "description": r.get("description"),
            "private": r.get("private", False),
            "pushed_at": r.get("pushed_at"),
        }
        for r in repos
        if not r.get("archived") and r["full_name"] not in tracked
    ]


@router.post("/{space_id}/connectivity")
async def check_connectivity(space_id: int, session: AsyncSession = Depends(get_session)):
    """Validate that the space token can connect to GitHub."""
    result = await session.execute(
        select(Space).options(selectinload(Space.github_account)).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    token = _token_for_space(space)
    if not token:
        raise HTTPException(status_code=400, detail="Space has no token")

    base_url = _base_url_for_space(space)
    gh = GitHubClient(token=token, base_url=base_url)
    try:
        rate = await gh.get_rate_limit()
        return {
            "ok": True,
            "rate_limit": rate.get("rate", {}).get("limit"),
            "rate_remaining": rate.get("rate", {}).get("remaining"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        await gh.close()
