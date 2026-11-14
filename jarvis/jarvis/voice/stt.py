"""Local speech recognition. Audio never leaves the server.

The default engine is Kroko, a German streaming Zipformer run by sherpa-onnx:
about a tenth of a second for a command on four cores, and more accurate than
Whisper small on room recordings. Whisper small and base stay selectable and
work exactly as before.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..audio.wav import pcm16_to_float

log = logging.getLogger(__name__)

LANGS = ("de", "en")
KROKO = "kroko"
# The streaming model only emits the last words once it has heard silence after them.
KROKO_TAIL_S = 1.5
_KROKO_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")

_ENGLISH = {
    "the", "turn", "switch", "lights", "light", "please", "what", "is", "it", "time", "on", "off", "in",
    "living", "room", "kitchen", "bedroom", "set", "a", "timer", "for", "minutes", "remind", "me", "how",
    "warm", "cold", "play", "some", "music", "stop", "thank", "you", "yes", "no", "open", "close", "blinds",
    "up", "down", "to", "percent", "and", "of", "my", "today", "date", "day", "hello", "good", "morning",
}
_GERMAN = {
    "der", "die", "das", "und", "ist", "im", "in", "an", "aus", "mach", "mache", "schalte", "schalt", "licht",
    "bitte", "wie", "spät", "es", "ein", "eine", "einen", "mir", "mich", "auf", "zu", "hoch", "runter", "fahr",
    "fahre", "dimm", "stell", "stelle", "wohnzimmer", "küche", "schlafzimmer", "esszimmer", "ja", "nein",
    "danke", "warm", "kalt", "draußen", "uhr", "timer", "erinnere", "spiel", "musik", "rollos", "rollo",
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
        self._decode = threading.Lock()
        self._warned = False

    @staticmethod
    def _threads() -> int:
        return max(2, min(8, os.cpu_count() or 4))

    def _model(self, name: str):
        with self._lock:
            model = self._models.get(name)
            if model is None:
                model = self._load_kroko() if name == KROKO else self._load_whisper(name)
                self._models[name] = model
            return model

    def _load_whisper(self, name: str):
        from faster_whisper import WhisperModel

        path = self.models_dir / "whisper" / name
        source = str(path) if path.exists() else name
        return WhisperModel(source, device="cpu", compute_type="int8", cpu_threads=self._threads(), num_workers=1)

    def _load_kroko(self):
        import sherpa_onnx

        path = self.models_dir / KROKO
        missing = [f for f in _KROKO_FILES if not (path / f).exists()]
        if missing:
            raise FileNotFoundError(f"Kroko-Modell fehlt in {path}: {', '.join(missing)}")
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=str(path / "encoder.onnx"),
            decoder=str(path / "decoder.onnx"),
            joiner=str(path / "joiner.onnx"),
            tokens=str(path / "tokens.txt"),
            num_threads=min(4, self._threads()),
            decoding_method="greedy_search",
        )

    def preload(self, name: str) -> None:
        if name == KROKO and not self._kroko_installed():
            name = "small"
        self._model(name)

    def transcribe(self, pcm: bytes, *, model: str, language: str, prompt: str = "") -> Transcript:
        audio = pcm16_to_float(pcm)
        if audio.size < 16000 * 0.3:
            return Transcript("", language if language in LANGS else "de", -9.0, 1.0)
        if model == KROKO and (language == "en" or not self._kroko_installed()):
            # Kroko is a German model; English-only households keep Whisper, as does an image without Kroko.
            model = "small"
        if model == KROKO:
            return self._kroko(audio, language)
        return self._whisper(audio, model, language, prompt)

    def _kroko_installed(self) -> bool:
        path = self.models_dir / KROKO
        installed = all((path / f).exists() for f in _KROKO_FILES)
        if not installed and not self._warned:
            self._warned = True
            log.warning("Kroko-Modell fehlt in %s, Erkennung läuft mit Whisper small", path)
        return installed

    # ------------------------------------------------------------ kroko
    def _kroko(self, audio: np.ndarray, language: str) -> Transcript:
        recognizer = self._model(KROKO)
        audio = prepare_audio(audio)
        with self._decode:
            stream = recognizer.create_stream()
            stream.accept_waveform(16000, audio)
            stream.accept_waveform(16000, np.zeros(int(KROKO_TAIL_S * 16000), dtype=np.float32))
            stream.input_finished()
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            result = recognizer.get_result_all(stream)
        text = re.sub(r"\s+", " ", result.text or "").strip()
        probs = list(getattr(result, "ys_probs", None) or [])
        confidence = float(np.mean(probs)) if probs else 0.0
        lang = language if language in LANGS else guess_language(text)
        if not text:
            return Transcript("", lang, -9.0, 1.0)
        return Transcript(text, lang, confidence, 0.0)

    # ---------------------------------------------------------- whisper
    def _whisper(self, audio: np.ndarray, model: str, language: str, prompt: str) -> Transcript:
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


def prepare_audio(audio: np.ndarray) -> np.ndarray:
    """Lift a quiet recording without turning the noise floor into speech."""
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < 0.02 or peak >= 0.5:
        return audio
    return np.clip(audio * (0.5 / peak), -1.0, 1.0).astype(np.float32)


def guess_language(text: str) -> str:
    """German unless the words are clearly English."""
    words = re.findall(r"[a-zäöüß']+", text.lower())
    english = sum(w in _ENGLISH for w in words)
    german = sum(w in _GERMAN for w in words)
    return "en" if english >= 2 and english > german else "de"
