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
# Once someone talks, softer syllables well under the start threshold still count as speech.
# A tight hold treated the end of a quiet word as silence and cut the sentence off.
HOLD_DB = 8.0


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
    end_silence_ms: int = 700
    no_speech_timeout_ms: int = 5000
    # Counted from the first word, so a late start after the wake word keeps the full time.
    max_utterance_ms: int = 15000

    @classmethod
    def for_noise_filter(cls, level: int, followup: bool = False, end_silence_ms: int = 700) -> "VadConfig":
        level = max(0, min(4, level))
        return cls(
            margin_db=7.0 + 1.5 * level,
            floor_dbfs=-55.0 + 1.5 * level,
            end_silence_ms=end_silence_ms,
            no_speech_timeout_ms=8000 if followup else 5000,
        )


class Endpointer:
    """Feed raw PCM; returns 'continue', 'end' (speech done) or 'timeout' (nothing said)."""

    def __init__(self, config: VadConfig | None = None):
        self.config = config or VadConfig()
        self._buffer = b""
        self._noise: float | None = None
        self._speech_run = 0
        self._loud_run = 0
        self._silence_ms = 0
        self._elapsed_ms = 0
        self._speech_from = 0
        self.speech_started = False

    @property
    def silence_ms(self) -> int:
        return self._silence_ms

    @property
    def last_speech_ms(self) -> int:
        """Stream position of the last speech frame; unchanged while the speaker stays quiet."""
        return self._elapsed_ms - self._silence_ms

    @property
    def speech_ms(self) -> int:
        """How long the voice itself lasted, without the silence after it."""
        if not self.speech_started:
            return 0
        return max(0, self.last_speech_ms - self._speech_from)

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
        threshold = self._threshold()
        if not self.speech_started:
            if level > threshold:
                self._speech_run += 1
                if self._speech_run >= cfg.start_frames:
                    self.speech_started = True
                    self._silence_ms = 0
                    self._speech_from = self._elapsed_ms - cfg.start_frames * FRAME_MS
            else:
                self._speech_run = 0
                self._noise = level if self._noise is None else 0.9 * self._noise + 0.1 * level
            if not self.speech_started and self._elapsed_ms >= cfg.no_speech_timeout_ms:
                return "timeout"
        else:
            is_speech = level > threshold - HOLD_DB
            # One loud frame in a pause is room noise. It must not restart the wait,
            # or a crackle keeps the board streaming for the whole utterance limit.
            if is_speech:
                self._loud_run += 1
                if self._loud_run >= 2:
                    self._silence_ms = 0
            else:
                self._loud_run = 0
                self._silence_ms += FRAME_MS
                if self._silence_ms >= cfg.end_silence_ms:
                    return "end"
            if self._elapsed_ms - self._speech_from >= cfg.max_utterance_ms:
                return "end"
        return "continue"


def _db(samples: np.ndarray) -> float:
    power = float(np.mean(samples * samples)) if samples.size else 0.0
    if power <= 1e-9:
        return -120.0
    return 10.0 * np.log10(power / (32768.0**2))


def high_pass(pcm: bytes, level: int) -> bytes:
    """Remove rumble. Duck only frames far below this recording's own voice.

    An absolute gate ate quiet consonants, so the same sentence was clear one
    time and missing the next. A recording that is quiet all the way through
    is left whole and turned up later.
    """
    if level <= 0 or len(pcm) < 4:
        return pcm
    x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    alpha = 0.97
    y = np.empty_like(x)
    y[0] = x[0]
    y[1:] = x[1:] - alpha * x[:-1]
    y = 0.6 * y + 0.4 * x
    frame = 480
    levels = [_db(y[start : start + frame]) for start in range(0, y.size, frame)]
    speech = float(np.percentile(levels, 90)) if levels else -120.0
    if speech < -46.0:
        return np.clip(y, -32768, 32767).astype("<i2").tobytes()
    gate_db = min(speech - (12.0 + level), -40.0)
    for index, start in enumerate(range(0, y.size, frame)):
        if levels[index] < gate_db:
            y[start : start + frame] *= 0.45
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()
