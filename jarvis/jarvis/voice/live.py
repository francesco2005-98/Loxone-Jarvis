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
from ..brain.tools import Turn
from .coordinator import _missed, hold_line
from .wake import has_words, split_wake

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
def early_model(model: str) -> str:
    """Kroko is fast enough for the early check; with Whisper the small base model does it."""
    return KROKO if model == KROKO else "base"


class Segmenter:
    """Cuts the endless browser stream into spoken sentences."""

    def __init__(self, noise_filter: int):
        base = VadConfig.for_noise_filter(noise_filter)
        self.config = VadConfig(
            margin_db=base.margin_db,
            floor_dbfs=base.floor_dbfs,
            end_silence_ms=base.end_silence_ms,
            no_speech_timeout_ms=10**9,
            max_utterance_ms=base.max_utterance_ms,
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
        self._job: asyncio.Task | None = None
        self._gen = 0

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
        # Audio keeps flowing while Jarvis thinks or speaks, so a new "Jarvis" can cut him off.
        self.segmenter.config.end_silence_ms = int(self.house.settings.get("voice.end_silence_ms"))
        busy = self.phase in ("thinking", "speaking")
        for item in self.segmenter.feed(data):
            self._enqueue(item)
        if busy:
            return
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
            self.session.woke()
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
            if self.phase in ("listening", "followup") and (self._job is None or self._job.done()):
                timeout = max(0.05, self.until - time.monotonic())
            try:
                segment, key = await asyncio.wait_for(self.queue.get(), timeout)
            except asyncio.TimeoutError:
                self.session.reset()
                await self._state("idle")
                continue
            self._dispatch(segment, key)

    def _dispatch(self, segment: bytes, key: tuple[int, int]) -> None:
        if self._job and not self._job.done() and self.phase in ("thinking", "speaking"):
            asyncio.create_task(self._preempt(segment, key))
            return
        self._gen += 1
        gen = self._gen
        self._job = asyncio.create_task(self._guarded(segment, key, gen))

    async def _guarded(self, segment: bytes, key: tuple[int, int], gen: int) -> None:
        try:
            await self._segment(segment, key, gen)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            if self._gen != gen:
                return
            log.exception("live segment failed")
            await self.send({"type": "error", "text": "Das hat nicht geklappt."})
            await self._end()

    async def _preempt(self, segment: bytes, key: tuple[int, int]) -> None:
        """A sentence spoken while Jarvis is busy. Only "Jarvis" replaces what he is doing."""
        try:
            transcript = await self._transcribe(segment)
        except Exception:  # noqa: BLE001
            log.debug("interrupt transcription failed", exc_info=True)
            return
        if self.phase not in ("thinking", "speaking"):
            # He finished while we were listening: the sentence is an ordinary next command.
            self._dispatch(segment, key)
            return
        if not transcript.usable:
            return
        woke, rest = split_wake(transcript.text)
        if not woke:
            return
        self._gen += 1
        gen = self._gen
        if self._job and not self._job.done():
            self._job.cancel()
        self.session.interrupt()
        await self.send({"type": "stop"})
        self.house.events.log("voice", "Live unterbrochen", rest[:80])
        if not has_words(rest):
            await self._state("listening", time.monotonic() + LISTEN_SECONDS)
            return
        self._job = asyncio.create_task(self._guarded_text(rest, transcript.lang, segment, gen))

    async def _guarded_text(self, text: str, lang: str, pcm: bytes, gen: int) -> None:
        try:
            await self._handle(text, lang, pcm, followup=False, gen=gen)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            if self._gen != gen:
                return
            log.exception("live segment failed")
            await self.send({"type": "error", "text": "Das hat nicht geklappt."})
            await self._end()

    async def _segment(self, pcm: bytes, key: tuple[int, int] | None = None, gen: int | None = None) -> None:
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
            if self.phase == "listening":
                await self._play_line(_missed("de"), "de", gen)
                if self._current(gen):
                    await self._state("listening", time.monotonic() + LISTEN_SECONDS)
            return
        woke, rest = split_wake(transcript.text)
        followup = self.phase == "followup" and not woke
        if woke:
            self.session.woke()
        if self.phase == "idle":
            if not woke:
                return
            if not has_words(rest):
                await self.send({"type": "heard", "text": transcript.text})
                await self._state("listening", time.monotonic() + LISTEN_SECONDS)
                return
            text = rest
        else:
            if woke and not has_words(rest):
                await self._state("listening", time.monotonic() + LISTEN_SECONDS)
                return
            text = rest if woke else transcript.text
        await self._handle(text, transcript.lang, pcm, followup, gen)

    def _current(self, gen: int | None) -> bool:
        return gen is None or self._gen == gen

    async def _handle(self, text: str, lang: str, pcm: bytes, followup: bool = False, gen: int | None = None) -> None:
        house = self.house
        coordinator = house.coordinator
        if not self._current(gen):
            return
        await self._state("thinking")
        started = time.monotonic()
        person_id = await asyncio.to_thread(coordinator._identify, pcm)
        if not self._current(gen):
            return
        anrede = coordinator._anrede(Turn(area=self.area, lang=lang, text=text, person_id=person_id))

        async def hold() -> None:
            await self._play_line(hold_line(lang, anrede), lang, gen)

        reply, done = await coordinator.handle_text(
            self.session, self.area, text, lang, person_id, origin="live", followup=followup, hold=hold
        )
        if not self._current(gen):
            return
        if not reply and not done:
            if followup:
                house.events.log("voice", f"Live, nicht an Jarvis: „{text}“", "Gespräch beendet")
                await self._end()
                return
            await self._play_line(_missed(lang), lang, gen)
            if self._current(gen):
                await self._state("listening", time.monotonic() + LISTEN_SECONDS)
            return
        await self.send({"type": "heard", "text": text})
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
        if not self._current(gen):
            return
        offer = await coordinator.take_suggestion(self.session)
        if not self._current(gen):
            return
        if offer is not None:
            audio, duration = await self._render(offer.question, lang)
            self._played.clear()
            await self.send({"type": "reply", "text": offer.question, "actions": [], "audio": audio})
            await self._wait_played(audio, duration)
            if not self._current(gen):
                return
        # A sentence already spoken during the answer stays queued. Only drop the
        # tail of this device's own voice when the user is not already talking.
        if not self.segmenter.speaking:
            self.segmenter.reset()
        seconds = float(self.house.settings.get("voice.followup_s"))
        if self.session.pending is not None:
            seconds = max(seconds, 6.0)
        if seconds > 0 and self.session.keep_talking(float(self.house.settings.get("voice.conversation_s"))):
            await self._state("followup", time.monotonic() + seconds)
        else:
            await self._end()

    async def _play_line(self, text: str, lang: str, gen: int | None) -> None:
        """One short line on this device. Used when a search runs long, or nothing was understood."""
        if not self._current(gen):
            return
        audio, duration = await self._render(text, lang)
        if not self._current(gen):
            return
        self._played.clear()
        await self._state("speaking")
        await self.send({"type": "reply", "text": text, "actions": [], "audio": audio})
        await self._wait_played(audio, duration)

    async def _end(self) -> None:
        """Back to waiting for "Jarvis", with a fresh conversation next time."""
        self.session.reset()
        self.segmenter.reset()
        await self._state("idle")
