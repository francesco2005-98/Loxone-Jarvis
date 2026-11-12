"""Program changes in Loxone Config through its MCP server, only after a spoken yes."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .tools import Pending, Turn

if TYPE_CHECKING:
    from ..house import House
    from .gemini import Brain

log = logging.getLogger(__name__)

MAX_STEPS = 16

ASK = {
    "name": "ask_confirmation",
    "parameters_json_schema": {
        "type": "object",
        "properties": {"summary": {"type": "string", "description": "Ein gesprochener Satz: was genau geändert wird"}},
        "required": ["summary"],
    },
    "description": "Pflicht vor jeder Änderung: fasst die geplanten Änderungen zusammen und wartet auf ein gesprochenes Ja.",
}


def _system(save_tool: str | None, lang: str) -> str:
    save = (
        f"Nach allen Änderungen rufe {save_tool} auf, damit sie auf den Miniserver gespeichert werden, "
        "und sag am Ende, dass die Logik kurz neu startet."
        if save_tool
        else "Ein Werkzeug zum Speichern auf den Miniserver bietet Config nicht an. Sag am Ende, dass die Änderung "
        "im offenen Projekt liegt und in Loxone Config gespeichert werden muss."
    )
    language = "Deutsch" if lang == "de" else "English"
    return (
        "Du bearbeitest das offene Loxone-Config-Projekt über dessen Werkzeuge. Lesende Werkzeuge darfst du frei "
        "benutzen, um das Projekt zu verstehen. Bevor du irgendetwas änderst, rufe ask_confirmation mit einem "
        "einzigen gesprochenen Satz auf, der die Änderung beschreibt. Erst nach der Bestätigung änderst du. "
        f"{save} Passwörter und Zugangsdaten gibst du nie aus. Deine letzte Antwort ist ein kurzer gesprochener "
        f"Satz auf {language} ohne Markdown."
    )


class ConfigAgent:
    def __init__(self, house: "House", brain: "Brain"):
        self.house = house
        self.brain = brain

    async def run(self, task: str, turn: Turn) -> dict:
        from google.genai import types

        tools = await self.house.mcp.tools()
        if not tools:
            return {"error": "Loxone Config bietet keine Werkzeuge an. Ist Config offen und der MCP-Server an?"}
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=task)])]
        state = {"task": task, "contents": contents, "confirmed": False}
        return await self._loop(state, turn)

    async def resume(self, pending: Pending, turn: Turn) -> str:
        from google.genai import types

        state = pending.state
        state["confirmed"] = True
        state["contents"].append(
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="ask_confirmation", response={"confirmed": True})],
            )
        )
        result = await self._loop(state, turn)
        return result.get("result") or result.get("error") or "Erledigt."

    async def _loop(self, state: dict, turn: Turn) -> dict:
        from google.genai import types

        tools = await self.house.mcp.tools()
        by_name = {t.gemini_name: t for t in tools}
        save_tool = next((t.gemini_name for t in tools if t.saves_to_miniserver), None)
        declarations = [ASK] + [
            {"name": t.gemini_name, "description": t.description, "parameters_json_schema": t.schema} for t in tools
        ]
        config = self.brain._config(_system(save_tool, turn.lang), declarations)
        contents = state["contents"]
        for _ in range(MAX_STEPS):
            response = await self.brain.generate(contents, config)
            candidate = (response.candidates or [None])[0]
            if candidate is None or candidate.content is None:
                break
            contents.append(candidate.content)
            calls = response.function_calls or []
            if not calls:
                text = (response.text or "").strip()
                if state["confirmed"]:
                    self.house.events.log("config", "Loxone Config geändert", state["task"][:200])
                return {"result": text or "Erledigt."}
            parts = []
            for call in calls:
                args = dict(call.args or {})
                if call.name == "ask_confirmation":
                    if state["confirmed"]:
                        parts.append(types.Part.from_function_response(name=call.name, response={"confirmed": True}))
                        continue
                    summary = str(args.get("summary", "")).strip()
                    turn.pending = Pending("config", summary, state=state, lang=turn.lang)
                    return {
                        "status": "needs_confirmation",
                        "summary": summary,
                        "hint": "Lies die Zusammenfassung vor und frag, ob du sie umsetzen sollst.",
                    }
                tool = by_name.get(call.name)
                if tool is None:
                    result = {"error": "unbekanntes Werkzeug"}
                elif not tool.read_only and not state["confirmed"]:
                    result = {"error": "Erst ask_confirmation aufrufen und auf das Ja warten."}
                else:
                    try:
                        result = await self.house.mcp.call(tool.name, args)
                    except Exception as exc:  # noqa: BLE001
                        result = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                parts.append(types.Part.from_function_response(name=call.name, response=result))
            contents.append(types.Content(role="user", parts=parts))
        return {"error": "Die Änderung war zu umfangreich für einen Durchgang."}
