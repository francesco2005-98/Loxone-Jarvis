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
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..audio.wav import pcm16_to_float

log = logging.getLogger(__name__)

LANGS = ("de", "en")
KROKO = "kroko"
# The streaming model only emits the last words once it has heard silence after them.
KROKO_TAIL_S = 0.8
_KROKO_FILES = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")
# A modest nudge. The score is added per token, so a long name must not drown out the sentence.
_HOTWORD_SCORE = 0.6

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
        self._bias = False

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
        common = dict(
            encoder=str(path / "encoder.onnx"),
            decoder=str(path / "decoder.onnx"),
            joiner=str(path / "joiner.onnx"),
            tokens=str(path / "tokens.txt"),
            num_threads=min(4, self._threads()),
            sample_rate=16000,
            feature_dim=80,
            decoding_method="modified_beam_search",
            max_active_paths=4,
            blank_penalty=0.5,
            model_type="zipformer2",
            provider="cpu",
            enable_endpoint_detection=False,
        )
        # Beam search keeps a few wordings instead of the first guess. blank_penalty
        # stops the model dropping the last words. Names of the house are biased
        # per sentence, so a room is not rewritten into a similar everyday word.
        try:
            vocab = _bpe_vocab(path / "tokens.txt")
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                **common,
                modeling_unit="bpe",
                bpe_vocab=str(vocab),
                hotwords_score=_HOTWORD_SCORE,
            )
            self._bias = True
            return recognizer
        except Exception:
            log.warning("Kroko hört ohne Namensliste zu", exc_info=True)
            self._bias = False
        try:
            return sherpa_onnx.OnlineRecognizer.from_transducer(**common)
        except Exception:
            log.warning("Kroko fällt auf die einfache Suche zurück", exc_info=True)
            basic = {key: value for key, value in common.items() if key not in {"model_type", "blank_penalty", "max_active_paths"}}
            basic["decoding_method"] = "greedy_search"
            return sherpa_onnx.OnlineRecognizer.from_transducer(**basic)

    def preload(self, name: str) -> None:
        if name == KROKO and not self._kroko_installed():
            name = "small"
        self._model(name)

    def transcribe(self, pcm: bytes, *, model: str, language: str, prompt: str = "") -> Transcript:
        audio = prepare_audio(pcm16_to_float(pcm))
        if audio.size < 16000 * 0.3:
            return Transcript("", language if language in LANGS else "de", -9.0, 1.0)
        if model == KROKO and (language == "en" or not self._kroko_installed()):
            # Kroko is a German model; English-only households keep Whisper, as does an image without Kroko.
            model = "small"
        if model == KROKO:
            return self._kroko(audio, language, prompt)
        return self._whisper(audio, model, language, prompt)

    def _kroko_installed(self) -> bool:
        path = self.models_dir / KROKO
        installed = all((path / f).exists() for f in _KROKO_FILES)
        if not installed and not self._warned:
            self._warned = True
            log.warning("Kroko-Modell fehlt in %s, Erkennung läuft mit Whisper small", path)
        return installed

    # ------------------------------------------------------------ kroko
    def _kroko(self, audio: np.ndarray, language: str, prompt: str = "") -> Transcript:
        recognizer = self._model(KROKO)
        audio = trim_silence(audio)
        hotwords = hotwords_from_prompt(prompt) if self._bias else ""
        tail = np.zeros(int(KROKO_TAIL_S * 16000), dtype=np.float32)
        with self._decode:
            try:
                stream = recognizer.create_stream(hotwords) if hotwords else recognizer.create_stream()
            except Exception:
                log.debug("Namensliste übersprungen", exc_info=True)
                stream = recognizer.create_stream()
            # The streaming encoder expects short pieces. One long buffer leaves
            # the start or the end of a sentence half decoded.
            for start in range(0, audio.size, 1600):
                stream.accept_waveform(16000, audio[start : start + 1600])
                while recognizer.is_ready(stream):
                    recognizer.decode_stream(stream)
            stream.accept_waveform(16000, tail)
            stream.input_finished()
            while recognizer.is_ready(stream):
                recognizer.decode_stream(stream)
            result = recognizer.get_result_all(stream)
        text = polish(re.sub(r"\s+", " ", result.text or "").strip())
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
        text = polish(" ".join(s.text.strip() for s in parts).strip())
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
    """Lift quiet speech. A single click must not keep the sentence quiet.

    The level is the 99th percentile, not the one loudest sample. A recording
    that is only noise (below about -42 dB) stays quiet, so the model does not
    invent words from the floor.
    """
    if audio.size == 0:
        return audio
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak < 1e-4:
        return audio.astype(np.float32)
    mean = float(np.mean(audio))
    # Only a real waveform crosses zero. A constant test tone must keep its level.
    if float(np.min(audio)) < 0 < float(np.max(audio)) and abs(mean) < 0.25 * peak:
        audio = audio - mean
    level = float(np.quantile(np.abs(audio), 0.99))
    if level < 0.008 or level >= 0.5:
        return np.clip(audio, -1.0, 1.0).astype(np.float32)
    return np.clip(audio * (0.5 / level), -1.0, 1.0).astype(np.float32)


