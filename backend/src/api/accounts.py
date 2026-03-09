"""API routes for managing linked GitHub accounts."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_github_user_id
from src.api.schemas import AddSpaceRequest, GitHubAccountCreate, GitHubAccountOut
from src.db.engine import get_session
from src.models.tables import GitHubAccount, Space
from src.services.crypto import decrypt_token, encrypt_token
from src.services.discovery import discover_spaces_for_account
from src.services.github_client import GitHubClient

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=list[GitHubAccountOut])
async def list_accounts(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[GitHubAccountOut]:
    """List GitHub accounts linked to the current user."""
    user_id = get_github_user_id(request)
    if not user_id:
        return []

    accounts = (
        (
            await session.execute(
                select(GitHubAccount)
                .where(GitHubAccount.user_id == user_id, GitHubAccount.is_active.is_(True))
                .order_by(GitHubAccount.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        GitHubAccountOut(
            id=a.id,
            login=a.login,
            avatar_url=a.avatar_url,
            base_url=a.base_url,
            has_token=bool(a.encrypted_token),
            created_at=a.created_at,
            last_login_at=a.last_login_at,
        )
        for a in accounts
    ]


@router.post("", response_model=GitHubAccountOut, status_code=201)
async def link_account_with_token(
    body: GitHubAccountCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> GitHubAccountOut:
    """Link a GitHub account using a Personal Access Token."""
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Validate token by fetching the user it belongs to
    base_url = body.base_url or "https://api.github.com"
    gh = GitHubClient(token=body.token, base_url=base_url)
    try:
        gh_user = await gh.get_authenticated_user()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid token or unreachable: {exc}") from exc
    finally:
        await gh.close()

    # Upsert GitHubAccount
    result = await session.execute(
        select(GitHubAccount).where(
            GitHubAccount.user_id == user_id,
            GitHubAccount.github_id == gh_user["id"],
        )
    )
    account = result.scalar_one_or_none()

    encrypted = encrypt_token(body.token)
    now = datetime.now(UTC)

    if account is None:
        account = GitHubAccount(
            user_id=user_id,
            github_id=gh_user["id"],
            login=gh_user["login"],
            avatar_url=gh_user.get("avatar_url"),
            encrypted_token=encrypted,
            base_url=base_url,
            last_login_at=now,
        )
        session.add(account)
    else:
        account.login = gh_user["login"]
        account.avatar_url = gh_user.get("avatar_url")
        account.encrypted_token = encrypted
        account.base_url = base_url
        account.is_active = True
        account.last_login_at = now

    await session.flush()

    # Auto-discover spaces
    spaces = await discover_spaces_for_account(session, account)
    await session.commit()
    await session.refresh(account)

    logger.info(f"Linked account {account.login} via PAT ({len(spaces)} spaces discovered)")

    return GitHubAccountOut(
        id=account.id,
        login=account.login,
        avatar_url=account.avatar_url,
        base_url=account.base_url,
        has_token=bool(account.encrypted_token),
        created_at=account.created_at,
        last_login_at=account.last_login_at,
    )


@router.post("/{account_id}/discover")
async def discover_spaces(
    account_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Re-run space discovery for a linked account."""
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    account = await session.get(GitHubAccount, account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")

    spaces = await discover_spaces_for_account(session, account)
    await session.commit()

    return {
        "discovered": len(spaces),
        "spaces": [{"id": s.id, "slug": s.slug, "space_type": s.space_type} for s in spaces],
    }


@router.post("/{account_id}/spaces")
async def add_space_to_account(
    account_id: int,
    body: AddSpaceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Manually add an org/user as a space when auto-discovery can't find it.

    This is needed when the token lacks `read:org` scope (e.g., fine-grained PATs,
    SSO-enforced orgs). Validates the org exists by calling the GitHub API.
    """
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    account = await session.get(GitHubAccount, account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")

    # Check if space already exists for this account
    result = await session.execute(
        select(Space).where(
            Space.github_account_id == account_id,
            Space.slug == body.slug,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return {"id": existing.id, "slug": existing.slug, "already_exists": True}

    # Validate the org/user exists on GitHub
    if not account.encrypted_token:
        raise HTTPException(status_code=400, detail="Account has no token")

    token = decrypt_token(account.encrypted_token)
    gh = GitHubClient(token=token, base_url=account.base_url)
    try:
        if body.space_type == "org":
            # Try to list org repos to validate access
            await gh.list_org_repos(body.slug)
        else:
            await gh.list_user_repos(body.slug)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot access {body.space_type} '{body.slug}': {exc}",
        ) from exc
    finally:
        await gh.close()

    space = Space(
        name=body.name or body.slug,
        slug=body.slug,
        space_type=body.space_type,
        github_account_id=account_id,
        user_id=user_id,
        is_active=False,
    )
    session.add(space)
    await session.commit()
    await session.refresh(space)

    logger.info(f"Manually added space '{space.slug}' to account {account.login}")
    return {"id": space.id, "slug": space.slug, "already_exists": False}


@router.delete("/{account_id}", status_code=204)
async def remove_account(
    account_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete a linked GitHub account and clean up trackers."""
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    account = await session.get(GitHubAccount, account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")

    account.is_active = False

    from sqlalchemy import delete, func
    from sqlalchemy import select as sa_select

    from src.models.tables import RepoTracker, TrackedRepo

    space_ids = (
        (await session.execute(select(Space.id).where(Space.github_account_id == account_id)))
        .scalars()
        .all()
    )

    if space_ids:
        # Delete RepoTracker rows where space_id is in the account's spaces
        await session.execute(delete(RepoTracker).where(RepoTracker.space_id.in_(space_ids)))

        # Deactivate any TrackedRepo with zero remaining trackers
        orphan_repo_ids = (
            (
                await session.execute(
                    sa_select(TrackedRepo.id)
                    .outerjoin(RepoTracker, RepoTracker.repo_id == TrackedRepo.id)
                    .where(TrackedRepo.is_active.is_(True))
                    .group_by(TrackedRepo.id)
                    .having(func.count(RepoTracker.id) == 0)
                )
            )
            .scalars()
            .all()
        )
        if orphan_repo_ids:
            for repo_id in orphan_repo_ids:
                repo = await session.get(TrackedRepo, repo_id)
                if repo:
                    repo.is_active = False

        # Delete the spaces themselves
        await session.execute(delete(Space).where(Space.id.in_(space_ids)))

    await session.commit()
    logger.info(f"Deactivated GitHub account and cleaned up trackers/spaces: {account.login}")
