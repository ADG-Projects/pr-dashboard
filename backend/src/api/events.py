"""SSE endpoint for real-time updates."""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from src.services.events import subscribe, unsubscribe

router = APIRouter(tags=["events"])


async def _event_generator(
    queue: asyncio.Queue[tuple[str, str]],
) -> AsyncGenerator[dict[str, str], None]:
    """Yield SSE events from a subscriber queue."""
    try:
        while True:
            event_type, data = await queue.get()
            yield {"event": event_type, "data": data}
    except asyncio.CancelledError:
        pass
    finally:
        await unsubscribe(queue)


@router.get("/api/events")
async def sse_stream() -> EventSourceResponse:
    """SSE stream for real-time progress and CI updates."""
    queue = await subscribe()
    return EventSourceResponse(_event_generator(queue), ping=15)
