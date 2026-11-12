"""WAV helpers: decode, resample, prepend silence, encode."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

import numpy as np


@dataclass
class Pcm:
    samples: np.ndarray  # int16 mono
    rate: int

    @property
    def duration(self) -> float:
        return self.samples.size / float(self.rate) if self.rate else 0.0


def decode(data: bytes) -> Pcm:
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError("nur 16-Bit-WAV wird unterstützt")
    samples = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype("<i2")
    return Pcm(samples.copy(), rate)


def encode(pcm: Pcm) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(pcm.rate)
        wav.writeframes(pcm.samples.astype("<i2").tobytes())
    return buffer.getvalue()


def resample(pcm: Pcm, rate: int) -> Pcm:
    if pcm.rate == rate or pcm.samples.size == 0:
        return Pcm(pcm.samples, rate) if pcm.samples.size == 0 else pcm
    duration = pcm.samples.size / pcm.rate
    target = int(round(duration * rate))
    src_t = np.arange(pcm.samples.size) / pcm.rate
    dst_t = np.arange(target) / rate
    out = np.interp(dst_t, src_t, pcm.samples.astype(np.float32))
    return Pcm(np.clip(out, -32768, 32767).astype("<i2"), rate)


def with_silence(pcm: Pcm, lead_ms: int, tail_ms: int = 250) -> Pcm:
    lead = np.zeros(int(pcm.rate * lead_ms / 1000), dtype="<i2")
    tail = np.zeros(int(pcm.rate * tail_ms / 1000), dtype="<i2")
    return Pcm(np.concatenate([lead, pcm.samples, tail]), pcm.rate)


def concat(parts: list[Pcm], gap_ms: int = 150) -> Pcm:
    if not parts:
        return Pcm(np.zeros(0, dtype="<i2"), 22050)
    rate = parts[0].rate
    gap = np.zeros(int(rate * gap_ms / 1000), dtype="<i2")
    chunks: list[np.ndarray] = []
    for index, part in enumerate(parts):
        if index:
            chunks.append(gap)
        chunks.append(resample(part, rate).samples)
    return Pcm(np.concatenate(chunks), rate)


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
