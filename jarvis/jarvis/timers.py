"""Timers and reminders in the house time zone, spoken in the Bereich that set them."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Awaitable, Callable

from .db import Database
from .settings import Settings

log = logging.getLogger(__name__)

FireCallback = Callable[[dict], Awaitable[None]]


class TimerService:
    def __init__(self, db: Database, settings: Settings):
        self._db = db
        self._settings = settings
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._fire: FireCallback | None = None

    def start(self, fire: FireCallback) -> None:
        self._fire = fire
        self._task = asyncio.create_task(self._run(), name="timers")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def parse_at(self, text: str) -> float:
        """ISO local time like 2026-11-12T18:30 in the house time zone."""
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self._settings.tz)
        return moment.timestamp()

    def add(
        self,
        *,
        kind: str,
        due_at: float,
        label: str,
        text: str,
        bereich_id: str | None,
        lang: str,
    ) -> dict:
        if due_at <= time.time():
            raise ValueError("Zeitpunkt liegt in der Vergangenheit")
        if due_at - time.time() > 366 * 86400:
            raise ValueError("höchstens ein Jahr im Voraus")
        timer_id = uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT INTO timers(id, kind, label, text, due_at, bereich_id, lang, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (timer_id, kind, label[:80], text[:300], due_at, bereich_id, lang, time.time()),
        )
        self._wake.set()
        return self.get(timer_id) or {}

    def get(self, timer_id: str) -> dict | None:
        return self._db.one("SELECT * FROM timers WHERE id = ?", (timer_id,))

    def list(self, bereich_id: str | None = None) -> list[dict]:
        if bereich_id:
            return self._db.all(
                "SELECT * FROM timers WHERE bereich_id = ? ORDER BY due_at", (bereich_id,)
            )
        return self._db.all("SELECT * FROM timers ORDER BY due_at")

    def cancel(self, *, bereich_id: str | None, label: str | None = None, timer_id: str | None = None) -> int:
        if timer_id:
            cur = self._db.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
        elif label:
            cur = self._db.execute(
                "DELETE FROM timers WHERE lower(label) = lower(?) AND (bereich_id = ? OR ? IS NULL)",
                (label, bereich_id, bereich_id),
            )
        else:
            cur = self._db.execute(
                "DELETE FROM timers WHERE bereich_id = ? OR ? IS NULL", (bereich_id, bereich_id)
            )
        self._wake.set()
        return cur.rowcount

    def describe(self, timer: dict) -> str:
        local = datetime.fromtimestamp(timer["due_at"], self._settings.tz)
        return local.strftime("%d.%m. %H:%M")

    async def _run(self) -> None:
        while True:
            self._wake.clear()
            now = time.time()
            due = self._db.all("SELECT * FROM timers WHERE due_at <= ? ORDER BY due_at", (now,))
            for timer in due:
                self._db.execute("DELETE FROM timers WHERE id = ?", (timer["id"],))
                if self._fire:
                    try:
                        await self._fire(timer)
                    except Exception:  # noqa: BLE001
                        log.exception("timer %s failed", timer["id"])
            nxt = self._db.one("SELECT MIN(due_at) AS due FROM timers")
            delay = 30.0
            if nxt and nxt["due"] is not None:
                delay = max(0.05, min(delay, nxt["due"] - time.time()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
