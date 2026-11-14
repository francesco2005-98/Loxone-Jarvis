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
import time
from collections import deque
from typing import TYPE_CHECKING, Awaitable, Callable

from ..areas import Area
from ..audio import wav
from .arbitration import AreaSession
from .stt import KROKO
from .vad import FRAME_BYTES, Endpointer, VadConfig, high_pass
from .wake import WAKE

if TYPE_CHECKING:
    from ..house import House

log = logging.getLogger(__name__)

LISTEN_SECONDS = 8.0
MAX_BACKLOG = 2
PREROLL_FRAMES = 10
# While a sentence is still running, the start is checked for the wake word,
# so the device can show "listening" before the speaker has finished.
EARLY_CHECKS_MS = (700, 1400)
SPECULATE_MS = 250
UTTERANCE_SECONDS = 12.0
WAKE_LEAD = 24


def early_model(model: str) -> str:
    """Kroko is fast enough for the early check; with Whisper the small base model does it."""
    return KROKO if model == KROKO else "base"


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
            end_silence_ms=550,
            no_speech_timeout_ms=10**9,
            max_utterance_ms=12000,
        )
        self._noise: float | None = None
        self.count = 0
        self.reset()

    @property
    def speaking(self) -> bool:
        return self._endpointer.speech_started

    @property
    def key(self) -> tuple[int, int]:
        """Identifies the sentence and its last spoken frame; stays the same through a pause."""
        return self.count, self._endpointer.last_speech_ms

    @property
    def silence_ms(self) -> int:
        return self._endpointer.silence_ms

    @property
    def speech_ms(self) -> int:
        return len(self._audio) // 32

    def audio(self) -> bytes:
        return bytes(self._audio)

    def reset(self) -> None:
        self.count += 1
        self._endpointer = Endpointer(self.config)
        self._endpointer._noise = self._noise
        self._pre: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._audio = bytearray()
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[tuple[bytes, tuple[int, int]]]:
        self._buffer += chunk
        done: list[tuple[bytes, tuple[int, int]]] = []
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
                key = self.key
                self.reset()
                if segment:
                    done.append((segment, key))
        return done

    def cut(self) -> tuple[bytes, tuple[int, int]] | None:
        """End the running sentence now, as if the pause had been long enough."""
        if not self.speaking or not self._audio:
            return None
        self._noise = self._endpointer._noise
        item = (bytes(self._audio), self.key)
        self.reset()
        return item


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
        self._stt_ms = 0
        self._early: asyncio.Task | None = None
        self._spec: tuple[tuple[int, int], asyncio.Future] | None = None
        self._early_segment = 0
        self._early_step = 0

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
        self.segmenter.config.end_silence_ms = int(self.house.settings.get("voice.end_silence_ms"))
        for item in self.segmenter.feed(data):
            self._enqueue(item)
        self._maybe_early()
        self._speculate()

    def _enqueue(self, item: tuple[bytes, tuple[int, int]]) -> None:
        while self.queue.qsize() >= MAX_BACKLOG:
            self.queue.get_nowait()
        self.queue.put_nowait(item)

    def _speculate(self) -> None:
        """Start recognition on a short pause; the result is used if the pause ends the sentence."""
        seg = self.segmenter
        if not seg.speaking or not self.house.settings.get("voice.speculative"):
            return
        if self._spec is not None and self._spec[0] != seg.key:
            self._spec[1].cancel()
            self._spec = None
        if self._spec is None and seg.silence_ms >= SPECULATE_MS:
            key = seg.key
            future = asyncio.ensure_future(self._transcribe(seg.audio()))
            self._spec = (key, future)
            future.add_done_callback(lambda done, key=key: self._maybe_cut(key, done))

    def _maybe_cut(self, key: tuple[int, int], done: asyncio.Future) -> None:
        """A finished instant command needs no longer pause: answer right away."""
        if done.cancelled() or done.exception() is not None:
            return
        seg = self.segmenter
        if seg.key != key or not seg.speaking or self.phase in ("thinking", "speaking"):
            return
        transcript = done.result()
        if not transcript.usable or not self.house.coordinator.complete(transcript.text, self.session):
            return
        item = seg.cut()
        if item is not None:
            self._enqueue(item)

    async def _transcribe(self, pcm: bytes):
        house = self.house
        return await asyncio.to_thread(
            house.stt.transcribe,
            high_pass(pcm, int(house.settings.get("mic.noise_filter"))),
            model=house.settings.get("stt.model"),
            language=house.settings.get("stt.language"),
            prompt=house.coordinator._stt_prompt(),
        )

    def _maybe_early(self) -> None:
        seg = self.segmenter
        if self.phase != "idle" or not seg.speaking or self._early is not None:
            return
        if not self.house.settings.get("live.early_wake"):
            return
        if self._early_segment != seg.count:
            self._early_segment = seg.count
            self._early_step = 0
        if self._early_step < len(EARLY_CHECKS_MS) and seg.speech_ms >= EARLY_CHECKS_MS[self._early_step]:
            self._early_step += 1
            self._early = asyncio.create_task(self._early_wake(seg.audio(), seg.count))

    async def _early_wake(self, pcm: bytes, segment: int) -> None:
        house = self.house
        language = house.settings.get("stt.language")
        try:
            transcript = await asyncio.to_thread(
                house.stt.transcribe,
                high_pass(pcm, int(house.settings.get("mic.noise_filter"))),
                model=early_model(house.settings.get("stt.model")),
                language=language if language in ("de", "en") else "de",
                prompt="Jarvis",
            )
        except Exception:  # noqa: BLE001
            log.debug("early wake check failed", exc_info=True)
            return
        finally:
            self._early = None
        if segment != self.segmenter.count or self.phase != "idle":
            return
        if split_wake(transcript.text)[0]:
            await self._state("listening", time.monotonic() + UTTERANCE_SECONDS + LISTEN_SECONDS)

    async def _render(self, text: str, lang: str) -> tuple[str | None, float]:
        """Piper audio as base64 WAV for the browser; the Audioserver stays out of Live answers."""
        try:
            rendered = await self.house.speech.render(text, lang, lead_ms=0)
        except Exception as exc:  # noqa: BLE001
            log.warning("live answer without audio: %s", exc)
            self.house.events.log("audio", "Live-Antwort ohne Ton", str(exc)[:200])
            return None, 0.0
        return base64.b64encode(wav.encode(rendered)).decode("ascii"), rendered.duration

    async def _wait_played(self, audio: str | None, duration: float) -> None:
        if not audio:
            return
        try:
            await asyncio.wait_for(self._played.wait(), timeout=duration + 4.0)
        except asyncio.TimeoutError:
            pass

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
                segment, key = await asyncio.wait_for(self.queue.get(), timeout)
            except asyncio.TimeoutError:
                self.session.reset()
                await self._state("idle")
                continue
            try:
                await self._segment(segment, key)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("live segment failed")
                await self.send({"type": "error", "text": "Das hat nicht geklappt."})
                await self._state("idle")

    async def _segment(self, pcm: bytes, key: tuple[int, int] | None = None) -> None:
        if self.phase == "listening":
            self.until = min(self.until, time.monotonic() + LISTEN_SECONDS)
        started = time.monotonic()
        spec, self._spec = self._spec, None
        if spec is not None and key is not None and spec[0] == key:
            transcript = await spec[1]
        else:
            transcript = await self._transcribe(pcm)
        self._stt_ms = int((time.monotonic() - started) * 1000)
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
        brain_ms = int((time.monotonic() - started) * 1000)
        latency = brain_ms + self._stt_ms
        coordinator.latencies.append(latency)
        coordinator.last = {
            "ts": time.time(),
            "area": f"Live · {self.area.name}",
            "text": text,
            "reply": reply,
            "actions": done,
            "ms": latency,
            "timing": {"stt": self._stt_ms, "brain": brain_ms},
        }
        house.events.publish({"type": "utterance", "utterance": coordinator.last})
        house.events.log("voice", f"Live: „{text}“", reply[:200])
        voice_started = time.monotonic()
        audio, duration = await self._render(reply, lang) if reply else (None, 0.0)
        coordinator.last["timing"]["voice"] = int((time.monotonic() - voice_started) * 1000)
        self._played.clear()
        await self._state("speaking")
        await self.send({"type": "reply", "text": reply, "actions": done, "audio": audio})
        await self._wait_played(audio, duration)
        offer = await coordinator.take_suggestion(self.session)
        if offer is not None:
            audio, duration = await self._render(offer.question, lang)
            self._played.clear()
            await self.send({"type": "reply", "text": offer.question, "actions": [], "audio": audio})
            await self._wait_played(audio, duration)
        self.segmenter.reset()
        while not self.queue.empty():
            self.queue.get_nowait()
        seconds = float(self.house.settings.get("voice.followup_s"))
        if self.session.pending is not None:
            seconds = max(seconds, 6.0)
        if seconds > 0:
            await self._state("followup", time.monotonic() + seconds)
        else:
            self.session.reset()
            await self._state("idle")
