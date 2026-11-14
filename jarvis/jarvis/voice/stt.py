"""Local speech recognition with faster-whisper. Audio never leaves the server.

The default model is a German fine-tune of Whisper large-v3-turbo. Short
commands are already cut out by the house VAD, so Whisper's own voice
activity filter stays off: it would clip the first syllable. Quiet recordings
are lifted to a steady level. Automatic language detection prefers German
unless English is clearly more likely, because a short German sentence is
often labelled English.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..audio.wav import pcm16_to_float

log = logging.getLogger(__name__)

LANGS = ("de", "en")
# English has to lead German by this much before a short utterance is transcribed in English.
ENGLISH_MARGIN = 0.15
_PROMPTS = {
    "de": "Jarvis, mach das Licht an.",
    "en": "Jarvis, turn the light on.",
}
# Local directory name -> Hugging Face id, used when the image has not baked the model in.
_SOURCES = {
    "german": "TheTobyB/whisper-large-v3-turbo-german-ct2",
}


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
                source = str(path) if (path / "model.bin").exists() else _SOURCES.get(name, name)
                threads = max(2, min(8, os.cpu_count() or 4))
                model = WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1)
                self._models[name] = model
            return model

    def preload(self, name: str) -> None:
        self._model(name)

    def transcribe(
        self,
        pcm: bytes,
        *,
        model: str,
        language: str,
        prompt: str = "",
        fast: bool = False,
    ) -> Transcript:
        audio = prepare_audio(pcm16_to_float(pcm))
        if audio.size < 16000 * 0.3:
            return Transcript("", language if language in LANGS else "de", -9.0, 1.0)
        whisper = self._model(model)
        fixed = language if language in LANGS else None
        parts, info = self._run(whisper, audio, fixed, prompt, fast)
        detected = getattr(info, "language", None)
        scores = dict(getattr(info, "all_language_probs", None) or [])
        chosen = pick_language(detected, scores, fixed)
        if chosen != (fixed or detected):
            parts, info = self._run(whisper, audio, chosen, prompt, fast)
        lang = chosen if chosen in LANGS else "de"
        text = " ".join(s.text.strip() for s in parts).strip()
        if not parts:
            return Transcript("", lang, -9.0, 1.0)
        avg = sum(s.avg_logprob for s in parts) / len(parts)
        no_speech = max(s.no_speech_prob for s in parts)
        return Transcript(text, lang, avg, no_speech)

    @staticmethod
    def _run(whisper, audio, lang: str | None, hotwords: str, fast: bool):
        # The utterance is already one sentence. A second VAD cuts the first sound off.
        segments, info = whisper.transcribe(
            audio,
            language=lang,
            beam_size=1 if fast else 5,
            temperature=0.0 if fast else (0.0, 0.2, 0.4),
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=False,
            initial_prompt=None if fast else _PROMPTS["en" if lang == "en" else "de"],
            hotwords=(hotwords or "").strip()[:240] or None,
        )
        return list(segments), info


def prepare_audio(audio: np.ndarray) -> np.ndarray:
    """Lift a quiet recording without turning the noise floor into speech."""
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < 0.02 or peak >= 0.5:
        return audio
    return np.clip(audio * (0.5 / peak), -1.0, 1.0).astype(np.float32)


def pick_language(detected: str | None, scores: dict[str, float] | None, fixed: str | None) -> str:
    """Language for this utterance. A fixed choice wins; otherwise German, unless English is clear."""
    if fixed in LANGS:
        return fixed
    scores = scores or {}
    if scores:
        if float(scores.get("en", 0.0)) > float(scores.get("de", 0.0)) + ENGLISH_MARGIN:
            return "en"
        return "de"
    if detected in LANGS:
        return detected
    return "de"
