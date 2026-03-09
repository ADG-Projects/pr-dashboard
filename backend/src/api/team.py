"""API routes for user management (users from GitHub OAuth)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.schemas import LinkedAccount, UserOut, UserUpdate
from src.db.engine import get_session
from src.models.tables import GitHubAccount, User

router = APIRouter(prefix="/api/team", tags=["team"])


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    """List all users (from OAuth login and auto-discovered reviewers)."""
    users = (
        (
            await session.execute(
                select(User)
                .options(selectinload(User.github_accounts).selectinload(GitHubAccount.spaces))
                .order_by(User.login)
            )
        )
        .scalars()
        .all()
    )
    return [
        UserOut(
            id=u.id,
            login=u.login,
            name=u.name,
            avatar_url=u.avatar_url,
            is_active=u.is_active,
            created_at=u.created_at,
            linked_accounts=[
                LinkedAccount(
                    login=ga.login,
                    avatar_url=ga.avatar_url,
                    space_slugs=[s.slug for s in ga.spaces],
                )
                for ga in u.github_accounts
            ],
        )
        for u in users
    ]


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Update a user (toggle active status)."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return UserOut(
        id=user.id,
        login=user.login,
        name=user.name,
        avatar_url=user.avatar_url,
        is_active=user.is_active,
        created_at=user.created_at,
    )
