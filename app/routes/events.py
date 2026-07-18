"""Server-Sent Events channels (spec §8.2).

The kiosk subscribes to ``kiosk`` and, on any ``state`` event, re-fetches its
board via HTMX. Parent live view subscribes to ``parent``.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.config import Config
from app.models.device import Device
from app.routes.deps import get_config, require_kiosk, require_parent
from app.services.event_bus import bus

router = APIRouter()


async def _stream(request: Request, channel: str):
    async with bus.subscribe(channel) as queue:
        # prime the connection so htmx-sse considers it open
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"event: {event.name}\ndata: {event.data}\n\n"


@router.get("/events/kiosk")
async def events_kiosk(
    request: Request,
    device: Device = Depends(require_kiosk),
    config: Config = Depends(get_config),
):
    return StreamingResponse(_stream(request, "kiosk"), media_type="text/event-stream")


@router.get("/events/parent")
async def events_parent(
    request: Request,
    device: Device = Depends(require_parent),
    config: Config = Depends(get_config),
):
    return StreamingResponse(_stream(request, "parent"), media_type="text/event-stream")
