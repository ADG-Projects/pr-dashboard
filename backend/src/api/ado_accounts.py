"""API routes for managing linked Azure DevOps accounts."""

import base64

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_github_user_id
from src.api.schemas import AdoAccountCreate, AdoAccountOut
from src.db.engine import get_session
from src.models.tables import AdoAccount
from src.services.crypto import encrypt_token

router = APIRouter(prefix="/api/ado-accounts", tags=["ado-accounts"])


async def _validate_ado_token(org_url: str, token: str) -> None:
    """Validate an ADO PAT by calling the projects API.

    Isolates the plaintext token in its own stack frame so it won't
    appear in tracebacks from the calling function.
    """
    encoded = base64.b64encode(f":{token}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{org_url}/_apis/projects?api-version=7.1",
                headers={"Authorization": f"Basic {encoded}"},
            )
            resp.raise_for_status()
    except Exception as exc:
        logger.warning(f"ADO token validation failed for {org_url}: {exc}")
        raise HTTPException(
            status_code=400, detail="Invalid token or ADO organization unreachable"
        ) from None


def _account_to_out(account: AdoAccount) -> AdoAccountOut:
    return AdoAccountOut(
        id=account.id,
        org_url=account.org_url,
        project=account.project,
        display_name=account.display_name,
        has_token=bool(account.encrypted_token),
        created_at=account.created_at,
    )


@router.get("", response_model=list[AdoAccountOut])
async def list_ado_accounts(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[AdoAccountOut]:
    """List ADO accounts linked to the current user."""
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    accounts = (
        (
            await session.execute(
                select(AdoAccount)
                .where(AdoAccount.user_id == user_id, AdoAccount.is_active.is_(True))
                .order_by(AdoAccount.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_account_to_out(a) for a in accounts]


@router.post("", response_model=AdoAccountOut, status_code=201)
async def link_ado_account(
    body: AdoAccountCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AdoAccountOut:
    """Link an ADO account using a Personal Access Token.

    Validates the token by calling the ADO projects API, then encrypts and stores it.
    """
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    org_url = body.org_url.rstrip("/")
    project = body.project

    # Validate token, encrypt it, then discard the plaintext immediately
    await _validate_ado_token(org_url, body.token)
    encrypted = encrypt_token(body.token)
    del body  # Remove plaintext token from this frame

    # Check if this org_url+project combo already exists for the user
    result = await session.execute(
        select(AdoAccount).where(
            AdoAccount.user_id == user_id,
            AdoAccount.org_url == org_url,
            AdoAccount.project == project,
        )
    )
    account = result.scalar_one_or_none()

    display_name = f"{org_url.split('/')[-1]} / {project}"

    if account is None:
        account = AdoAccount(
            user_id=user_id,
            encrypted_token=encrypted,
            org_url=org_url,
            project=project,
            display_name=display_name,
        )
        session.add(account)
    else:
        account.encrypted_token = encrypted
        account.display_name = display_name
        account.is_active = True

    await session.commit()
    await session.refresh(account)

    logger.info(f"Linked ADO account {display_name} for user {user_id}")
    return _account_to_out(account)


@router.delete("/{account_id}", status_code=204)
async def remove_ado_account(
    account_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a linked ADO account."""
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    account = await session.get(AdoAccount, account_id)
    if not account or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="ADO account not found")

    await session.delete(account)
    await session.commit()
    logger.info(f"Deleted ADO account {account.display_name} for user {user_id}")
