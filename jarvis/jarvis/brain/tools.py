"""Functions Gemini may call. Every call is validated against the allowlist here."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..areas import Area
from ..loxone.catalog import ALL_ACTIONS, ActionError, Entry

if TYPE_CHECKING:
    from ..house import House

log = logging.getLogger(__name__)


@dataclass
class Pending:
    """Something that waits for a spoken yes in this Bereich."""

    kind: str  # "control" | "config"
    question: str
    actions: list[tuple[Entry, str, Any, Any]] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    lang: str = "de"


@dataclass
class Turn:
    area: Area
    lang: str
    text: str
    person_id: str | None = None
    pending: Pending | None = None
    final_text: str | None = None
    done: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


DECLARATIONS: list[dict] = [
    {
        "name": "control",
        "description": "Schaltet freigegebene Geräte aus der Geräteliste. Mehrere Aktionen in einem Aufruf möglich.",
        "parameters_json_schema": _schema(
            {
                "actions": {
                    "type": "array",
                    "items": _schema(
                        {
                            "device": {"type": "string", "description": "Kennung aus der Geräteliste, z. B. L3"},
                            "action": {"type": "string", "enum": ALL_ACTIONS},
                            "value": {
                                "type": "string",
                                "description": "Zahl (Prozent, Grad Celsius, Farbton 0-360, Kelvin) oder Name einer Szene/Auswahl",
                            },
                            "value2": {"type": "number", "description": "Helligkeit 1-100 bei color/white"},
                        },
                        ["device", "action"],
                    ),
                }
            },
            ["actions"],
        ),
    },
    {
        "name": "music",
        "description": "Musik und Radio über den Loxone Audioserver. Ohne Bereich gilt der Bereich, in dem gesprochen wurde.",
        "parameters_json_schema": _schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "next", "previous", "volume", "louder", "quieter", "favorite"],
                },
                "value": {"type": "string", "description": "Lautstärke 0-100 oder Name/Nummer des Favoriten"},
                "areas": {"type": "array", "items": {"type": "string"}, "description": "Namen der Bereiche"},
            },
            ["action"],
        ),
    },
    {
        "name": "announce",
        "description": "Durchsage in einem, mehreren oder allen Bereichen (areas: [\"alle\"]).",
        "parameters_json_schema": _schema(
            {
                "text": {"type": "string", "description": "Was gesagt werden soll, fertig formuliert"},
                "areas": {"type": "array", "items": {"type": "string"}},
            },
            ["text", "areas"],
        ),
    },
    {
        "name": "timer",
        "description": "Timer und Erinnerungen in der Zeitzone des Hauses. Fällige Timer spricht Jarvis in diesem Bereich.",
        "parameters_json_schema": _schema(
            {
                "action": {"type": "string", "enum": ["set", "cancel", "list"]},
                "kind": {"type": "string", "enum": ["timer", "reminder"]},
                "seconds": {"type": "integer", "description": "Dauer ab jetzt in Sekunden"},
                "at": {"type": "string", "description": "Ortszeit ISO 8601, z. B. 2026-11-12T20:00"},
                "label": {"type": "string", "description": "Kurzer Name, z. B. Nudeln"},
                "message": {"type": "string", "description": "Text der Erinnerung"},
            },
            ["action"],
        ),
    },
    {
        "name": "remember",
        "description": "Merkt sich eine Vorliebe oder Notiz, nur auf ausdrücklichen Wunsch.",
        "parameters_json_schema": _schema(
            {
                "scope": {"type": "string", "enum": ["person", "household"]},
                "note": {"type": "string"},
            },
            ["scope", "note"],
        ),
    },
    {
        "name": "rename",
        "description": (
            "Ändert Jarvis' eigene Zuordnung: alias = weitere Sprechweise für ein Gerät, "
            "main_light = Hauptlicht dieses Bereichs, zone = Audio-Zone dieses Bereichs. Ändert nichts in Loxone."
        ),
        "parameters_json_schema": _schema(
            {
                "kind": {"type": "string", "enum": ["alias", "main_light", "zone"]},
                "device": {"type": "string", "description": "Kennung bei alias und main_light"},
                "alias": {"type": "string"},
                "zone": {"type": "string", "description": "Name der Audio-Zone"},
            },
            ["kind"],
        ),
    },
    {
        "name": "web_search",
        "description": "Google-Suche für Fragen außerhalb der Haussteuerung. Liefert eine fertige gesprochene Antwort.",
        "parameters_json_schema": _schema({"query": {"type": "string"}}, ["query"]),
    },
]

CONFIG_DECLARATION = {
    "name": "loxone_config",
    "description": (
        "Neue Logik, neue Bausteine oder Programmänderungen im offenen Loxone-Config-Projekt. "
        "Beschreibe die Aufgabe vollständig in einem Satz."
    ),
    "parameters_json_schema": _schema({"task": {"type": "string"}}, ["task"]),
}


class ToolRunner:
    def __init__(self, house: "House"):
        self.house = house
        self.search: Callable[[str, Turn], Awaitable[str]] | None = None
        self.config_task: Callable[[str, Turn], Awaitable[dict]] | None = None

    def declarations(self, config_available: bool) -> list[dict]:
        return DECLARATIONS + ([CONFIG_DECLARATION] if config_available else [])

    async def run(self, name: str, args: dict, turn: Turn) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return {"error": f"unbekanntes Werkzeug {name}"}
        try:
            return await handler(args or {}, turn)
        except ActionError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}

    # -------------------------------------------------------------- control
    async def _tool_control(self, args: dict, turn: Turn) -> dict:
        catalog = self.house.catalog
        results: list[dict] = []
        critical: list[tuple[Entry, str, Any, Any]] = []
        for item in args.get("actions") or []:
            device = str(item.get("device", "")).strip().upper()
            action = str(item.get("action", "")).strip()
            entry = catalog.get(device)
            if entry is None or not entry.allowed:
                results.append({"device": device, "error": "nicht freigegeben oder unbekannt"})
                continue
            if entry.critical:
                critical.append((entry, action, item.get("value"), item.get("value2")))
                continue
            results.append(await self.execute(entry, action, item.get("value"), item.get("value2"), turn))
        if critical:
            question = ", ".join(f"{e.name}: {a}" for e, a, _, _ in critical)
            turn.pending = Pending("control", question, critical, lang=turn.lang)
            results.append(
                {
                    "status": "needs_confirmation",
                    "devices": [e.name for e, *_ in critical],
                    "hint": "Frag in einem kurzen Satz, ob du das wirklich tun sollst. Erst ein Ja führt es aus.",
                }
            )
        return {"results": results}

    async def execute(self, entry: Entry, action: str, value: Any, value2: Any, turn: Turn | None) -> dict:
        house = self.house
        commands = house.catalog.commands(entry, action, value, value2, house.miniserver.state)
        for uuid, cmd in commands:
            reply = await house.miniserver.command(uuid, cmd)
            if not reply.ok:
                return {"device": entry.short_id, "error": f"Miniserver meldet Code {reply.code}"}
        label = f"{entry.name}: {action}" + (f" {value}" if value not in (None, "") else "")
        if turn is not None:
            turn.done.append(label)
        house.events.log("action", label, entry.room or "")
        return {"device": entry.short_id, "ok": True}

    # ---------------------------------------------------------------- music
    def _target_areas(self, names: list[str] | None, turn: Turn) -> list[Area]:
        if not names:
            return [turn.area]
        if any(str(n).strip().lower() in ("alle", "all", "überall", "everywhere", "haus") for n in names):
            return [a for a in self.house.areas.list() if a.zone_uuid]
        found: list[Area] = []
        for name in names:
            area = self.house.areas.find(str(name))
            if area is None:
                raise ActionError(f"Bereich '{name}' gibt es nicht")
            if area not in found:
                found.append(area)
        return found

    async def _tool_music(self, args: dict, turn: Turn) -> dict:
        house = self.house
        action = args.get("action")
        value = args.get("value")
        done = []
        for area in self._target_areas(args.get("areas"), turn):
            zone = house.zone_of(area)
            if zone is None:
                done.append({"area": area.name, "error": "keine Audio-Zone zugeordnet"})
                continue
            if action == "favorite":
                await house.play_favorite(zone, value)
            else:
                cmd = {
                    "play": "play",
                    "pause": "pause",
                    "next": "next",
                    "previous": "prev",
                    "louder": "volUp",
                    "quieter": "volDown",
                }.get(action)
                if action == "volume":
                    try:
                        cmd = f"volume/{max(0, min(100, int(float(value))))}"
                    except (TypeError, ValueError) as exc:
                        raise ActionError("Lautstärke fehlt") from exc
                if cmd is None:
                    raise ActionError("unbekannte Musikaktion")
                reply = await house.miniserver.command(zone.uuid, cmd)
                if not reply.ok:
                    done.append({"area": area.name, "error": f"Code {reply.code}"})
                    continue
            turn.done.append(f"Musik {action} {area.name}")
            done.append({"area": area.name, "ok": True})
        return {"results": done}

    # ------------------------------------------------------------- announce
    async def _tool_announce(self, args: dict, turn: Turn) -> dict:
        text = str(args.get("text", "")).strip()
        if not text:
            raise ActionError("Text fehlt")
        areas = [a for a in self._target_areas(args.get("areas"), turn) if a.zone_uuid]
        if not areas:
            raise ActionError("Kein Bereich mit Audio-Zone gefunden")
        self.house.background(self.house.speech.say(text, turn.lang, areas), "announce")
        turn.done.append("Durchsage: " + ", ".join(a.name for a in areas))
        return {"ok": True, "areas": [a.name for a in areas]}

    # ---------------------------------------------------------------- timer
    async def _tool_timer(self, args: dict, turn: Turn) -> dict:
        timers = self.house.timers
        action = args.get("action")
        if action == "list":
            items = timers.list(turn.area.id)
            return {"timers": [{"label": t["label"], "kind": t["kind"], "due": timers.describe(t)} for t in items]}
        if action == "cancel":
            count = timers.cancel(bereich_id=turn.area.id, label=args.get("label") or None)
            return {"cancelled": count}
        kind = args.get("kind") or "timer"
        if args.get("seconds"):
            due = time.time() + max(1, int(args["seconds"]))
        elif args.get("at"):
            due = timers.parse_at(str(args["at"]))
        else:
            raise ActionError("Dauer oder Zeitpunkt fehlt")
        timer = timers.add(
            kind=kind,
            due_at=due,
            label=str(args.get("label") or ("Timer" if kind == "timer" else "Erinnerung")),
            text=str(args.get("message") or ""),
            bereich_id=turn.area.id,
            lang=turn.lang,
        )
        turn.done.append(f"{kind} {timer['label']}")
        return {"ok": True, "due": timers.describe(timer)}

    # ------------------------------------------------------------- remember
    async def _tool_remember(self, args: dict, turn: Turn) -> dict:
        note = str(args.get("note", "")).strip()[:300]
        if not note:
            raise ActionError("Notiz fehlt")
        if args.get("scope") == "person" and turn.person_id:
            self.house.people.append_note(turn.person_id, note)
            return {"ok": True, "stored_as": "person"}
        notes = (self.house.settings.get("house.notes") + "\n" + note).strip()[-4000:]
        self.house.settings.update({"house.notes": notes})
        return {"ok": True, "stored_as": "household"}

    # --------------------------------------------------------------- rename
    async def _tool_rename(self, args: dict, turn: Turn) -> dict:
        house = self.house
        kind = args.get("kind")
        if kind in ("alias", "main_light"):
            entry = house.catalog.get(str(args.get("device", "")))
            if entry is None or not entry.allowed:
                raise ActionError("Gerät nicht freigegeben")
            if kind == "alias":
                alias = str(args.get("alias", "")).strip()
                if not alias:
                    raise ActionError("Sprechweise fehlt")
                house.catalog.add_alias(entry.short_id, alias)
                house.events.log("mapping", f"{entry.name} heißt auch „{alias}“")
                return {"ok": True}
            house.areas.update(turn.area.id, main_light=entry.short_id)
            house.events.log("mapping", f"Hauptlicht {turn.area.name}: {entry.name}")
            return {"ok": True}
        if kind == "zone":
            wanted = str(args.get("zone", "")).strip().lower()
            for zone in house.catalog.zones():
                if zone["name"].lower() == wanted or wanted in zone["name"].lower():
                    house.areas.update(turn.area.id, zone_uuid=zone["uuid"])
                    house.events.log("mapping", f"Zone {turn.area.name}: {zone['name']}")
                    return {"ok": True}
            raise ActionError("Zone nicht gefunden")
        raise ActionError("unbekannte Art")

    # --------------------------------------------------------------- search
    async def _tool_web_search(self, args: dict, turn: Turn) -> dict:
        if self.search is None:
            raise ActionError("Suche nicht verfügbar")
        answer = await self.search(str(args.get("query", "")), turn)
        turn.final_text = answer
        return {"answer": answer}

    # --------------------------------------------------------------- config
    async def _tool_loxone_config(self, args: dict, turn: Turn) -> dict:
        if self.config_task is None:
            raise ActionError("Loxone Config ist nicht verbunden")
        return await self.config_task(str(args.get("task", "")), turn)
