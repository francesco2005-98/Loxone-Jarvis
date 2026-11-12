"""Local speaker recognition. Embeddings only; audio is never stored."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..audio.wav import pcm16_to_float

log = logging.getLogger(__name__)

# Conservative on purpose: a wrong "Sir" is worse than a neutral answer.
MATCH_THRESHOLD = 0.62
MATCH_MARGIN = 0.08
MIN_SECONDS = 1.0


@dataclass
class Match:
    person_id: str | None
    score: float


class SpeakerRecognizer:
    def __init__(self, model_path: Path):
        self.model_path = model_path
        self._extractor = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.model_path.exists()

    def _load(self):
        if self._extractor is None:
            import sherpa_onnx

            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(self.model_path), num_threads=2, provider="cpu"
            )
            if not config.validate():
                raise RuntimeError("Sprechermodell ungültig")
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        return self._extractor

    def embed(self, pcm: bytes, rate: int = 16000) -> np.ndarray | None:
        if len(pcm) < rate * 2 * MIN_SECONDS:
            return None
        with self._lock:
            extractor = self._load()
            stream = extractor.create_stream()
            stream.accept_waveform(rate, pcm16_to_float(pcm))
            stream.input_finished()
            if not extractor.is_ready(stream):
                return None
            vector = np.asarray(extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else None


def profile_from(embeddings: list[np.ndarray]) -> np.ndarray:
    stacked = np.stack(embeddings).astype(np.float32)
    mean = stacked.mean(axis=0)
    return mean / float(np.linalg.norm(mean))


def to_blob(vector: np.ndarray) -> bytes:
    return vector.astype("<f4").tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="<f4")


def identify(embedding: np.ndarray, profiles: dict[str, np.ndarray]) -> Match:
    if not profiles:
        return Match(None, 0.0)
    scores = sorted(
        ((float(np.dot(embedding, vec)), pid) for pid, vec in profiles.items() if vec.shape == embedding.shape),
        reverse=True,
    )
    if not scores:
        return Match(None, 0.0)
    best, person = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    if best >= MATCH_THRESHOLD and best - second >= MATCH_MARGIN:
        return Match(person, best)
    return Match(None, best)
