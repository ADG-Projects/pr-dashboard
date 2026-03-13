"""GitHub webhook receiver endpoint."""

import asyncio
import hashlib
import hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import select

from src.config.settings import settings
from src.db.engine import async_session_factory
from src.models.tables import TrackedRepo
from src.services.crypto import decrypt_token
from src.services.github_client import GitHubClient
from src.services.sync_service import SyncService

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# Strong references to background tasks to prevent GC
_background_tasks: set[asyncio.Task] = set()


def _track_task(task: asyncio.Task) -> None:
    """Add task to the set and register cleanup callback."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Validate X-Hub-Signature-256 header using HMAC-SHA256."""
    if not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


async def _resolve_client_for_repo(repo_id: int) -> GitHubClient | None:
    """Resolve a GitHub client for a tracked repo using any available tracker token."""
    from sqlalchemy.orm import selectinload

    from src.models.tables import RepoTracker, Space

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


async def _find_repo(full_name: str) -> TrackedRepo | None:
    """Find a tracked repo by full_name (owner/name)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(TrackedRepo).where(
                TrackedRepo.full_name == full_name,
                TrackedRepo.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()


async def _handle_pr_event(repo: TrackedRepo, pr_number: int, delivery_id: str) -> None:
    """Background task: sync a single PR."""
    gh = None
    try:
        gh = await _resolve_client_for_repo(repo.id)
        if not gh:
            logger.warning(f"Webhook: no token for {repo.full_name}, skipping PR #{pr_number}")
            return
        svc = SyncService()
        await svc.sync_single_pr(repo.id, repo.owner, repo.name, pr_number, gh)
    except Exception:
        logger.exception(
            f"Webhook background task failed: {repo.full_name}#{pr_number} delivery={delivery_id}"
        )
    finally:
        if gh:
            await gh.close()


async def _handle_check_event(repo: TrackedRepo, head_sha: str, delivery_id: str) -> None:
    """Background task: sync checks by head SHA."""
    gh = None
    try:
        gh = await _resolve_client_for_repo(repo.id)
        if not gh:
            logger.warning(f"Webhook: no token for {repo.full_name}, skipping check sync")
            return
        svc = SyncService()
        await svc.sync_checks_by_sha(repo.id, repo.owner, repo.name, head_sha, gh)
    except Exception:
        logger.exception(
            f"Webhook background task failed: {repo.full_name} sha={head_sha[:8]} "
            f"delivery={delivery_id}"
        )
    finally:
        if gh:
            await gh.close()


@router.post("/github")
async def receive_github_webhook(request: Request) -> JSONResponse:
    """Receive and process GitHub webhook events."""
    if not settings.github_webhook_secret:
        return JSONResponse(status_code=403, content={"detail": "Webhooks not configured"})

    # Read raw body for signature validation
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "unknown")
    event_type = request.headers.get("X-GitHub-Event", "")

    if not _verify_signature(body, signature, settings.github_webhook_secret):
        logger.warning(f"Webhook signature validation failed: delivery={delivery_id}")
        return JSONResponse(status_code=401, content={"detail": "Invalid signature"})

    payload = await request.json()

    # Ping event (sent when webhook is first created)
    if event_type == "ping":
        logger.info(f"Webhook ping received: delivery={delivery_id}")
        return JSONResponse(content={"status": "pong"})

    # Extract repo info
    repo_data = payload.get("repository", {})
    full_name = repo_data.get("full_name", "")
    action = payload.get("action", "")

    if not full_name:
        return JSONResponse(content={"status": "ignored", "reason": "no repository"})

    repo = await _find_repo(full_name)
    if not repo:
        return JSONResponse(content={"status": "ignored", "reason": "repo not tracked"})

    # Route by event type
    if event_type == "pull_request":
        pr_number = payload.get("pull_request", {}).get("number")
        if pr_number:
            logger.info(
                f"Webhook received: event=pull_request action={action} "
                f"repo={full_name} pr=#{pr_number} delivery={delivery_id}"
            )
            _track_task(asyncio.create_task(_handle_pr_event(repo, pr_number, delivery_id)))

    elif event_type == "pull_request_review":
        pr_number = payload.get("pull_request", {}).get("number")
        if pr_number:
            logger.info(
                f"Webhook received: event=pull_request_review action={action} "
                f"repo={full_name} pr=#{pr_number} delivery={delivery_id}"
            )
            _track_task(asyncio.create_task(_handle_pr_event(repo, pr_number, delivery_id)))

    elif event_type in ("check_suite", "check_run"):
        if event_type == "check_suite":
            head_sha = payload.get("check_suite", {}).get("head_sha", "")
        else:
            head_sha = payload.get("check_run", {}).get("head_sha", "")
        if head_sha:
            logger.info(
                f"Webhook received: event={event_type} action={action} "
                f"repo={full_name} sha={head_sha[:8]} delivery={delivery_id}"
            )
            _track_task(asyncio.create_task(_handle_check_event(repo, head_sha, delivery_id)))

    elif event_type in ("issue_comment", "pull_request_review_comment"):
        # Only handle comments on PRs (issue_comment fires for both issues and PRs)
        issue = payload.get("issue", {})
        pr_ref = issue.get("pull_request") or payload.get("pull_request")
        if pr_ref:
            pr_number = issue.get("number") or payload.get("pull_request", {}).get("number")
            if pr_number:
                logger.info(
                    f"Webhook received: event={event_type} action={action} "
                    f"repo={full_name} pr=#{pr_number} delivery={delivery_id}"
                )
                _track_task(asyncio.create_task(_handle_pr_event(repo, pr_number, delivery_id)))

    else:
        logger.debug(f"Webhook ignored: event={event_type} repo={full_name} delivery={delivery_id}")

    return JSONResponse(content={"status": "accepted"})
