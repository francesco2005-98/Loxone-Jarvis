"""Live events for the interface and the short activity log."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .db import Database


class EventBus:
    def __init__(self, db: Database):
        self._db = db
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def log(self, kind: str, title: str, detail: str = "") -> None:
        ts = time.time()
        self._db.execute(
            "INSERT INTO events(ts, kind, title, detail) VALUES(?,?,?,?)",
            (ts, kind, title[:200], detail[:500]),
        )
        self.publish({"type": "event", "event": {"ts": ts, "kind": kind, "title": title, "detail": detail}})

    def recent(self, limit: int = 30) -> list[dict]:
        return self._db.all("SELECT ts, kind, title, detail FROM events ORDER BY ts DESC LIMIT ?", (limit,))

    def prune(self, retention_days: int) -> None:
        self._db.execute("DELETE FROM events WHERE ts < ?", (time.time() - retention_days * 86400,))
