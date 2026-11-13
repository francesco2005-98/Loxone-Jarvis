"""Client for the piper container."""

from __future__ import annotations

import time

import aiohttp

from ..settings import Settings
from .pronounce import clip, pronounce


class TtsError(RuntimeError):
    pass


class Piper:
    def __init__(self, base_url: str, settings: Settings):
        self.base_url = base_url
        self.settings = settings
        self._voices: list[dict] = []
        self._voices_at = 0.0

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
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.post(f"{self.base_url}/synthesize", json=payload) as resp:
                if resp.status == 404:
                    raise TtsError(f"Stimme {payload['voice']} ist nicht installiert")
                if resp.status != 200:
                    raise TtsError(f"Piper Fehler {resp.status}")
                return await resp.read()
