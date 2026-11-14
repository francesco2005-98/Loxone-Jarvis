"""Client for the piper container."""

from __future__ import annotations

import time
from collections import OrderedDict

import aiohttp

from ..settings import Settings
from .pronounce import clip, pronounce

# Instant answers repeat ("Sehr wohl, Sir. Licht im Esszimmer ist an."), and a high
# quality voice needs over half a second per sentence. Repeats come from memory.
CACHE_ENTRIES = 256
CACHE_MAX_TEXT = 300


class TtsError(RuntimeError):
    pass


class Piper:
    def __init__(self, base_url: str, settings: Settings):
        self.base_url = base_url
        self.settings = settings
        self._voices: list[dict] = []
        self._voices_at = 0.0
        self._cache: OrderedDict[tuple[str, str, float], bytes] = OrderedDict()

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
        audio = await self._post(payload)
        if len(payload["text"]) <= CACHE_MAX_TEXT:
            self._cache[key] = audio
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)
        return audio

    async def _post(self, payload: dict) -> bytes:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(f"{self.base_url}/synthesize", json=payload) as resp:
                if resp.status == 404:
                    raise TtsError(f"Stimme {payload['voice']} ist nicht installiert")
                if resp.status != 200:
                    raise TtsError(f"Piper Fehler {resp.status}")
                return await resp.read()
