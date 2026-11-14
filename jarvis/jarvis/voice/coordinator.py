"""From wake word to spoken answer, once per Bereich, several Bereiche in parallel."""

from __future__ import annotations

import asyncio
import math
import re
import logging
import time
from datetime import datetime
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..areas import Area
from ..brain import confirm, fallback, quick, when
from ..brain.gemini import BrainUnavailable, wants_detail
from ..brain.tools import Pending, Turn
from ..people import ENROLL_SENTENCES
from ..settings import ANREDE_LABELS
from .arbitration import ARBITRATION_WINDOW, AreaSession, Offer, Phase, rms
from .speaker import identify, profile_from
from .correct import correct
from .vad import Endpointer, VadConfig, high_pass

if TYPE_CHECKING:
    from ..house import House
    from .mics import Mic

log = logging.getLogger(__name__)

LEVEL_INTERVAL = 0.06
SUGGEST_WAIT = 1.2
SUGGEST_COOLDOWN = {"reserved": 600.0, "active": 120.0}
OFFER_YES = {"gerne", "klar", "sicher", "unbedingt", "bitte", "jo", "jep", "sure", "please", "yeah"}
OFFER_FOLLOWUP_MIN = 6.0
# Recognition starts on a short pause and is kept if the pause turns into the end of the sentence.
SPECULATE_MS = 250


