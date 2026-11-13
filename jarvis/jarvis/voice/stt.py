"""Local speech recognition with faster-whisper. Audio never leaves the server."""

from __future__ import annotations

import logging
import os
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
                threads = max(2, min(8, os.cpu_count() or 4))
                model = WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1)
                self._models[name] = model
            return model

    def preload(self, name: str) -> None:
        self._model(name)

    def transcribe(self, pcm: bytes, *, model: str, language: str, prompt: str = "") -> Transcript:
        audio = pcm16_to_float(pcm)
        if audio.size < 16000 * 0.3:
            return Transcript("", language if language in LANGS else "de", -9.0, 1.0)
        whisper = self._model(model)
        fixed = language if language in LANGS else None
        parts, info = self._run(whisper, audio, fixed, prompt)
        lang = fixed or info.language
        if lang not in LANGS:
            # Detection ran inside the same pass; only a third language costs a second one.
            lang = _best_of(info)
            parts, info = self._run(whisper, audio, lang, prompt)
        text = " ".join(s.text.strip() for s in parts).strip()
        if not parts:
            return Transcript("", lang, -9.0, 1.0)
        avg = sum(s.avg_logprob for s in parts) / len(parts)
        no_speech = max(s.no_speech_prob for s in parts)
        return Transcript(text, lang, avg, no_speech)

    @staticmethod
    def _run(whisper, audio, lang: str | None, prompt: str):
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
        return list(segments), info


def _best_of(info) -> str:
    scores = dict(getattr(info, "all_language_probs", None) or [])
    return max(LANGS, key=lambda code: scores.get(code, 0.0)) if scores else "de"
