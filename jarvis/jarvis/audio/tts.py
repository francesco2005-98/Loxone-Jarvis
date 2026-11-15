"""Client for the piper container."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path

import aiohttp

from ..settings import Settings
from .pronounce import clip, pronounce

log = logging.getLogger(__name__)

# Instant answers repeat ("Sehr wohl, Sir. Licht im Esszimmer ist an."), and a high
# quality voice needs a third to half a second per sentence. Repeats come from memory,
# and from disk after a restart.
CACHE_ENTRIES = 256
CACHE_MAX_TEXT = 300
DISK_ENTRIES = 2000


class TtsError(RuntimeError):
    pass


class Piper:
    def __init__(self, base_url: str, settings: Settings, cache_dir: Path | None = None):
        self.base_url = base_url
        self.settings = settings
        self.cache_dir = cache_dir
        self._voices: list[dict] = []
        self._voices_at = 0.0
        self._cache: OrderedDict[tuple[str, str, float], bytes] = OrderedDict()
        self._pending: dict[tuple[str, str, float], asyncio.Future] = {}
        self._writes = 0

    async def voices(self) -> list[dict]:
        if self._voices and time.monotonic() - self._voices_at < 300:
            return self._voices
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(f"{self.base_url}/voices") as resp:
                if resp.status != 200:
                    raise TtsError("Piper antwortet nicht")
                self._voices = await resp.json()
                self._voices_at = time.monotonic()
        return self._voices

    def voice_for(self, lang: str) -> str:
        return self.settings.get("voice.en" if lang == "en" else "voice.de")

    async def synthesize(self, text: str, lang: str, voice: str | None = None) -> bytes:
        payload = {
            "text": clip(pronounce(text, lang)),
            "voice": voice or self.voice_for(lang),
            "length_scale": round(1.0 / float(self.settings.get("voice.speed")), 3),
        }
        key = (payload["text"], payload["voice"], payload["length_scale"])
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        running = self._pending.get(key)
        if running is None:
            running = self._pending[key] = asyncio.ensure_future(self._produce(key, payload))
            running.add_done_callback(lambda _, key=key: self._pending.pop(key, None))
        return await asyncio.shield(running)

    def prerender(self, text: str, lang: str) -> None:
        """Start synthesizing a sentence that is about to be spoken; say() then picks it up."""
        try:
            task = asyncio.ensure_future(self.synthesize(text, lang))
        except RuntimeError:
            return
        task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _produce(self, key: tuple[str, str, float], payload: dict) -> bytes:
        keep = len(payload["text"]) <= CACHE_MAX_TEXT
        audio = await asyncio.to_thread(self._read_disk, key) if keep else None
        if audio is None:
            audio = await self._post(payload)
            if keep:
                asyncio.get_running_loop().run_in_executor(None, self._write_disk, key, audio)
        if keep:
            self._cache[key] = audio
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
        return audio

    def _path(self, key: tuple[str, str, float]) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.wav"

    def _read_disk(self, key: tuple[str, str, float]) -> bytes | None:
        path = self._path(key)
        if path is None:
            return None
        try:
            data = path.read_bytes()
            os.utime(path)
            return data
        except OSError:
            return None

    def _write_disk(self, key: tuple[str, str, float], audio: bytes) -> None:
        path = self._path(key)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(audio)
            tmp.replace(path)
            self._writes += 1
            if self._writes % 100 == 1:
                self._trim()
        except OSError as exc:
            log.info("tts cache write failed: %s", exc)

    def _trim(self) -> None:
        files = sorted(self.cache_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)  # type: ignore[union-attr]
        for old in files[: max(0, len(files) - DISK_ENTRIES)]:
            old.unlink(missing_ok=True)

    async def _post(self, payload: dict) -> bytes:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(f"{self.base_url}/synthesize", json=payload) as resp:
                if resp.status == 404:
                    raise TtsError(f"Stimme {payload['voice']} ist nicht installiert")
                if resp.status != 200:
                    raise TtsError(f"Piper Fehler {resp.status}")
                return await resp.read()