@dataclass
class Run:
    mic: "Mic"
    area_id: str | None
    kind: str  # command | followup | enroll
    endpointer: Endpointer
    audio: bytearray = field(default_factory=bytearray)
    state: str = "collecting"
    started: float = field(default_factory=time.monotonic)
    spec: asyncio.Future | None = None
    spec_anchor: int = -1


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
        self._declines: dict[str, list[tuple[float, str, str, str]]] = {}
        self._suggested_at: dict[str, float] = {}
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
            run = Run(mic, mic.area_id, "followup", Endpointer(self._vad(noise, True)), state="listening")
            self.runs[mic.id] = run
            mics.send(mic, "RUN_START")
            mics.send(mic, "STT_START")
        else:
            self.runs[mic.id] = Run(mic, mic.area_id, "command", Endpointer(self._vad(noise)))
            mics.send(mic, "RUN_START")
            if len(mics.in_area(mic.area_id)) <= 1:
                # Nothing to compare. Waiting out the arbitration window only delays the sentence.
                self._decide(mic.area_id)
            elif len(session.candidates) == 1:
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
        elif run.kind != "enroll":
            self._speculate(run)

    def _vad(self, noise: int, followup: bool = False) -> VadConfig:
        end = int(self.house.settings.get("voice.end_silence_ms"))
        return VadConfig.for_noise_filter(noise, followup, end_silence_ms=end)

    def _speculate(self, run: Run) -> None:
        ep = run.endpointer
        if not ep.speech_started or not self.house.settings.get("voice.speculative"):
            return
        if run.spec is not None and run.spec_anchor != ep.last_speech_ms:
            run.spec.cancel()
            run.spec = None
        if run.spec is None and ep.silence_ms >= SPECULATE_MS:
            anchor = run.spec_anchor = ep.last_speech_ms
            run.spec = asyncio.ensure_future(self._transcribe(bytes(run.audio)))
            run.spec.add_done_callback(lambda done, run=run, anchor=anchor: self._maybe_finish(run, anchor, done))

    def _maybe_finish(self, run: Run, anchor: int, done: asyncio.Future) -> None:
        """A finished instant command needs no longer pause: answer right away."""
        if done.cancelled() or done.exception() is not None:
            return
        if run.state != "listening" or run.spec is not done or run.endpointer.last_speech_ms != anchor:
            return
        _, transcript = done.result()
        session = self.sessions.get(run.area_id) if run.area_id else None
        if transcript.usable and self.complete(transcript.text, session):
            self._finish(run, "end")

    def complete(self, text: str, session: AreaSession | None = None) -> bool:
        """True when the words so far already form an instant command or a yes/no answer."""
        text = correct(text, self.house)
        if session is not None and session.pending is not None and confirm.classify(text) != confirm.Answer.UNCLEAR:
            return True
        return quick.parse(text) is not None

    async def _transcribe(self, audio: bytes):
        house = self.house
        pcm = high_pass(audio, int(house.settings.get("mic.noise_filter")))
        transcript = await asyncio.to_thread(
            house.stt.transcribe,
            pcm,
            model=house.settings.get("stt.model"),
            language=house.settings.get("stt.language"),
            prompt=self._stt_prompt(),
        )
        return pcm, transcript

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
        """Names Whisper may lean on: areas and rooms first, then devices."""
        ordered = ["Jarvis"]
        for area in self.house.areas.list():
            ordered.append(area.name)
            ordered.extend(area.rooms)
        for entry in self.house.catalog.entries(only_allowed=True):
            ordered.append(entry.name)
            ordered.extend(entry.aliases)
        seen: list[str] = []
        for name in ordered:
            name = (name or "").strip()
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
            found: dict[str, str | None] = {}

            def _keep(task: asyncio.Task, found: dict = found) -> None:
                try:
                    found["id"] = task.result()
                except Exception:  # noqa: BLE001
                    found["id"] = None

            who = asyncio.ensure_future(
                asyncio.to_thread(self._identify, high_pass(audio, int(house.settings.get("mic.noise_filter"))))
            )
            who.add_done_callback(_keep)
            if run.spec is not None and run.spec_anchor == run.endpointer.last_speech_ms:
                pcm, transcript = await run.spec
            else:
                pcm, transcript = await self._transcribe(audio)
            stt_ms = int((time.monotonic() - started) * 1000)
            mics.send(run.mic, "STT_END", {"text": transcript.text[:200] or " "})
            mics.send(run.mic, "INTENT_START")
            mics.send(run.mic, "INTENT_END", {"conversation_id": "", "continue_conversation": "0"})
            mics.send(run.mic, "RUN_END")
            self.runs.pop(run.mic.id, None)
            if area is None or not transcript.usable:
                session.reset()
                return
            # The reply does not wait for speaker recognition. A name that is already
            # known is used; otherwise the answer stays neutral.
            person_id = found.get("id")
            brain_started = time.monotonic()
            followup = run.kind == "followup"
            reply, done = await self.handle_text(session, area, transcript.text, transcript.lang, person_id, followup=followup)
            if not reply:
                house.events.log("voice", f"Nicht an Jarvis: „{transcript.text}“", "Gespräch beendet")
                session.reset()
                self._phase(area.id)
                return
            brain_ms = int((time.monotonic() - brain_started) * 1000)
            latency = int((time.monotonic() - started) * 1000)
            self.latencies.append(latency)
            self.last = {
                "ts": time.time(),
                "area": area.name,
                "text": transcript.text,
                "reply": reply,
                "actions": done,
                "ms": latency,
                "timing": {"stt": stt_ms, "brain": brain_ms},
            }
            house.events.publish({"type": "utterance", "utterance": self.last})
            house.events.log("voice", f"„{transcript.text}“", reply[:200])
            if reply:
                session.start_speaking()
                await house.speech.say(reply, transcript.lang, [area])
                self.last["timing"]["voice"] = house.speech.last_voice_ms
                offer = await self.take_suggestion(session)
                if offer is not None:
                    await house.speech.say(offer.question, transcript.lang, [area])
            if session.phase in (Phase.SPEAKING, Phase.THINKING) and not session.keep_talking(
                float(house.settings.get("voice.conversation_s"))
            ):
                # Long enough since the last "Jarvis": the next sentence needs the wake word again.
                session.reset()
                self._phase(area.id)
            elif session.phase in (Phase.SPEAKING, Phase.THINKING):
                seconds = float(house.settings.get("voice.followup_s"))
                if session.pending is not None:
                    seconds = max(seconds, OFFER_FOLLOWUP_MIN)
                session.open_followup(run.mic.id, seconds)
                self._phase(area.id)
                if session.phase == Phase.FOLLOWUP:
                    until = session.followup_until
                    asyncio.get_running_loop().call_later(seconds + 0.5, self._expire, area.id, until)
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
        self,
        session: AreaSession,
        area: Area,
        text: str,
        lang: str,
        person_id: str | None,
        origin: str = "",
        followup: bool = False,
    ) -> tuple[str, list[str]]:
        """The spoken reply and the actions taken. An empty reply means: not meant for Jarvis, stay quiet."""
        text = correct(text, self.house)
        if followup and session.pending is None and _fragment(text):
            return "", []
        if session.pending is not None:
            pending: Pending = session.pending  # type: ignore[assignment]
            session.pending = None
            answer = confirm.classify(text)
            if pending.kind == "offer" and answer == confirm.Answer.UNCLEAR:
                answer = _offer_answer(text)
            if pending.kind == "offer":
                if answer != confirm.Answer.UNCLEAR:
                    reply, done = await self._answer_offer(pending, area, lang, person_id, origin, answer)
                    _remember(session, text, reply)
                    return reply, done
            elif answer == confirm.Answer.YES:
                return await self._confirmed(pending, session, area, lang, person_id, origin)
            elif answer == confirm.Answer.NO:
                return ("Abgebrochen." if lang == "de" else "Cancelled."), []
            else:
                session.pending = pending
        session.suggestion = None
        turn = Turn(
            area=area, lang=lang, text=text, person_id=person_id, origin=origin, followup=followup, detailed=wants_detail(text)
        )
        instant = await self._quick(turn)
        if instant is not None:
            _remember(session, text, instant)
            if turn.done and self._may_suggest(area):
                session.suggestion = asyncio.ensure_future(self.house.brain.suggest(turn))
            return instant, turn.done
        try:
            reply, history = await self.house.brain.answer(turn, session.history)
            session.history = history
        except BrainUnavailable as exc:
            self.house.events.log("ai", "Gemini nicht erreichbar", str(exc)[:200])
            reply = await self._fallback(turn)
            if followup and not turn.done:
                return "", []
        if turn.pending is not None:
            session.pending = turn.pending
        elif turn.offer is not None:
            session.pending = turn.offer
        return reply, turn.done

    # --------------------------------------------------------- suggestions
    async def _answer_offer(
        self, offer: Pending, area: Area, lang: str, person_id: str | None, origin: str, answer: confirm.Answer
    ) -> tuple[str, list[str]]:
        """A plain yes or no to Jarvis' own suggestion, decided locally without Gemini."""
        turn = Turn(area=area, lang=lang, text="ja", person_id=person_id, origin=origin)
        anrede = self._anrede(turn)
        if answer == confirm.Answer.NO:
            for entry, action, _, _ in offer.actions:
                self._declines.setdefault(area.id, []).append((time.time(), entry.uuid, action, f"{entry.name}: {action}"))
            return self.house.phrases.pick("offer_declined", lang, anrede=anrede), []
        ok = True
        for entry, action, value, value2 in offer.actions:
            try:
                result = await self.house.brain.runner.execute(entry, action, value, value2, turn)
                ok = ok and bool(result.get("ok"))
            except Exception:  # noqa: BLE001
                log.exception("offer failed")
                ok = False
        return self.house.phrases.pick("offer_done" if ok else "failed", lang, anrede=anrede), turn.done

    def _may_suggest(self, area: Area) -> bool:
        """Background suggestions for instant commands: only with Gemini, other devices around and a pause since the last."""
        house = self.house
        level = house.settings.get("brain.suggestions")
        if level == "off" or not house.secrets.has("gemini.api_key"):
            return False
        if time.monotonic() - self._suggested_at.get(area.id, -1e9) < SUGGEST_COOLDOWN.get(level, 600.0):
            return False
        if level == "reserved":
            around = [e for e in house.catalog.entries(only_allowed=True) if e.room in area.rooms and not e.critical]
            if len({e.group for e in around}) < 2 and len(around) < 2:
                return False
        self._suggested_at[area.id] = time.monotonic()
        return True

    async def take_suggestion(self, session: AreaSession, wait: float = SUGGEST_WAIT) -> Pending | None:
        """The background suggestion, if it is ready by now (or within a short wait). Becomes the open question."""
        task = session.suggestion
        session.suggestion = None
        if task is None:
            return None
        try:
            offer = await asyncio.wait_for(asyncio.shield(task), timeout=wait)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            task.cancel()
            return None
        if offer is not None and session.pending is None:
            session.pending = offer
            _remember_model(session, offer.question)
            return offer
        return None

    def _recent_declines(self, area_id: str) -> list[tuple[float, str, str, str]]:
        hours = float(self.house.settings.get("brain.decline_hours"))
        cutoff = time.time() - hours * 3600
        items = [d for d in self._declines.get(area_id, []) if d[0] >= cutoff]
        self._declines[area_id] = items
        return items

    def declined(self, area_id: str, uuid: str, action: str) -> bool:
        return any(d[1] == uuid and d[2] == action for d in self._recent_declines(area_id))

    def declined_labels(self, area_id: str) -> list[str]:
        return [d[3] for d in self._recent_declines(area_id)]

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
        """Plain commands (light, dimming, blinds, time, date) are handled at once, without Gemini."""
        if not self.house.settings.get("brain.quick"):
            return None
        command = quick.parse(turn.text)
        if command is None:
            return None
        anrede = self._anrede(turn)
        book = self.house.phrases
        if command.kind in ("time", "date"):
            return quick.clock_reply(command, datetime.now(self.house.settings.tz), turn.lang, anrede, book)
        if command.kind == "temp":
            return quick.temperature_reply(self.house, turn.area, command, turn.lang, anrede, book)
        if command.kind in ("timer", "reminder", "timer_left", "timer_cancel"):
            return self._quick_timer(turn, command, anrede)
        entries, room = quick.targets(self.house, turn.area, command)
        if not entries:
            return None
        value = command.scene or (str(command.value) if command.value is not None else None)
        ok = True
        for entry in entries:
            try:
                result = await self.house.brain.runner.execute(entry, command.action, value, None, turn)
                ok = ok and bool(result.get("ok"))
            except Exception:  # noqa: BLE001
                log.exception("quick command failed")
                ok = False
        return quick.reply(command, room, turn.lang, anrede, ok, book)

    def _quick_timer(self, turn: Turn, command: quick.QuickCommand, anrede: str | None) -> str | None:
        house = self.house
        book = house.phrases
        lang = turn.lang
        timers = house.timers
        area_id = turn.area.id
        if command.kind == "timer_cancel":
            count = timers.cancel(bereich_id=area_id)
            return book.pick("timer_cancel" if count else "timer_none", lang, anrede=anrede)
        if command.kind == "timer_left":
            now = time.time()
            running = timers.list(area_id)[:3]
            if not running:
                return book.pick("timer_none", lang, anrede=anrede)
            parts = []
            for timer in running:
                seconds = max(1, int(timer["due_at"] - now))
                if seconds > 120:
                    seconds = int(math.ceil(seconds / 60) * 60)
                left = when.human_duration(seconds, lang)
                label = timer.get("label") or ""
                generic = label.lower() in ("timer", "erinnerung", "reminder", "")
                parts.append(left if generic else (f"{left} for {label}" if lang == "en" else f"{left} für {label}"))
            return book.pick("timer_left", lang, anrede=anrede, wert=(" and " if lang == "en" else " und ").join(parts))
        now_local = datetime.now(house.settings.tz)
        if command.clock:
            moment = when.parse_clock(command.clock, now_local)
            if moment is None:
                return None
            due = moment.timestamp()
            wert = f"at {when.human_clock(moment, lang)}" if lang == "en" else f"um {when.human_clock(moment, lang)}"
        else:
            due = time.time() + int(command.value or 0)
            wert = when.human_duration(int(command.value or 0), lang)
            if command.kind == "reminder":
                wert = f"in {wert}"
        label = quick.timer_label(turn.text, command.reason)
        if command.kind == "timer":
            text = quick.original_words(turn.text, command.reason)
            label = label or "Timer"
        else:
            text = quick.reminder_text(turn.text, command.reason)
            label = label or ("Reminder" if lang == "en" else "Erinnerung")
        try:
            timers.add(kind=command.kind, due_at=due, label=label, text=text, bereich_id=area_id, lang=lang, origin=turn.origin)
        except ValueError:
            return None
        turn.done.append(f"{command.kind} {label}")
        return book.pick("timer_set" if command.kind == "timer" else "reminder_set", lang, anrede=anrede, wert=wert)

    def _anrede(self, turn: Turn) -> str | None:
        anrede = self.house.people.address(turn.person_id)[0] if turn.person_id else None
        return anrede or ANREDE_LABELS.get(self.house.settings.get("house.default_anrede"))

    async def _fallback(self, turn: Turn) -> str:
        command = fallback.parse(turn.text)
        if command is None:
            return self.house.phrases.pick("offline", turn.lang, anrede=self._anrede(turn))
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
        if not ok:
            return self.house.phrases.pick("failed", turn.lang, anrede=self._anrede(turn))
        intent = {"on": "light_on", "off": "light_off", "up": "shade_up", "down": "shade_down"}[command.action]
        return self.house.phrases.pick(intent, turn.lang, anrede=self._anrede(turn))

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


def _remember_model(session: AreaSession, text: str) -> None:
    try:
        from google.genai import types
    except ImportError:
        return
    session.history = (list(session.history) + [types.Content(role="model", parts=[types.Part.from_text(text=text)])])[-12:]


def _fragment(text: str) -> bool:
    """One stray word heard after an answer ("Hm", "Na", "Okay"): nothing to act on."""
    return len(re.findall(r"[a-zäöüß]+", text.lower())) < 2 and quick.parse(text) is None


def _offer_answer(text: str) -> confirm.Answer:
    """Friendly yes or no words that are fine for a suggestion, though not for the alarm."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    if words and words[0] in OFFER_YES and len(words) <= 4:
        return confirm.Answer.YES
    if words and words[0] in ("nö", "nee", "lass", "lieber") and len(words) <= 4:
        return confirm.Answer.NO
    return confirm.Answer.UNCLEAR
