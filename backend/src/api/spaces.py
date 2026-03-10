"""API routes for space management (auto-discovered GitHub connections)."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.auth import get_github_user_id
from src.api.schemas import SpaceOut, SpaceToggle
from src.db.engine import get_session
from src.models.tables import GitHubAccount, RepoTracker, Space, TrackedRepo
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


def _space_to_out(s: Space) -> SpaceOut:
    return SpaceOut(
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


@router.get("", response_model=list[SpaceOut])
async def list_spaces(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[SpaceOut]:
    """List spaces belonging to the current user."""
    user_id = get_github_user_id(request)
    stmt = (
        select(Space)
        .join(GitHubAccount, Space.github_account_id == GitHubAccount.id, isouter=True)
        .options(selectinload(Space.github_account))
        .where(
            # Only return spaces whose account is active (or has no account)
            (GitHubAccount.is_active == True) | (Space.github_account_id.is_(None))  # noqa: E712
        )
        .order_by(Space.created_at)
    )
    if user_id:
        stmt = stmt.where(Space.user_id == user_id)
    else:
        return []

    spaces = (await session.execute(stmt)).scalars().all()
    return [_space_to_out(s) for s in spaces]


@router.patch("/{space_id}/toggle", response_model=SpaceOut)
async def toggle_space(
    space_id: int,
    body: SpaceToggle,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SpaceOut:
    """Enable or disable a space (controls whether its repos are synced)."""
    result = await session.execute(
        select(Space).options(selectinload(Space.github_account)).where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    user_id = get_github_user_id(request)
    if space.user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the space owner can toggle this space")

    space.is_active = body.is_active
    await session.commit()
    await session.refresh(space)

    logger.info(f"Space '{space.name}' {'enabled' if space.is_active else 'disabled'}")
    return _space_to_out(space)


@router.delete("/{space_id}", status_code=204)
async def delete_space(
    space_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> None:
    """Soft-delete a space."""
    space = await session.get(Space, space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    user_id = get_github_user_id(request)
    if space.user_id != user_id:
        raise HTTPException(status_code=403, detail="Only the space owner can delete this space")

    space.is_active = False
    await session.commit()


@router.get("/{space_id}/available-repos")
async def list_available_repos(
    space_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    """List repos in a space's org/user that the current user is not yet tracking."""
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
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (403, 404) and space.space_type == "org":
            logger.warning(
                f"Cannot list org repos for {space.slug} ({exc.response.status_code}), "
                f"falling back to /user/repos"
            )
            all_repos = await gh.list_all_repos()
            repos = [r for r in all_repos if r["owner"]["login"] == space.slug]
        else:
            await gh.close()
            raise
    finally:
        await gh.close()

    # Per-user filter: only hide repos the current user already tracks
    user_id = get_github_user_id(request)
    if user_id:
        user_tracked = set(
            (
                await session.execute(
                    select(TrackedRepo.full_name)
                    .join(RepoTracker, RepoTracker.repo_id == TrackedRepo.id)
                    .where(
                        RepoTracker.user_id == user_id,
                        TrackedRepo.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
    else:
        user_tracked = set(
            (
                await session.execute(
                    select(TrackedRepo.full_name).where(TrackedRepo.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )

    non_archived = [r for r in repos if not r.get("archived")]
    total_from_github = len(non_archived)
    available = [r for r in non_archived if r["full_name"] not in user_tracked]
    already_tracked_count = total_from_github - len(available)

    return {
        "total_from_github": total_from_github,
        "already_tracked_count": already_tracked_count,
        "repos": [
            {
                "name": r["name"],
                "full_name": r["full_name"],
                "description": r.get("description"),
                "private": r.get("private", False),
                "pushed_at": r.get("pushed_at"),
            }
            for r in available
        ],
    }


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
