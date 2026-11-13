"""One listening decision per Bereich.

Several boards in the same Bereich may wake on the same sentence. Jarvis
collects for ARBITRATION_WINDOW seconds, keeps the board with the clearest
level and rejects the rest. Different Bereiche never block each other.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

import numpy as np

ARBITRATION_WINDOW = 0.4
FOLLOWUP_SECONDS = 8.0


class Phase(str, Enum):
    IDLE = "idle"
    COLLECTING = "collecting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FOLLOWUP = "followup"


def rms(chunk: bytes) -> float:
    if len(chunk) < 2:
        return 0.0
    samples = np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype="<i2").astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples)))


@dataclass
class Candidate:
    mic_id: str
    started: float
    energy: float = 0.0
    frames: int = 0
    chunks: list[bytes] = field(default_factory=list)

    def feed(self, chunk: bytes) -> None:
        self.chunks.append(chunk)
        self.energy += rms(chunk)
        self.frames += 1

    @property
    def level(self) -> float:
        return self.energy / self.frames if self.frames else 0.0


class Offer(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    FOLLOWUP = "followup"


@dataclass
class AreaSession:
    area_id: str
    clock: Callable[[], float] = time.monotonic
    phase: Phase = Phase.IDLE
    candidates: dict[str, Candidate] = field(default_factory=dict)
    winner: str | None = None
    window_started: float = 0.0
    followup_until: float = 0.0
    followup_mic: str | None = None
    history: list = field(default_factory=list)
    pending: object | None = None

    def offer(self, mic_id: str, wake_word: bool) -> Offer:
        """A board in this Bereich wants to start a run."""
        now = self.clock()
        if self.phase == Phase.IDLE:
            if not wake_word:
                return Offer.REJECT
            self.phase = Phase.COLLECTING
            self.window_started = now
            self.candidates = {mic_id: Candidate(mic_id, now)}
            self.winner = None
            return Offer.ACCEPT
        if self.phase == Phase.COLLECTING and wake_word:
            if mic_id not in self.candidates and now - self.window_started <= ARBITRATION_WINDOW:
                self.candidates[mic_id] = Candidate(mic_id, now)
                return Offer.ACCEPT
            return Offer.REJECT
        if self.phase == Phase.FOLLOWUP and not wake_word and mic_id == self.followup_mic:
            if now <= self.followup_until:
                self.phase = Phase.LISTENING
                self.winner = mic_id
                self.candidates = {mic_id: Candidate(mic_id, now)}
                return Offer.FOLLOWUP
            return Offer.REJECT
        if self.phase == Phase.FOLLOWUP and wake_word:
            # A fresh wake word inside the window starts a normal run.
            self.phase = Phase.COLLECTING
            self.window_started = now
            self.candidates = {mic_id: Candidate(mic_id, now)}
            self.winner = None
            return Offer.ACCEPT
        return Offer.REJECT

    def feed(self, mic_id: str, chunk: bytes) -> None:
        candidate = self.candidates.get(mic_id)
        if candidate is not None:
            candidate.feed(chunk)

    def window_elapsed(self) -> bool:
        return self.phase == Phase.COLLECTING and self.clock() - self.window_started >= ARBITRATION_WINDOW

    def decide(self) -> tuple[str | None, list[str]]:
        """Pick the clearest board. Returns (winner, losers)."""
        if self.phase != Phase.COLLECTING or not self.candidates:
            return None, []
        ranked = sorted(
            self.candidates.values(),
            key=lambda c: (c.level, -c.started),
            reverse=True,
        )
        winner = ranked[0]
        self.winner = winner.mic_id
        self.phase = Phase.LISTENING
        losers = [c.mic_id for c in ranked[1:]]
        self.candidates = {winner.mic_id: winner}
        return winner.mic_id, losers

    def drop(self, mic_id: str) -> None:
        """A board stopped on its own before the decision."""
        self.candidates.pop(mic_id, None)
        if self.phase == Phase.COLLECTING and not self.candidates:
            self.reset()

    def buffered_audio(self) -> bytes:
        if self.winner and self.winner in self.candidates:
            return b"".join(self.candidates[self.winner].chunks)
        return b""

    def start_thinking(self) -> None:
        self.phase = Phase.THINKING

    def start_speaking(self) -> None:
        self.phase = Phase.SPEAKING

    def open_followup(self, mic_id: str | None, seconds: float = FOLLOWUP_SECONDS) -> None:
        if mic_id is None or seconds <= 0:
            self.reset()
            return
        self.phase = Phase.FOLLOWUP
        self.followup_mic = mic_id
        self.followup_until = self.clock() + seconds

    def followup_expired(self) -> bool:
        return self.phase == Phase.FOLLOWUP and self.clock() > self.followup_until

    def reset(self) -> None:
        self.phase = Phase.IDLE
        self.candidates = {}
        self.winner = None
        self.followup_mic = None
        self.followup_until = 0.0
        self.pending = None
        self.history = []

    @property
    def busy(self) -> bool:
        return self.phase not in (Phase.IDLE, Phase.FOLLOWUP)
