"""In-process async pub/sub feeding SSE (spec §8.2).

Single-worker Uvicorn keeps this simple — no external broker at two users.
Publishers call :func:`publish`; SSE routes :func:`subscribe` to a channel and
receive events on an ``asyncio.Queue``.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass
class Event:
    name: str
    data: str  # already-rendered payload (HTML partial or small text)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    def publish(self, channel: str, event: Event) -> None:
        for q in list(self._subscribers.get(channel, ())):
            q.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[channel].add(q)
        try:
            yield q
        finally:
            self._subscribers[channel].discard(q)


bus = EventBus()
