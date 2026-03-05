"""API routes for user management (users from GitHub OAuth)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import UserOut, UserUpdate
from src.db.engine import get_session
from src.models.tables import User

router = APIRouter(prefix="/api/team", tags=["team"])


@router.get("", response_model=list[UserOut])
async def list_users(
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    """List all users (created via GitHub OAuth login)."""
    users = (await session.execute(select(User).order_by(User.login))).scalars().all()
    return [
        UserOut(
            id=u.id,
            login=u.login,
            name=u.name,
            avatar_url=u.avatar_url,
            is_active=u.is_active,
            created_at=u.created_at,
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
