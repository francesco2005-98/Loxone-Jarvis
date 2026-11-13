"""Piper text-to-speech, offline, for the jarvis container only.

GET  /voices      -> list of installed voices
POST /synthesize  -> {"text", "voice", "length_scale"} returns audio/wav

Multi-speaker models appear once per speaker as "<model>#<speaker>".
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from piper import PiperVoice, SynthesisConfig

VOICES_DIR = Path(os.environ.get("PIPER_VOICES", "/voices"))
PORT = int(os.environ.get("PIPER_PORT", "5000"))
MAX_TEXT = 1500
VOICE_ID = re.compile(r"^([a-z]{2}_[A-Z]{2}-[A-Za-z0-9_]+-(?:x_low|low|medium|high))(?:#([A-Za-z0-9_]+))?$")
MAX_SPEAKERS = 12
SKIP_SPEAKERS = {"angry", "disgusted", "drunk"}

log = logging.getLogger("piper")
_cache: dict[str, PiperVoice] = {}
_lock = threading.Lock()


def installed() -> list[dict]:
    voices = []
    for model in sorted(VOICES_DIR.glob("*.onnx")):
        config_path = model.with_suffix(".onnx.json")
        if not config_path.exists():
            continue
        try:
            config = json.loads(config_path.read_text("utf-8"))
        except ValueError:
            continue
        voice_id = model.stem
        lang, _, rest = voice_id.partition("-")
        name, _, quality = rest.rpartition("-")
        base = {
            "lang": lang.split("_")[0],
            "locale": lang,
            "quality": quality,
            "sample_rate": config.get("audio", {}).get("sample_rate", 22050),
        }
        title = name.replace("_", " ").title()
        speakers = [s for s in (config.get("speaker_id_map") or {}) if s not in SKIP_SPEAKERS][:MAX_SPEAKERS]
        if len(speakers) > 1:
            for speaker in speakers:
                numbered = speaker.isdigit() or (speaker[:1] == "p" and speaker[1:].isdigit())
                label = f"Sprecher {speaker}" if numbered else speaker.title()
                voices.append({**base, "id": f"{voice_id}#{speaker}", "name": f"{title} · {label}"})
        else:
            voices.append({**base, "id": voice_id, "name": title})
    return voices


def load(voice_id: str) -> PiperVoice:
    match = VOICE_ID.match(voice_id)
    if not match:
        raise KeyError(voice_id)
    voice_id = match.group(1)
    with _lock:
        voice = _cache.get(voice_id)
        if voice is None:
            model = VOICES_DIR / f"{voice_id}.onnx"
            if not model.exists():
                raise KeyError(voice_id)
            voice = PiperVoice.load(str(model))
            _cache[voice_id] = voice
        return voice


def speaker_id(voice: PiperVoice, voice_id: str) -> int | None:
    match = VOICE_ID.match(voice_id)
    speaker = match.group(2) if match else None
    if not speaker:
        return None
    mapping = getattr(voice.config, "speaker_id_map", None) or {}
    if speaker not in mapping:
        raise KeyError(voice_id)
    return int(mapping[speaker])


def synthesize(text: str, voice_id: str, length_scale: float) -> bytes:
    voice = load(voice_id)
    config = SynthesisConfig(speaker_id=speaker_id(voice, voice_id), length_scale=length_scale)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        voice.synthesize_wav(text, wav, syn_config=config)
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "piper"

    def log_message(self, fmt: str, *args) -> None:  # no request logging: texts are private
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/voices":
            self._json(200, installed())
        elif self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/synthesize":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64 * 1024:
                raise ValueError("request too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            text = str(body.get("text", "")).strip()
            voice_id = str(body.get("voice", ""))
            scale = float(body.get("length_scale", 1.0))
            if not text or len(text) > MAX_TEXT:
                raise ValueError("text empty or too long")
            scale = max(0.5, min(2.0, scale))
            audio = synthesize(text, voice_id, scale)
        except KeyError:
            self._json(404, {"error": "voice not installed"})
            return
        except (ValueError, TypeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except Exception:  # noqa: BLE001
            log.exception("synthesis failed")
            self._json(500, {"error": "synthesis failed"})
            return
        self._send(200, audio, "audio/wav")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    voices = installed()
    log.info("%d voices installed: %s", len(voices), ", ".join(v["id"] for v in voices))
    for voice in voices[:2]:
        load(voice["id"])
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