def trim_silence(audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Keep a short margin around the voice and drop a long quiet wait."""
    if audio.size < sample_rate:
        return audio
    frame = 320
    loud = [i for i in range(0, audio.size, frame) if float(np.max(np.abs(audio[i : i + frame]))) > 0.05]
    if len(loud) < 3:
        return audio
    start = max(0, loud[0] - int(0.2 * sample_rate))
    end = min(audio.size, loud[-1] + frame + int(0.6 * sample_rate))
    if end - start < int(0.3 * sample_rate):
        return audio
    return np.ascontiguousarray(audio[start:end])


def hotwords_from_prompt(prompt: str) -> str:
    """House names, one phrase per line, in the form Kroko's stream expects."""
    seen: set[str] = set()
    words: list[str] = []
    for raw in re.split(r"[,/\n]+", prompt or ""):
        word = raw.strip()
        key = word.casefold()
        if not 3 <= len(word) <= 40 or key in seen:
            continue
        seen.add(key)
        words.append(word)
        if len(words) >= 48:
            break
    return "/".join(words)


def polish(text: str) -> str:
    """Mend the short garbage words this German model writes instead of a command."""
    if not text:
        return text
    text = re.sub(r"\bpro zent\b", "Prozent", text, flags=re.IGNORECASE)

    def one(match: re.Match[str]) -> str:
        return _MISHEARD.get(match.group(0).casefold(), match.group(0))

    return re.sub(r"[A-Za-zÄÖÜäöüß]+", one, text)


# Only forms that are not German words. A real word is left for the name corrector.
_MISHEARD = {
    "lich": "Licht",
    "lichd": "Licht",
    "lcht": "Licht",
    "rolos": "Rollos",
    "jalusie": "Jalousie",
    "jalusien": "Jalousien",
    "heizug": "Heizung",
    "heizunk": "Heizung",
    "proznt": "Prozent",
    "automatick": "Automatik",
    "automatig": "Automatik",
    "garagntor": "Garagentor",
    "garagentohr": "Garagentor",
    "fensta": "Fenster",
    "lamelen": "Lamellen",
}


def _bpe_vocab(tokens: Path) -> Path:
    """A unigram list so house names can be biased. Longer pieces win the split."""
    lines = ["<unk>\t0", "<s>\t0", "</s>\t0"]
    for raw in tokens.read_text(encoding="utf-8").splitlines():
        piece, sep, _idx = raw.rpartition(" ")
        if not sep or not piece or piece.startswith("<"):
            continue
        lines.append(f"{piece}\t{-0.05 / max(len(piece), 1):.4f}")
    text = "\n".join(lines) + "\n"
    dest = tokens.with_name("bpe.vocab")
    try:
        if not dest.exists() or dest.read_text(encoding="utf-8") != text:
            dest.write_text(text, encoding="utf-8")
        return dest
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "jarvis-kroko-bpe.vocab"
        fallback.write_text(text, encoding="utf-8")
        return fallback


def guess_language(text: str) -> str:
    """German unless the words are clearly English."""
    words = re.findall(r"[a-zäöüß']+", text.lower())
    english = sum(w in _ENGLISH for w in words)
    german = sum(w in _GERMAN for w in words)
    return "en" if english >= 2 and english > german else "de"
