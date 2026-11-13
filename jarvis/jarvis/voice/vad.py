"""End-of-utterance detection on the 16 kHz stream from a board.

The board already heard the wake word. This only decides when the sentence
after it is over, so the board can stop streaming.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2


def frame_dbfs(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype="<i2").astype(np.float32)
    if samples.size == 0:
        return -120.0
    power = float(np.mean(samples * samples))
    if power <= 1e-9:
        return -120.0
    return 10.0 * np.log10(power / (32768.0**2))


@dataclass
class VadConfig:
    margin_db: float = 9.0
    floor_dbfs: float = -52.0
    start_frames: int = 3
    end_silence_ms: int = 550
    no_speech_timeout_ms: int = 5000
    max_utterance_ms: int = 12000

    @classmethod
    def for_noise_filter(cls, level: int, followup: bool = False) -> "VadConfig":
        level = max(0, min(4, level))
        return cls(
            margin_db=7.0 + 1.5 * level,
            floor_dbfs=-55.0 + 1.5 * level,
            no_speech_timeout_ms=8000 if followup else 5000,
            max_utterance_ms=8000 if followup else 12000,
        )


class Endpointer:
    """Feed raw PCM; returns 'continue', 'end' (speech done) or 'timeout' (nothing said)."""

    def __init__(self, config: VadConfig | None = None):
        self.config = config or VadConfig()
        self._buffer = b""
        self._noise: float | None = None
        self._speech_run = 0
        self._silence_ms = 0
        self._elapsed_ms = 0
        self.speech_started = False

    @property
    def silence_ms(self) -> int:
        return self._silence_ms

    @property
    def last_speech_ms(self) -> int:
        """Stream position of the last speech frame; unchanged while the speaker stays quiet."""
        return self._elapsed_ms - self._silence_ms

    def _threshold(self) -> float:
        noise = self._noise if self._noise is not None else -60.0
        return max(noise + self.config.margin_db, self.config.floor_dbfs)

    def feed(self, chunk: bytes) -> str:
        self._buffer += chunk
        result = "continue"
        while len(self._buffer) >= FRAME_BYTES:
            frame, self._buffer = self._buffer[:FRAME_BYTES], self._buffer[FRAME_BYTES:]
            result = self._frame(frame)
            if result != "continue":
                return result
        return result

    def _frame(self, frame: bytes) -> str:
        cfg = self.config
        self._elapsed_ms += FRAME_MS
        level = frame_dbfs(frame)
        is_speech = level > self._threshold()
        if not self.speech_started:
            if is_speech:
                self._speech_run += 1
                if self._speech_run >= cfg.start_frames:
                    self.speech_started = True
                    self._silence_ms = 0
            else:
                self._speech_run = 0
                self._noise = level if self._noise is None else 0.9 * self._noise + 0.1 * level
            if not self.speech_started and self._elapsed_ms >= cfg.no_speech_timeout_ms:
                return "timeout"
        else:
            if is_speech:
                self._silence_ms = 0
            else:
                self._silence_ms += FRAME_MS
                if self._silence_ms >= cfg.end_silence_ms:
                    return "end"
        if self._elapsed_ms >= cfg.max_utterance_ms:
            return "end" if self.speech_started else "timeout"
        return "continue"


def high_pass(pcm: bytes, level: int) -> bytes:
    """Remove rumble and apply a soft noise gate before recognition."""
    if level <= 0 or len(pcm) < 4:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    alpha = 0.97
    y = np.empty_like(x)
    y[0] = x[0]
    y[1:] = x[1:] - alpha * x[:-1]
    y = 0.6 * y + 0.4 * x
    frame = 480
    gate_db = -62.0 + 3.0 * level
    for start in range(0, y.size, frame):
        seg = y[start : start + frame]
        power = float(np.mean(seg * seg)) if seg.size else 0.0
        db = 10.0 * np.log10(power / (32768.0**2)) if power > 1e-9 else -120.0
        if db < gate_db:
            y[start : start + frame] = seg * 0.2
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()
