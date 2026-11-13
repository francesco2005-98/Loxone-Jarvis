"""Speak a reply in one or more Bereiche, with their microphones muted meanwhile."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ..areas import Area
from . import wav
from .audioserver import UPLOAD_RATE

if TYPE_CHECKING:
    from ..house import House

log = logging.getLogger(__name__)

UNMUTE_DELAY = 0.4


class Speech:
    def __init__(self, house: "House"):
        self.house = house
        self._locks: dict[str, asyncio.Lock] = {}
        self.last_start_latency: float | None = None

    async def render(self, text: str, lang: str, lead_ms: int | None = None) -> wav.Pcm:
        """Lead silence protects the first syllable on the Audioserver; a browser plays it right away."""
        raw = await self.house.piper.synthesize(text, lang)
        pcm = wav.resample(wav.decode(raw), UPLOAD_RATE)
        lead = int(self.house.settings.get("announce.silence_ms")) if lead_ms is None else lead_ms
        return wav.with_silence(pcm, lead, tail_ms=150 if lead_ms is not None else 250)

    def _volume(self, area: Area) -> int:
        fixed = int(self.house.settings.get("announce.volume"))
        if fixed > 0:
            return fixed
        current = self.house.miniserver.state(area.zone_uuid, "volume") if area.zone_uuid else None
        try:
            return max(15, min(100, int(float(current)))) if current is not None else 40
        except (TypeError, ValueError):
            return 40

    async def say(self, text: str, lang: str, areas: list[Area]) -> bool:
        """Returns True when the Audioserver confirmed the announcement."""
        targets: list[tuple[int, int]] = []
        speaking: list[Area] = []
        for area in areas:
            zone = self.house.zone_of(area)
            if zone is None or zone.details.get("playerid") is None:
                continue
            targets.append((int(zone.details["playerid"]), self._volume(area)))
            speaking.append(area)
        if not targets:
            self.house.events.log("audio", "Keine Audio-Zone für die Antwort", ", ".join(a.name for a in areas))
            return False
        locks = [self._locks.setdefault(a.id, asyncio.Lock()) for a in speaking]
        for lock in locks:
            await lock.acquire()
        coordinator = self.house.coordinator
        try:
            pcm = await self.render(text, lang)
            for area in speaking:
                coordinator.set_speaking(area.id, True)
            started = time.monotonic()
            announcement = await self.house.audioserver.announce(targets, wav.encode(pcm))
            try:
                await asyncio.wait_for(announcement.began.wait(), timeout=4.0)
                self.last_start_latency = time.monotonic() - started
                await asyncio.wait_for(announcement.finished.wait(), timeout=pcm.duration + 5.0)
            except asyncio.TimeoutError:
                remaining = pcm.duration + 1.0 - (time.monotonic() - started)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("announcement failed: %s", exc)
            self.house.events.log("audio", "Ansage fehlgeschlagen", str(exc)[:200])
            return False
        finally:
            await asyncio.sleep(UNMUTE_DELAY)
            for area in speaking:
                coordinator.set_speaking(area.id, False)
            for lock in locks:
                lock.release()
