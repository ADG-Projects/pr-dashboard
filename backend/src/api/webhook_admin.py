"""Webhook registration and management endpoints."""

from fastapi import APIRouter, HTTPException
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config.settings import settings
from src.db.engine import async_session_factory
from src.models.tables import RepoTracker, Space, TrackedRepo
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient

router = APIRouter(prefix="/api/webhooks/admin", tags=["webhook-admin"])

WEBHOOK_CALLBACK_PATH = "/api/webhooks/github"


def _callback_url() -> str:
    return f"{settings.webhook_base_url.rstrip('/')}{WEBHOOK_CALLBACK_PATH}"


async def _get_client_for_repo(repo_id: int) -> GitHubClient | None:
    """Resolve a GitHub client from any tracker's space."""
    async with async_session_factory() as session:
        trackers = (
            (
                await session.execute(
                    select(RepoTracker)
                    .options(selectinload(RepoTracker.space).selectinload(Space.github_account))
                    .where(RepoTracker.repo_id == repo_id)
                )
            )
            .scalars()
            .all()
        )

        for tracker in trackers:
            if tracker.space and tracker.space.is_active and tracker.space.github_account:
                account = tracker.space.github_account
                if account.encrypted_token and account.is_active:
                    token = decrypt_token(account.encrypted_token)
                    if token:
                        return GitHubClient(token=token, base_url=account.base_url)
    return None


@router.post("/repos/{repo_id}/register")
async def register_webhook(repo_id: int) -> dict:
    """Register a GitHub webhook on a tracked repo."""
    if not settings.github_webhook_secret or not settings.webhook_base_url:
        raise HTTPException(
            status_code=400,
            detail="GITHUB_WEBHOOK_SECRET and WEBHOOK_BASE_URL must be configured",
        )

    async with async_session_factory() as session:
        repo = await session.get(TrackedRepo, repo_id)
        if not repo:
            raise HTTPException(status_code=404, detail="Repo not found")

        if repo.github_webhook_id:
            return {"status": "already_registered", "hook_id": repo.github_webhook_id}

        gh = await _get_client_for_repo(repo_id)
        if not gh:
            raise HTTPException(status_code=400, detail="No GitHub token available")

        try:
            url = _callback_url()
            hook = await gh.create_webhook(
                repo.owner, repo.name, url, settings.github_webhook_secret
            )
            hook_id = hook["id"]
            repo.github_webhook_id = hook_id
            await session.commit()
            logger.info(f"Webhook registered: repo_id={repo_id} hook_id={hook_id} url={url}")
            return {"status": "registered", "hook_id": hook_id}
        except Exception as exc:
            logger.warning(f"Failed to register webhook for {repo.full_name}: {exc}")
            raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}") from exc
        finally:
            await gh.close()


@router.delete("/repos/{repo_id}/unregister")
async def unregister_webhook(repo_id: int) -> dict:
    """Remove a GitHub webhook from a tracked repo."""
    async with async_session_factory() as session:
        repo = await session.get(TrackedRepo, repo_id)
        if not repo:
            raise HTTPException(status_code=404, detail="Repo not found")

        if not repo.github_webhook_id:
            return {"status": "no_webhook"}

        gh = await _get_client_for_repo(repo_id)
        if not gh:
            # Clear local state even if we can't reach GitHub
            repo.github_webhook_id = None
            await session.commit()
            return {"status": "cleared_locally", "detail": "No token to delete from GitHub"}

        try:
            await gh.delete_webhook(repo.owner, repo.name, repo.github_webhook_id)
            logger.info(f"Webhook unregistered: repo_id={repo_id} hook_id={repo.github_webhook_id}")
        except Exception as exc:
            logger.warning(f"Failed to delete webhook from GitHub: {exc}")
        finally:
            await gh.close()

        repo.github_webhook_id = None
        await session.commit()
        return {"status": "unregistered"}


@router.post("/register-all")
async def register_all_webhooks() -> dict:
    """Batch register webhooks for all repos that don't have one."""
    if not settings.github_webhook_secret or not settings.webhook_base_url:
        raise HTTPException(
            status_code=400,
            detail="GITHUB_WEBHOOK_SECRET and WEBHOOK_BASE_URL must be configured",
        )

    async with async_session_factory() as session:
        repos = (
            (
                await session.execute(
                    select(TrackedRepo).where(
                        TrackedRepo.is_active.is_(True),
                        TrackedRepo.github_webhook_id.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    results = {"registered": 0, "failed": 0, "details": []}
    url = _callback_url()

    for repo in repos:
        gh = await _get_client_for_repo(repo.id)
        if not gh:
            results["failed"] += 1
            results["details"].append({"repo": repo.full_name, "error": "no token"})
            continue

        try:
            hook = await gh.create_webhook(
                repo.owner, repo.name, url, settings.github_webhook_secret
            )
            hook_id = hook["id"]
            async with async_session_factory() as session:
                r = await session.get(TrackedRepo, repo.id)
                if r:
                    r.github_webhook_id = hook_id
                    await session.commit()
            logger.info(f"Webhook registered: repo_id={repo.id} hook_id={hook_id} url={url}")
            results["registered"] += 1
            results["details"].append({"repo": repo.full_name, "hook_id": hook_id})
        except Exception as exc:
            logger.warning(f"Failed to register webhook for {repo.full_name}: {exc}")
            results["failed"] += 1
            results["details"].append({"repo": repo.full_name, "error": str(exc)})
        finally:
            await gh.close()

    return results


@router.get("/status")
async def webhook_status() -> list[dict]:
    """Get webhook registration status for all active repos."""
    async with async_session_factory() as session:
        repos = (
            (
                await session.execute(
                    select(TrackedRepo)
                    .where(TrackedRepo.is_active.is_(True))
                    .order_by(TrackedRepo.full_name)
                )
            )
            .scalars()
            .all()
        )

    return [
        {
            "repo_id": repo.id,
            "full_name": repo.full_name,
            "webhook_id": repo.github_webhook_id,
            "has_webhook": repo.github_webhook_id is not None,
        }
        for repo in repos
    ]


async def auto_register_webhook(repo_id: int, owner: str, name: str) -> None:
    """Fire-and-forget webhook registration (called after adding a repo)."""
    if not settings.github_webhook_secret or not settings.webhook_base_url:
        return

    gh = await _get_client_for_repo(repo_id)
    if not gh:
        logger.warning(f"Webhook auto-register: no token for {owner}/{name}")
        return

    try:
        url = _callback_url()
        hook = await gh.create_webhook(owner, name, url, settings.github_webhook_secret)
        hook_id = hook["id"]
        async with async_session_factory() as session:
            repo = await session.get(TrackedRepo, repo_id)
            if repo:
                repo.github_webhook_id = hook_id
                await session.commit()
        logger.info(f"Webhook auto-registered: repo_id={repo_id} hook_id={hook_id} url={url}")
    except Exception as exc:
        logger.warning(f"Webhook auto-register failed for {owner}/{name}: {exc}")
    finally:
        await gh.close()
