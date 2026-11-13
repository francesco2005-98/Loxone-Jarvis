"""From wake word to spoken answer, once per Bereich, several Bereiche in parallel."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..areas import Area
from ..brain import confirm, fallback, quick
from ..brain.gemini import BrainUnavailable
from ..brain.tools import Pending, Turn
from ..people import ENROLL_SENTENCES
from .arbitration import ARBITRATION_WINDOW, FOLLOWUP_SECONDS, AreaSession, Offer, Phase, rms
from .speaker import identify, profile_from
from .vad import Endpointer, VadConfig, high_pass

if TYPE_CHECKING:
    from ..house import House
    from .mics import Mic

log = logging.getLogger(__name__)

LEVEL_INTERVAL = 0.06


@dataclass
class Run:
    mic: "Mic"
    area_id: str | None
    kind: str  # command | followup | enroll
    endpointer: Endpointer
    audio: bytearray = field(default_factory=bytearray)
    state: str = "collecting"
    started: float = field(default_factory=time.monotonic)


@dataclass
class EnrollJob:
    person_id: str
    mic_id: str
    step: int = 0
    retries: int = 0
    embeddings: list = field(default_factory=list)


class Coordinator:
    def __init__(self, house: "House"):
        self.house = house
        self.sessions: dict[str, AreaSession] = {}
        self.runs: dict[str, Run] = {}
        self.speaking: set[str] = set()
        self.enroll: dict[str, EnrollJob] = {}
        self.latencies: deque[int] = deque(maxlen=20)
        self.last: dict | None = None
        self._level_at: dict[str, float] = {}

    # ------------------------------------------------------------ helpers
    def session(self, area_id: str) -> AreaSession:
        session = self.sessions.get(area_id)
        if session is None:
            session = self.sessions[area_id] = AreaSession(area_id)
        return session

    def _phase(self, area_id: str) -> None:
        session = self.sessions.get(area_id)
        phase = session.phase.value if session else "idle"
        if area_id in self.speaking:
            phase = "speaking"
        self.house.events.publish({"type": "phase", "area": area_id, "phase": phase})

    def _level(self, mic: "Mic", data: bytes) -> None:
        now = time.monotonic()
        if now - self._level_at.get(mic.id, 0.0) < LEVEL_INTERVAL:
            return
        self._level_at[mic.id] = now
        level = min(1.0, rms(data) / 6000.0)
        mic.level = level
        self.house.events.publish({"type": "level", "mic": mic.id, "area": mic.area_id, "level": round(level, 3)})

    def set_speaking(self, area_id: str, active: bool) -> None:
        if active:
            self.speaking.add(area_id)
        else:
            self.speaking.discard(area_id)
        self.house.mics.mute_area(area_id, active)
        self._phase(area_id)

    @property
    def average_latency(self) -> int | None:
        return int(sum(self.latencies) / len(self.latencies)) if self.latencies else None

    # -------------------------------------------------------- device side
    async def on_start(self, mic: "Mic", conversation_id: str, wake_word: str | None) -> int | None:
        mics = self.house.mics
        if mic.id in self.enroll and wake_word is None:
            run = Run(mic, None, "enroll", Endpointer(VadConfig(no_speech_timeout_ms=8000)), state="listening")
            self.runs[mic.id] = run
            mics.send(mic, "RUN_START")
            mics.send(mic, "STT_START")
            return 0
        if not mic.area_id or self.house.areas.get(mic.area_id) is None:
            self.house.events.log("mic", f"{mic.label} hat keinen Bereich", "Auf der Mikrofonseite zuordnen")
            return None
        if mic.area_id in self.speaking:
            return None
        session = self.session(mic.area_id)
        offer = session.offer(mic.id, wake_word is not None)
        if offer == Offer.REJECT:
            return None
        noise = int(self.house.settings.get("mic.noise_filter"))
        if offer == Offer.FOLLOWUP:
            run = Run(mic, mic.area_id, "followup", Endpointer(VadConfig.for_noise_filter(noise, True)), state="listening")
            self.runs[mic.id] = run
            mics.send(mic, "RUN_START")
            mics.send(mic, "STT_START")
        else:
            self.runs[mic.id] = Run(mic, mic.area_id, "command", Endpointer(VadConfig.for_noise_filter(noise)))
            mics.send(mic, "RUN_START")
            if len(session.candidates) == 1:
                area_id = mic.area_id
                asyncio.get_running_loop().call_later(ARBITRATION_WINDOW, self._decide, area_id)
        self._phase(mic.area_id)
        return 0

    def on_audio(self, mic: "Mic", data: bytes) -> None:
        run = self.runs.get(mic.id)
        if run is None or run.state == "done":
            return
        self._level(mic, data)
        if run.state == "collecting":
            self.session(run.area_id).feed(mic.id, data)
            return
        run.audio.extend(data)
        result = run.endpointer.feed(data)
        if result != "continue":
            self._finish(run, result)

    async def on_stop(self, mic: "Mic", abort: bool) -> None:
        run = self.runs.get(mic.id)
        if run is None:
            return
        if run.state == "collecting":
            self.runs.pop(mic.id, None)
            if run.area_id:
                self.session(run.area_id).drop(mic.id)
                self._phase(run.area_id)
        elif run.state == "listening":
            self._finish(run, "end" if run.endpointer.speech_started else "timeout")

    def _decide(self, area_id: str) -> None:
        session = self.sessions.get(area_id)
        if session is None or session.phase != Phase.COLLECTING:
            return
        winner, losers = session.decide()
        for loser in losers:
            run = self.runs.pop(loser, None)
            if run:
                self.house.mics.send(run.mic, "RUN_END")
        run = self.runs.get(winner) if winner else None
        if run is None:
            session.reset()
            self._phase(area_id)
            return
        run.state = "listening"
        self.house.mics.send(run.mic, "STT_START")
        buffered = session.buffered_audio()
        run.audio.extend(buffered)
        self._phase(area_id)
        result = run.endpointer.feed(buffered)
        if result != "continue":
            self._finish(run, result)

    def _finish(self, run: Run, result: str) -> None:
        if run.state == "done":
            return
        run.state = "done"
        mics = self.house.mics
        mics.send(run.mic, "STT_VAD_END")
        audio = bytes(run.audio)
        if run.kind == "enroll":
            self.house.background(self._enroll_step(run, audio, result), "enroll")
            return
        if result == "timeout":
            mics.send(run.mic, "RUN_END")
            self.runs.pop(run.mic.id, None)
            if run.area_id:
                self.session(run.area_id).reset()
                self._phase(run.area_id)
            return
        self.house.background(self._process(run, audio), "voice")

    # ------------------------------------------------------------ process
    def _stt_prompt(self) -> str:
        names = ["Jarvis"]
        names += [a.name for a in self.house.areas.list()]
        for entry in self.house.catalog.entries(only_allowed=True):
            names.append(entry.name)
            names.extend(entry.aliases)
        seen: list[str] = []
        for name in names:
            if name and name not in seen:
                seen.append(name)
        return ", ".join(seen)[:400]

    def _identify(self, pcm: bytes) -> str | None:
        recognizer = self.house.speaker
        profiles = self.house.people.profiles()
        if not profiles or not recognizer.available:
            return None
        embedding = recognizer.embed(pcm)
        if embedding is None:
            return None
        return identify(embedding, profiles).person_id

    async def _process(self, run: Run, audio: bytes) -> None:
        house = self.house
        mics = house.mics
        session = self.session(run.area_id)
        area = house.areas.get(run.area_id)
        session.start_thinking()
        self._phase(run.area_id)
        started = time.monotonic()
        try:
            pcm = high_pass(audio, int(house.settings.get("mic.noise_filter")))
            transcript = await asyncio.to_thread(
                house.stt.transcribe,
                pcm,
                model=house.settings.get("stt.model"),
                language=house.settings.get("stt.language"),
                prompt=self._stt_prompt(),
            )
            mics.send(run.mic, "STT_END", {"text": transcript.text[:200] or " "})
            mics.send(run.mic, "INTENT_START")
            mics.send(run.mic, "INTENT_END", {"conversation_id": "", "continue_conversation": "0"})
            mics.send(run.mic, "RUN_END")
            self.runs.pop(run.mic.id, None)
            if area is None or not transcript.usable:
                session.reset()
                return
            person_id = await asyncio.to_thread(self._identify, pcm)
            reply, done = await self.handle_text(session, area, transcript.text, transcript.lang, person_id)
            latency = int((time.monotonic() - started) * 1000)
            self.latencies.append(latency)
            self.last = {
                "ts": time.time(),
                "area": area.name,
                "text": transcript.text,
                "reply": reply,
                "actions": done,
                "ms": latency,
            }
            house.events.publish({"type": "utterance", "utterance": self.last})
            house.events.log("voice", f"„{transcript.text}“", reply[:200])
            if reply:
                session.start_speaking()
                await house.speech.say(reply, transcript.lang, [area])
            if session.phase in (Phase.SPEAKING, Phase.THINKING):
                session.open_followup(run.mic.id)
                until = session.followup_until
                self._phase(area.id)
                asyncio.get_running_loop().call_later(FOLLOWUP_SECONDS + 0.5, self._expire, area.id, until)
                await mics.listen(run.mic)
        except Exception:  # noqa: BLE001
            log.exception("voice processing failed")
            mics.send(run.mic, "RUN_END")
            session.reset()
            self._phase(run.area_id)
        finally:
            if self.runs.get(run.mic.id) is run:
                self.runs.pop(run.mic.id, None)

    def _expire(self, area_id: str, until: float) -> None:
        session = self.sessions.get(area_id)
        if session and session.phase == Phase.FOLLOWUP and session.followup_until == until:
            session.reset()
            self._phase(area_id)

    async def handle_text(
        self, session: AreaSession, area: Area, text: str, lang: str, person_id: str | None, origin: str = ""
    ) -> tuple[str, list[str]]:
        if session.pending is not None:
            pending: Pending = session.pending  # type: ignore[assignment]
            session.pending = None
            answer = confirm.classify(text)
            if answer == confirm.Answer.YES:
                return await self._confirmed(pending, session, area, lang, person_id, origin)
            if answer == confirm.Answer.NO:
                return ("Abgebrochen." if lang == "de" else "Cancelled."), []
        turn = Turn(area=area, lang=lang, text=text, person_id=person_id, origin=origin)
        instant = await self._quick(turn)
        if instant is not None:
            _remember(session, text, instant)
            return instant, turn.done
        try:
            reply, history = await self.house.brain.answer(turn, session.history)
            session.history = history
        except BrainUnavailable as exc:
            self.house.events.log("ai", "Gemini nicht erreichbar", str(exc)[:200])
            reply = await self._fallback(turn)
        if turn.pending is not None:
            session.pending = turn.pending
        return reply, turn.done

    async def _confirmed(
        self, pending: Pending, session: AreaSession, area: Area, lang: str, person_id: str | None, origin: str = ""
    ) -> tuple[str, list[str]]:
        turn = Turn(area=area, lang=lang, text="ja", person_id=person_id, origin=origin)
        if pending.kind == "control":
            results = []
            for entry, action, value, value2 in pending.actions:
                try:
                    results.append(await self.house.brain.runner.execute(entry, action, value, value2, turn))
                except Exception as exc:  # noqa: BLE001
                    results.append({"error": str(exc)})
            ok = all(r.get("ok") for r in results)
            if lang == "de":
                return ("Erledigt." if ok else "Das hat nicht geklappt."), turn.done
            return ("Done." if ok else "That didn't work."), turn.done
        try:
            reply = await self.house.config_agent.resume(pending, turn)
        except BrainUnavailable:
            reply = fallback.offline_notice(lang)
        if turn.pending is not None:
            session.pending = turn.pending
        return reply, turn.done

    async def _quick(self, turn: Turn) -> str | None:
        """Plain light commands switch at once, without a round trip to Gemini."""
        command = quick.parse(turn.text)
        if command is None:
            return None
        entries, room = quick.targets(self.house, turn.area, command)
        if not entries:
            return None
        ok = True
        for entry in entries:
            try:
                result = await self.house.brain.runner.execute(entry, command.action, None, None, turn)
                ok = ok and bool(result.get("ok"))
            except Exception:  # noqa: BLE001
                log.exception("quick command failed")
                ok = False
        anrede = self.house.people.address(turn.person_id)[0] if turn.person_id else None
        return quick.reply(command, room, turn.lang, anrede, ok)

    async def _fallback(self, turn: Turn) -> str:
        command = fallback.parse(turn.text)
        if command is None:
            return fallback.offline_notice(turn.lang)
        house = self.house
        runner = house.brain.runner
        targets = []
        if command.target == "light":
            main = house.catalog.get(turn.area.main_light) if turn.area.main_light else None
            if main and main.allowed and not main.critical:
                targets = [main]
            else:
                targets = [
                    e
                    for e in house.catalog.entries(only_allowed=True)
                    if e.type == "LightControllerV2" and e.room in turn.area.rooms and not e.critical
                ]
        else:
            targets = [
                e
                for e in house.catalog.entries(only_allowed=True)
                if e.group == "shading" and e.room in turn.area.rooms and not e.critical
            ]
        ok = bool(targets)
        for entry in targets:
            try:
                result = await runner.execute(entry, command.action, None, None, turn)
                ok = ok and bool(result.get("ok"))
            except Exception:  # noqa: BLE001
                ok = False
        return fallback.reply(command, turn.lang, ok)

    # --------------------------------------------------------- enrollment
    async def start_enroll(self, person_id: str, mic_id: str) -> None:
        mic = self.house.mics.mics.get(mic_id)
        if mic is None or not mic.connected:
            raise RuntimeError("Mikrofon nicht verbunden")
        if self.house.people.get(person_id) is None:
            raise KeyError(person_id)
        if not self.house.speaker.available:
            raise RuntimeError("Sprechermodell fehlt im Container")
        self.enroll[mic_id] = EnrollJob(person_id, mic_id)
        self._enroll_publish(self.enroll[mic_id], "listening")
        if not await self.house.mics.listen(mic):
            self.enroll.pop(mic_id, None)
            raise RuntimeError("Mikrofon hört nicht zu")

    def cancel_enroll(self, mic_id: str) -> None:
        job = self.enroll.pop(mic_id, None)
        if job:
            self._enroll_publish(job, "cancelled")

    def _enroll_publish(self, job: EnrollJob, state: str) -> None:
        sentence = ENROLL_SENTENCES[job.step] if job.step < len(ENROLL_SENTENCES) else ""
        self.house.events.publish(
            {
                "type": "enroll",
                "person": job.person_id,
                "mic": job.mic_id,
                "step": job.step,
                "total": len(ENROLL_SENTENCES),
                "sentence": sentence,
                "state": state,
            }
        )

    async def _enroll_step(self, run: Run, audio: bytes, result: str) -> None:
        mics = self.house.mics
        mics.send(run.mic, "RUN_END")
        self.runs.pop(run.mic.id, None)
        job = self.enroll.get(run.mic.id)
        if job is None:
            return
        embedding = None
        if result != "timeout":
            embedding = await asyncio.to_thread(self.house.speaker.embed, audio)
        if embedding is None:
            job.retries += 1
            if job.retries > 2:
                self.enroll.pop(run.mic.id, None)
                self._enroll_publish(job, "failed")
                return
            self._enroll_publish(job, "retry")
        else:
            job.embeddings.append(embedding)
            job.step += 1
            if job.step >= len(ENROLL_SENTENCES):
                self.house.people.set_voice(job.person_id, profile_from(job.embeddings))
                self.enroll.pop(run.mic.id, None)
                self._enroll_publish(job, "done")
                self.house.events.log("people", "Stimmprofil gespeichert")
                return
            self._enroll_publish(job, "listening")
        await asyncio.sleep(0.8)
        await mics.listen(run.mic)


def _remember(session: AreaSession, text: str, reply: str) -> None:
    """Keep instant answers in the conversation so a follow-up like "und wieder aus" has context."""
    try:
        from google.genai import types
    except ImportError:
        return
    session.history = (
        list(session.history)
        + [
            types.Content(role="user", parts=[types.Part.from_text(text=text)]),
            types.Content(role="model", parts=[types.Part.from_text(text=reply)]),
        ]
    )[-12:]
