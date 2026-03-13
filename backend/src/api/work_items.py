"""API routes for Azure DevOps work item linking."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_github_user_id
from src.db.engine import get_session
from src.models.tables import AdoAccount, PullRequest, WorkItemLink
from src.services import ado_client
from src.services.crypto import decrypt_token

router = APIRouter(prefix="/api/ado", tags=["work-items"])
pr_router = APIRouter(prefix="/api/repos/{repo_id}", tags=["work-items"])


async def _resolve_ado_credentials(request: Request, session: AsyncSession) -> tuple[str, str, str]:
    """Resolve ADO credentials from the authenticated user's AdoAccount.

    Returns (token, org_url, project) or raises 400/401.
    """
    user_id = get_github_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    result = await session.execute(
        select(AdoAccount)
        .where(
            AdoAccount.user_id == user_id,
            AdoAccount.is_active.is_(True),
        )
        .order_by(AdoAccount.created_at)
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(
            status_code=400,
            detail="No ADO account linked. Add one in Settings.",
        )

    token = decrypt_token(account.encrypted_token)
    if not token:
        raise HTTPException(
            status_code=400,
            detail="Cannot decrypt ADO token. The server's SECRET_KEY may have changed.",
        )

    return token, account.org_url, account.project


@router.get("/status")
async def ado_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Check whether ADO integration is configured for the current user."""
    user_id = get_github_user_id(request)
    if not user_id:
        return {"configured": False}

    result = await session.execute(
        select(AdoAccount.id)
        .where(
            AdoAccount.user_id == user_id,
            AdoAccount.is_active.is_(True),
        )
        .limit(1)
    )
    return {"configured": result.scalar_one_or_none() is not None}


@router.get("/work-items")
async def list_work_items(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Return recent ADO work items for preloading in the dropdown."""
    token, org_url, project = await _resolve_ado_credentials(request, session)
    try:
        return await ado_client.list_work_items(token, org_url, project)
    except Exception as exc:
        logger.warning(f"ADO list_work_items failed: {exc}")
        raise HTTPException(status_code=502, detail="ADO API error") from exc


@router.get("/search")
async def search_work_items(
    q: str = Query(..., min_length=1),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Search ADO work items by ID or title."""
    token, org_url, project = await _resolve_ado_credentials(request, session)
    try:
        return await ado_client.search_work_items(token, org_url, project, q)
    except Exception as exc:
        logger.warning(f"ADO search failed: {exc}")
        raise HTTPException(status_code=502, detail="ADO API error") from exc


@pr_router.post("/pulls/{number}/work-items")
async def link_work_item(
    repo_id: int,
    number: int,
    body: dict,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Link an ADO work item to a PR."""
    token, org_url, project = await _resolve_ado_credentials(request, session)

    work_item_id = body.get("work_item_id")
    if not work_item_id or not isinstance(work_item_id, int):
        raise HTTPException(
            status_code=422, detail="work_item_id is required and must be an integer"
        )

    result = await session.execute(
        select(PullRequest).where(PullRequest.repo_id == repo_id, PullRequest.number == number)
    )
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")

    existing = await session.execute(
        select(WorkItemLink).where(
            WorkItemLink.pull_request_id == pr.id,
            WorkItemLink.work_item_id == work_item_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Work item already linked to this PR")

    item = await ado_client.get_work_item(token, org_url, project, work_item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"ADO work item {work_item_id} not found")

    link = WorkItemLink(
        pull_request_id=pr.id,
        work_item_id=item["work_item_id"],
        title=item["title"],
        state=item["state"],
        work_item_type=item["work_item_type"],
        url=item["url"],
        assigned_to=item["assigned_to"],
    )
    session.add(link)
    await session.commit()
    await session.refresh(link)

    if pr.html_url:
        await ado_client.add_hyperlink(
            token,
            org_url,
            project,
            work_item_id,
            pr.html_url,
            f"Linked from PR Dashboard: {pr.html_url}",
            pr_number=pr.number,
        )

    return {
        "id": link.id,
        "work_item_id": link.work_item_id,
        "title": link.title,
        "state": link.state,
        "work_item_type": link.work_item_type,
        "url": link.url,
        "assigned_to": link.assigned_to,
    }


@pr_router.delete("/pulls/{number}/work-items/{work_item_id}")
async def unlink_work_item(
    repo_id: int,
    number: int,
    work_item_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Remove a work item link from a PR."""
    token, org_url, project = await _resolve_ado_credentials(request, session)

    result = await session.execute(
        select(PullRequest).where(PullRequest.repo_id == repo_id, PullRequest.number == number)
    )
    pr = result.scalar_one_or_none()
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")

    link = (
        await session.execute(
            select(WorkItemLink).where(
                WorkItemLink.pull_request_id == pr.id,
                WorkItemLink.work_item_id == work_item_id,
            )
        )
    ).scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Work item link not found")

    if pr.html_url:
        await ado_client.remove_hyperlink(
            token,
            org_url,
            project,
            work_item_id,
            pr.html_url,
            pr_number=pr.number,
        )

    await session.delete(link)
    await session.commit()
    return {"ok": True}
