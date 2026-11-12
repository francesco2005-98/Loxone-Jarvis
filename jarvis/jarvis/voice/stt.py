"""Local speech recognition with faster-whisper. Audio never leaves the server."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from ..audio.wav import pcm16_to_float

log = logging.getLogger(__name__)

LANGS = ("de", "en")


@dataclass
class Transcript:
    text: str
    lang: str
    confidence: float
    no_speech: float

    @property
    def usable(self) -> bool:
        return bool(self.text) and self.no_speech < 0.6 and self.confidence > -1.2


class Recognizer:
    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    def _model(self, name: str):
        with self._lock:
            model = self._models.get(name)
            if model is None:
                from faster_whisper import WhisperModel

                path = self.models_dir / "whisper" / name
                source = str(path) if path.exists() else name
                model = WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=4, num_workers=2)
                self._models[name] = model
            return model

    def preload(self, name: str) -> None:
        self._model(name)

    def transcribe(self, pcm: bytes, *, model: str, language: str, prompt: str = "") -> Transcript:
        audio = pcm16_to_float(pcm)
        if audio.size < 16000 * 0.3:
            return Transcript("", language if language in LANGS else "de", -9.0, 1.0)
        whisper = self._model(model)
        lang = language if language in LANGS else None
        if lang is None:
            lang = self._detect(whisper, audio)
        segments, info = whisper.transcribe(
            audio,
            language=lang,
            beam_size=2,
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=True,
            initial_prompt=prompt[:400] or None,
        )
        parts = list(segments)
        text = " ".join(s.text.strip() for s in parts).strip()
        if not parts:
            return Transcript("", lang, -9.0, 1.0)
        avg = sum(s.avg_logprob for s in parts) / len(parts)
        no_speech = max(s.no_speech_prob for s in parts)
        return Transcript(text, lang, avg, no_speech)

    @staticmethod
    def _detect(whisper, audio) -> str:
        try:
            _, _, probs = whisper.detect_language(audio)
            scores = dict(probs)
            return max(LANGS, key=lambda code: scores.get(code, 0.0))
        except Exception:  # noqa: BLE001
            log.debug("language detection failed, using German", exc_info=True)
            return "de"
