"""Speak a reply in one or more Bereiche. Saying "Jarvis" meanwhile cuts it off."""

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
BEGIN_TIMEOUT = 4.0


class Speech:
    def __init__(self, house: "House"):
        self.house = house
        self._locks: dict[str, asyncio.Lock] = {}
        self._cuts: dict[str, asyncio.Event] = {}
        self._interrupted_at: dict[str, float] = {}
        self.last_start_latency: float | None = None
        self.last_voice_ms: int | None = None
        self.last_timing: dict[str, int] = {}
        self.last_heard = False

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

    def interrupt(self, area_id: str) -> bool:
        """Stop waiting for the running answer in this Bereich. True if one was running."""
        self._interrupted_at[area_id] = time.monotonic()
        cut = self._cuts.get(area_id)
        if cut is None:
            return False
        cut.set()
        return True

    def interrupted_since(self, area_id: str, moment: float) -> bool:
        return self._interrupted_at.get(area_id, float("-inf")) >= moment

    async def say(self, text: str, lang: str, areas: list[Area]) -> bool:
        """Returns True when the Audioserver confirmed the announcement."""
        chosen: list[tuple[Area, object]] = []
        for area in areas:
            zone = self.house.zone_of(area)
            if zone is None or zone.details.get("playerid") is None:
                continue
            chosen.append((area, zone))
        if not chosen:
            self.house.events.log("audio", "Keine Audio-Zone für die Antwort", ", ".join(a.name for a in areas))
            return False
        self.last_heard = False
        speaking = [area for area, _ in chosen]
        locks = [self._locks.setdefault(a.id, asyncio.Lock()) for a in speaking]
        for lock in locks:
            await lock.acquire()
        coordinator = self.house.coordinator
        cut = asyncio.Event()
        for area in speaking:
            self._cuts[area.id] = cut
        try:
            # The Audioserver connection and the voice are prepared together.
            # After a pause the first reply otherwise waits while the link is rebuilt.
            warming = [asyncio.ensure_future(self.house.audioserver.warm(zone)) for _, zone in chosen]
            render_started = time.monotonic()
            try:
                pcm = await self.render(text, lang)
            finally:
                for task in warming:
                    try:
                        await task
                    except Exception:  # noqa: BLE001
                        log.debug("audioserver warm failed", exc_info=True)
            if cut.is_set():
                return False
            targets: list[tuple[int, int, str | None]] = []
            for area, zone in chosen:
                player, host = await self.house.audioserver.player_for(zone)
                targets.append((player, self._volume(area), host))
            for area in speaking:
                coordinator.set_speaking(area.id, True, text)
            encoded = wav.encode(pcm)
            played = False
            for attempt in (1, 2):
                if cut.is_set():
                    break
                try:
                    announcement = await self.house.audioserver.announce(targets, encoded)
                except Exception as exc:  # noqa: BLE001
                    log.warning("announcement failed: %s", exc)
                    if attempt == 2:
                        self.house.events.log("audio", "Ansage fehlgeschlagen", str(exc)[:200])
                        return False
                    self.house.events.log("audio", "Ansage wird wiederholt", str(exc)[:160])
                    continue
                started = time.monotonic()
                heard = await self._wait_playback(announcement, cut, pcm, started, render_started)
                if heard:
                    played = True
                    break
                if cut.is_set():
                    break
                await self.house.audioserver.cut(announcement)
                if attempt == 1:
                    self.house.events.log("audio", "Ansage hat nicht begonnen", "zweiter Versuch")
            if not played:
                self.house.events.log("audio", "Ansage hat nicht begonnen", "")
            return played
        except Exception as exc:  # noqa: BLE001
            log.warning("announcement failed: %s", exc)
            self.house.events.log("audio", "Ansage fehlgeschlagen", str(exc)[:200])
            return False
        finally:
            for area in speaking:
                if self._cuts.get(area.id) is cut:
                    del self._cuts[area.id]
            if not cut.is_set():
                await asyncio.sleep(UNMUTE_DELAY)
            for area in speaking:
                coordinator.set_speaking(area.id, False)
            for lock in locks:
                lock.release()

    async def _wait_playback(self, announcement, cut: asyncio.Event, pcm: wav.Pcm, started: float, render_started: float) -> bool:
        """True when the Audioserver started the file, or the speaker interrupted it."""
        try:
            if await _until(announcement.began, cut, BEGIN_TIMEOUT):
                began = time.monotonic()
                self.last_start_latency = began - started
                self.last_voice_ms = int((began - render_started) * 1000)
                self.last_timing = {
                    "tts": int((started - render_started) * 1000),
                    "upload": announcement.upload_ms,
                    "server": int((began - started) * 1000),
                }
                await _until(announcement.finished, cut, pcm.duration + 5.0)
                heard = True
            else:
                heard = False
        except asyncio.TimeoutError:
            heard = announcement.began.is_set()
            if heard:
                remaining = pcm.duration + 1.0 - (time.monotonic() - started)
                if remaining > 0 and not cut.is_set():
                    try:
                        await asyncio.wait_for(cut.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        pass
        if cut.is_set() and not announcement.finished.is_set():
            self.house.background(self.house.audioserver.cut(announcement), "cut announcement")
        self.last_heard = heard
        return heard


async def _until(event: asyncio.Event, cut: asyncio.Event, timeout: float) -> bool:
    """Wait for event, unless cut comes first. True when event fired; raises TimeoutError otherwise."""
    if event.is_set():
        return True
    waiters = [asyncio.ensure_future(event.wait()), asyncio.ensure_future(cut.wait())]
    try:
        done, _ = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
    if not done:
        raise asyncio.TimeoutError
    return event.is_set()
