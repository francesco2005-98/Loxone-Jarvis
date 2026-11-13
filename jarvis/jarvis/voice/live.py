"""Live mode: a browser (tablet, phone) as microphone and speaker.

The browser streams 16 kHz PCM only while the Live page is open. Speech
segments are transcribed locally and only a sentence that starts with the wake
word reaches the brain, with the command in the same breath ("Jarvis, Licht
an") or in the next sentence. The answer goes back as text plus Piper audio
that the browser plays itself.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections import deque
from typing import TYPE_CHECKING, Awaitable, Callable

from ..areas import Area
from ..audio import wav
from .arbitration import FOLLOWUP_SECONDS, AreaSession
from .vad import FRAME_BYTES, Endpointer, VadConfig, high_pass

if TYPE_CHECKING:
    from ..house import House

log = logging.getLogger(__name__)

LISTEN_SECONDS = 8.0
MAX_BACKLOG = 2
PREROLL_FRAMES = 10

# Spellings Whisper produces for "Jarvis" in German and English speech.
WAKE = re.compile(
    r"\b(jarvis|jarviz|jarwis|jarvi|jarves|javis|jervis|dschawis|dschavis|dschaavis|tschawis|tschavis|chavis)\b",
    re.IGNORECASE,
)
WAKE_LEAD = 24


def split_wake(text: str) -> tuple[bool, str]:
    """(woke, rest): the wake word near the start of the sentence and what follows it."""
    match = WAKE.search(text or "")
    if match is None or match.start() > WAKE_LEAD:
        return False, ""
    rest = text[match.end() :].strip(" \t,.;:!?-–—\"'„“")
    return True, rest


def _has_words(text: str) -> bool:
    return sum(ch.isalpha() for ch in text) >= 2


class Segmenter:
    """Cuts the endless browser stream into spoken sentences."""

    def __init__(self, noise_filter: int):
        base = VadConfig.for_noise_filter(noise_filter)
        self.config = VadConfig(
            margin_db=base.margin_db,
            floor_dbfs=base.floor_dbfs,
            end_silence_ms=700,
            no_speech_timeout_ms=10**9,
            max_utterance_ms=12000,
        )
        self._noise: float | None = None
        self.reset()

    def reset(self) -> None:
        self._endpointer = Endpointer(self.config)
        self._endpointer._noise = self._noise
        self._pre: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._audio = bytearray()
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer += chunk
        done: list[bytes] = []
        while len(self._buffer) >= FRAME_BYTES:
            frame, self._buffer = self._buffer[:FRAME_BYTES], self._buffer[FRAME_BYTES:]
            ep = self._endpointer
            was_speaking = ep.speech_started
            result = ep.feed(frame)
            if ep.speech_started:
                if not was_speaking:
                    self._audio.extend(b"".join(self._pre))
                self._audio.extend(frame)
            else:
                self._pre.append(frame)
            if result in ("end", "timeout"):
                self._noise = ep._noise
                segment = bytes(self._audio) if result == "end" else b""
                self.reset()
                if segment:
                    done.append(segment)
        return done


class LiveSession:
    """One browser in Live mode."""

    def __init__(self, house: "House", send: Callable[[dict], Awaitable[None]]):
        self.house = house
        self._send = send
        self._send_lock = asyncio.Lock()
        self.session = AreaSession("live")
        self.area = self._resolve_area(None)
        self.phase = "idle"
        self.until = 0.0
        self.segmenter = Segmenter(int(house.settings.get("mic.noise_filter")))
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._played = asyncio.Event()

    # ----------------------------------------------------------- browser
    def _resolve_area(self, area_id: str | None) -> Area:
        """Room context for sentences without a room. The answer never goes to its zone."""
        area = self.house.areas.get(area_id) if area_id else None
        return area or Area("live", "Live", None, None, [])

    async def send(self, message: dict) -> None:
        async with self._send_lock:
            await self._send(message)

    async def command(self, message: dict) -> None:
        kind = message.get("type")
        if kind in ("hello", "area"):
            self.area = self._resolve_area(str(message.get("area") or "") or None)
            await self.send({"type": "ready", "area": self.area.public(), "phase": self.phase})
        elif kind == "played":
            self._played.set()

    def feed(self, data: bytes) -> None:
        if self.phase in ("thinking", "speaking"):
            return
        for segment in self.segmenter.feed(data):
            while self.queue.qsize() >= MAX_BACKLOG:
                self.queue.get_nowait()
            self.queue.put_nowait(segment)

    async def _render(self, text: str, lang: str) -> tuple[str | None, float]:
        """Piper audio as base64 WAV for the browser; the Audioserver stays out of Live answers."""
        try:
            rendered = await self.house.speech.render(text, lang, lead_ms=0)
        except Exception as exc:  # noqa: BLE001
            log.warning("live answer without audio: %s", exc)
            self.house.events.log("audio", "Live-Antwort ohne Ton", str(exc)[:200])
            return None, 0.0
        return base64.b64encode(wav.encode(rendered)).decode("ascii"), rendered.duration

    async def announce(self, text: str, lang: str) -> None:
        """A timer or reminder set from this browser rings here."""
        audio, _ = await self._render(text, lang)
        await self.send({"type": "announce", "text": text, "audio": audio})

    async def _state(self, phase: str, until: float = 0.0) -> None:
        self.phase = phase
        self.until = until
        await self.send({"type": "state", "phase": phase})

    # ------------------------------------------------------------ worker
    async def run(self) -> None:
        await self.send({"type": "state", "phase": self.phase})
        while True:
            timeout = None
            if self.phase in ("listening", "followup"):
                timeout = max(0.05, self.until - time.monotonic())
            try:
                segment = await asyncio.wait_for(self.queue.get(), timeout)
            except asyncio.TimeoutError:
                self.session.reset()
                await self._state("idle")
                continue
            try:
                await self._segment(segment)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("live segment failed")
                await self.send({"type": "error", "text": "Das hat nicht geklappt."})
                await self._state("idle")

    async def _segment(self, pcm: bytes) -> None:
        house = self.house
        transcript = await asyncio.to_thread(
            house.stt.transcribe,
            high_pass(pcm, int(house.settings.get("mic.noise_filter"))),
            model=house.settings.get("stt.model"),
            language=house.settings.get("stt.language"),
            prompt=house.coordinator._stt_prompt(),
        )
        if not transcript.usable:
            return
        woke, rest = split_wake(transcript.text)
        if self.phase == "idle":
            if not woke:
                return
            if not _has_words(rest):
                await self.send({"type": "heard", "text": transcript.text})
                await self._state("listening", time.monotonic() + LISTEN_SECONDS)
                return
            text = rest
        else:
            if woke and not _has_words(rest):
                await self._state("listening", time.monotonic() + LISTEN_SECONDS)
                return
            text = rest if woke else transcript.text
        await self._handle(text, transcript.lang, pcm)

    async def _handle(self, text: str, lang: str, pcm: bytes) -> None:
        house = self.house
        coordinator = house.coordinator
        await self._state("thinking")
        await self.send({"type": "heard", "text": text})
        started = time.monotonic()
        person_id = await asyncio.to_thread(coordinator._identify, pcm)
        reply, done = await coordinator.handle_text(self.session, self.area, text, lang, person_id, origin="live")
        latency = int((time.monotonic() - started) * 1000)
        coordinator.latencies.append(latency)
        coordinator.last = {
            "ts": time.time(),
            "area": f"Live · {self.area.name}",
            "text": text,
            "reply": reply,
            "actions": done,
            "ms": latency,
        }
        house.events.publish({"type": "utterance", "utterance": coordinator.last})
        house.events.log("voice", f"Live: „{text}“", reply[:200])
        audio, duration = await self._render(reply, lang) if reply else (None, 0.0)
        self._played.clear()
        await self._state("speaking")
        await self.send({"type": "reply", "text": reply, "actions": done, "audio": audio})
        if audio:
            try:
                await asyncio.wait_for(self._played.wait(), timeout=duration + 4.0)
            except asyncio.TimeoutError:
                pass
        self.segmenter.reset()
        while not self.queue.empty():
            self.queue.get_nowait()
        await self._state("followup", time.monotonic() + FOLLOWUP_SECONDS)
