"""Gemini as the interpreter of free sentences. Text in, tool calls and a short reply out."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..location import sun_times
from ..loxone.catalog import ACTIONS
from .prompt import SpeakerInfo, build_system
from .tools import ToolRunner, Turn, settled

if TYPE_CHECKING:
    from ..house import House

log = logging.getLogger(__name__)

MAX_STEPS = 6
HISTORY_ITEMS = 12
REQUEST_TIMEOUT_MS = 25000


class BrainUnavailable(RuntimeError):
    """Gemini could not be reached or refused the key. Triggers the offline sentences."""


class Brain:
    def __init__(self, house: "House"):
        self.house = house
        self.runner = ToolRunner(house)
        self.runner.search = self.search
        self._client = None
        self._client_key: str | None = None
        self._models: list[str] = []
        self._models_at = 0.0
        self.last_latency_ms: int | None = None
        self.last_error: str | None = None

    # ------------------------------------------------------------- client
    def _genai(self):
        key = self.house.secrets.get("gemini.api_key")
        if not key:
            raise BrainUnavailable("Kein Gemini-Schlüssel hinterlegt")
        if self._client is None or key != self._client_key:
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
            self._client_key = key
        return self._client

    @property
    def model(self) -> str:
        return self.house.settings.get("gemini.model")

    def _thinking(self):
        """Thinking level exists from Gemini 3 on; Flash can go down to minimal, which keeps voice turns fast."""
        from google.genai import types

        match = re.match(r"gemini-(\d+)", self.model)
        if match and int(match.group(1)) >= 3:
            level = types.ThinkingLevel.MINIMAL if "flash" in self.model else types.ThinkingLevel.LOW
            return types.ThinkingConfig(thinking_level=level)
        return None

    def _config(self, system: str, declarations: list[dict] | None = None, search: bool = False):
        from google.genai import types

        tools = []
        if declarations:
            tools.append(types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in declarations]))
        if search:
            tools.append(types.Tool(google_search=types.GoogleSearch()))
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=tools or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=self._thinking(),
            max_output_tokens=1024,
        )

    async def generate(self, contents: list, config) -> Any:
        from google.genai import errors

        client = self._genai()
        started = time.monotonic()
        try:
            response = await client.aio.models.generate_content(model=self.model, contents=contents, config=config)
        except errors.APIError as exc:
            self.last_error = f"{exc.code}: {exc.message}"[:200]
            raise BrainUnavailable(self.last_error) from exc
        except (asyncio.TimeoutError, OSError) as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            raise BrainUnavailable(self.last_error) from exc
        except Exception as exc:  # noqa: BLE001  httpx and transport errors
            if type(exc).__module__.startswith(("httpx", "aiohttp", "httpcore")):
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                raise BrainUnavailable(self.last_error) from exc
            raise
        self.last_latency_ms = int((time.monotonic() - started) * 1000)
        self.last_error = None
        usage = getattr(response, "usage_metadata", None)
        searches = 0
        for candidate in getattr(response, "candidates", None) or []:
            meta = getattr(candidate, "grounding_metadata", None)
            if meta and meta.web_search_queries:
                searches += len(meta.web_search_queries)
        self.house.usage.record(
            input_tokens=(getattr(usage, "prompt_token_count", 0) or 0) + (getattr(usage, "tool_use_prompt_token_count", 0) or 0),
            output_tokens=(getattr(usage, "candidates_token_count", 0) or 0) + (getattr(usage, "thoughts_token_count", 0) or 0),
            searches=searches,
        )
        return response

    # -------------------------------------------------------------- models
    async def models(self) -> list[str]:
        if self._models and time.monotonic() - self._models_at < 3600:
            return self._models
        names: list[str] = []
        try:
            pager = await self._genai().aio.models.list()
            async for model in pager:
                name = (model.name or "").removeprefix("models/")
                actions = model.supported_actions or []
                if name.startswith("gemini") and ("generateContent" in actions or not actions):
                    if not any(x in name for x in ("tts", "image", "embedding", "live", "audio", "transcribe")):
                        names.append(name)
        except BrainUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("model list failed: %s", exc)
        current = self.model
        if current not in names:
            names.append(current)
        self._models = sorted(set(names), key=lambda n: (("flash" not in n), n), reverse=False)
        self._models_at = time.monotonic()
        return self._models

    async def test(self) -> dict:
        from google.genai import types

        response = await self.generate(
            [types.Content(role="user", parts=[types.Part.from_text(text="Antworte nur mit: bereit")])],
            types.GenerateContentConfig(thinking_config=self._thinking(), max_output_tokens=50),
        )
        return {"ok": True, "model": self.model, "reply": (response.text or "").strip()[:60], "ms": self.last_latency_ms}

    # -------------------------------------------------------------- answer
    def system_for(self, turn: Turn, config_available: bool) -> str:
        house = self.house
        area = turn.area
        entries = house.catalog.entries(only_allowed=True)
        devices = []
        for entry in entries:
            state = house.catalog.describe_state(entry, house.miniserver.state)
            extra = house.catalog.extras(entry, house.miniserver.state)
            label = {"light": "Licht", "climate": "Klima", "shading": "Beschattung", "gate": "Tor", "alarm": "Alarm"}.get(
                entry.group, "Gerät"
            )
            flag = " (Bestätigung nötig)" if entry.critical else ""
            devices.append(
                " | ".join(
                    [
                        entry.short_id,
                        entry.name,
                        entry.room or "-",
                        f"{label}/{entry.type}{flag}",
                        ", ".join(entry.aliases) or "-",
                        ",".join(ACTIONS.get(entry.type, ())),
                        state or "-",
                        extra or "-",
                    ]
                )
            )
        anrede, name, notes = house.people.address(turn.person_id)
        speaker = SpeakerInfo(anrede, name, notes) if anrede else None
        main = house.catalog.get(area.main_light) if area.main_light else None
        return build_system(
            persona=house.settings.get("gemini.system_prompt"),
            now=datetime.now(house.settings.tz),
            area_name=area.name,
            area_rooms=area.rooms,
            main_light=f"{main.short_id} ({main.name})" if main else None,
            speaker=speaker,
            household_notes=house.settings.get("house.notes"),
            devices=devices,
            areas=[a.name for a in house.areas.list()],
            favorites=house.cached_favorites(area),
            config_available=config_available,
            location=self.location_line(),
        )

    def location_line(self) -> str | None:
        settings = self.house.settings
        label = settings.get("house.location")
        lat, lon = settings.get("house.lat"), settings.get("house.lon")
        if lat is None or lon is None:
            return label or None
        line = f"{label or 'ohne Adresse'} (Breite {lat:.4f}, Länge {lon:.4f})"
        tz = settings.tz
        rise, sets = sun_times(lat, lon, datetime.now(tz).date(), tz)
        if rise and sets:
            line += f"; heute Sonnenaufgang {rise:%H:%M}, Sonnenuntergang {sets:%H:%M}"
        return line

    async def answer(self, turn: Turn, history: list) -> tuple[str, list]:
        from google.genai import types

        config_available = self.house.mcp.configured
        system = self.system_for(turn, config_available)
        declarations = self.runner.declarations(config_available)
        config = self._config(system, declarations)
        contents = list(history[-HISTORY_ITEMS:])
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=turn.text)]))
        text = ""
        for _ in range(MAX_STEPS):
            response = await self.generate(contents, config)
            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                break
            contents.append(candidate.content)
            calls = response.function_calls or []
            if not calls:
                text = (response.text or "").strip()
                break
            parts = []
            done_already = True
            for call in calls:
                args = dict(call.args or {})
                result = await self.runner.run(call.name, args, turn)
                done_already = done_already and settled(call.name, args, result)
                parts.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=parts))
            spoken = _spoken(calls, candidate)
            if done_already and spoken and turn.pending is None:
                text = spoken
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))
                break
            if turn.final_text and len(calls) == 1 and calls[0].name == "web_search":
                text = turn.final_text
                contents.append(types.Content(role="model", parts=[types.Part.from_text(text=text)]))
                break
        if not text:
            text = "Erledigt." if turn.done else ("Das habe ich nicht verstanden." if turn.lang == "de" else "I didn't catch that.")
        return text, contents[-HISTORY_ITEMS:]

    async def search(self, query: str, turn: Turn) -> str:
        from google.genai import types

        lang = "Deutsch" if turn.lang == "de" else "English"
        system = (
            self.house.settings.get("gemini.system_prompt")
            + f"\nBeantworte die Frage mit der Google-Suche, auf {lang}, in höchstens zwei gesprochenen Sätzen. "
            "Keine Quellenangaben, keine URLs, kein Markdown. Jetzt ist "
            + datetime.now(self.house.settings.tz).strftime("%d.%m.%Y %H:%M")
            + "."
        )
        where = self.location_line()
        if where:
            system += f" Das Haus steht hier: {where}. Fragen ohne Ort (Wetter, Öffnungszeiten, Verkehr) beziehen sich darauf."
        response = await self.generate(
            [types.Content(role="user", parts=[types.Part.from_text(text=query)])],
            self._config(system, search=True),
        )
        return (response.text or "").strip() or ("Dazu finde ich nichts." if turn.lang == "de" else "I found nothing on that.")


def _spoken(calls: list, candidate: Any) -> str:
    """The confirmation the model already wrote: reply fields of the calls, else plain text parts."""
    replies: list[str] = []
    for call in calls:
        reply = str((call.args or {}).get("reply") or "").strip()
        if reply and reply not in replies:
            replies.append(reply)
    if replies:
        return " ".join(replies)
    parts = getattr(candidate.content, "parts", None) or []
    return " ".join(p.text.strip() for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()
