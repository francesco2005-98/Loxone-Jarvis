"""Functions Gemini may call. Every call is validated against the allowlist here."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..areas import Area
from ..loxone.catalog import ACTIONS, ALL_ACTIONS, ActionError, Entry
from ..loxone.client import NotConnected

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
    origin: str = ""
    pending: Pending | None = None
    offer: Pending | None = None
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
        "description": (
            "Musik über den Loxone Audioserver, auch „Radio“, „Lautsprecher“ oder „Musik“ genannt. "
            "areas nimmt Bereiche oder Audio-Zonen aus Loxone (Name oder Raum). Ohne Angabe gilt der Bereich, in dem gesprochen wurde. "
            "Für „spiel Jazz“, einen Künstlernamen oder eine Playlist action=search und value=nur der Suchbegriff, "
            "z. B. Jazz. Das startet die passende Spotify-Playlist in der Zone. "
            "Gespeicherte Favoriten bleiben action=favorite."
        ),
        "parameters_json_schema": _schema(
            {
                "action": {
                    "type": "string",
                    "enum": ["play", "pause", "next", "previous", "volume", "louder", "quieter", "favorite", "search"],
                },
                "value": {"type": "string", "description": "Lautstärke 0-100, Favoritenname oder Suchbegriff wie Jazz"},
                "areas": {"type": "array", "items": {"type": "string"}, "description": "Namen der Bereiche"},
            },
            ["action"],
        ),
    },
    {
        "name": "announce",
        "description": "Durchsage in einem, mehreren oder allen Bereichen oder Audio-Zonen (areas: [\"alle\"]).",
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

_OFFER_ACTION = _schema(
    {
        "device": {"type": "string", "description": "Kennung aus der Geräteliste"},
        "action": {"type": "string", "enum": ALL_ACTIONS},
        "value": {"type": "string"},
        "value2": {"type": "number"},
    },
    ["device", "action"],
)
OFFER_PROPERTY = {
    "type": "object",
    "description": (
        "Optionaler Vorschlag für eine naheliegende Folgeaktion. Wird erst nach einem Ja ausgeführt. "
        "question ist genau eine kurze Frage im Jarvis-Stil."
    ),
    "properties": {
        "question": {"type": "string", "description": "z. B. Soll ich außerdem die Beschattung im Wohnzimmer herunterfahren?"},
        "actions": {"type": "array", "items": _OFFER_ACTION},
    },
    "required": ["question", "actions"],
}

# Action tools carry the spoken confirmation, so a fully successful call needs no second model turn.
QUICK_TOOLS = ("control", "music", "announce", "timer", "remember", "rename")
REPLY_PROPERTY = {
    "type": "string",
    "description": "Kurze gesprochene Bestätigung für den Erfolgsfall, ein Satz im Jarvis-Stil, ohne Kennungen.",
}
for _declaration in DECLARATIONS:
    if _declaration["name"] in QUICK_TOOLS:
        _declaration["parameters_json_schema"]["properties"]["reply"] = REPLY_PROPERTY
    if _declaration["name"] in ("control", "music"):
        _declaration["parameters_json_schema"]["properties"]["offer"] = OFFER_PROPERTY


def settled(name: str, args: dict, result: dict) -> bool:
    """True when an action tool fully succeeded and its reply can be spoken as is."""
    if name not in QUICK_TOOLS or "error" in result:
        return False
    if name == "timer" and args.get("action") != "set":
        return False
    return not any(item.get("error") or item.get("status") for item in result.get("results") or [])


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
            result = await handler(args or {}, turn)
            if args and args.get("offer") and settled(name, args, result):
                turn.offer = self.offer(args["offer"], turn)
            return result
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

    def offer(self, raw: Any, turn: Turn) -> Pending | None:
        """A suggested follow-up the speaker can accept with a plain yes. Only allowed, non-critical devices."""
        house = self.house
        if house.settings.get("brain.suggestions") == "off" or not isinstance(raw, dict):
            return None
        question = str(raw.get("question") or "").strip()
        actions: list[tuple[Entry, str, Any, Any]] = []
        for item in raw.get("actions") or []:
            if not isinstance(item, dict):
                continue
            entry = house.catalog.get(str(item.get("device", "")).strip().upper())
            if entry is None:
                continue
            action, _ = house.catalog.normalize(entry, str(item.get("action", "")), item.get("value"), house.miniserver.state)
            if not entry.allowed or entry.critical or action not in ACTIONS.get(entry.type, ()):
                continue
            if house.coordinator.declined(turn.area.id, entry.uuid, action):
                continue
            actions.append((entry, action, item.get("value"), item.get("value2")))
        if not question or not actions:
            return None
        return Pending("offer", question, actions, lang=turn.lang)

    async def execute(self, entry: Entry, action: str, value: Any, value2: Any, turn: Turn | None) -> dict:
        house = self.house
        state = house.miniserver.state
        commands = house.catalog.commands(entry, action, value, value2, state)
        action, value = house.catalog.normalize(entry, action, value, state)
        for uuid, cmd in commands:
            reply = await self._send(uuid, cmd)
            if not reply.ok:
                return {"device": entry.short_id, "error": f"Miniserver meldet Code {reply.code}"}
        label = f"{entry.name}: {action}" + (f" {value}" if value not in (None, "") else "")
        if turn is not None:
            turn.done.append(label)
        house.events.log("action", label, entry.room or "")
        return {"device": entry.short_id, "ok": True}

    async def _send(self, uuid: str, cmd: str):
        """One retry when the Miniserver connection was just lost and comes straight back."""
        miniserver = self.house.miniserver
        try:
            return await miniserver.command(uuid, cmd)
        except NotConnected:
            for _ in range(10):
                await asyncio.sleep(0.2)
                if miniserver.connected:
                    return await miniserver.command(uuid, cmd)
            raise ActionError("Miniserver gerade nicht verbunden") from None

    # ---------------------------------------------------------------- music
    def _target_areas(self, names: list[str] | None, turn: Turn) -> list[Area]:
        """Bereiche or Loxone audio zones by name; a zone without a Bereich still plays."""
        if not names:
            return [turn.area]
        if any(str(n).strip().lower() in ("alle", "all", "überall", "everywhere", "haus") for n in names):
            return self._all_zone_targets()
        found: list[Area] = []
        for name in names:
            area = self.house.areas.find(str(name))
            if area is None or not area.zone_uuid:
                zone = find_zone(self.house.miniserver.structure.zones(), str(name))
                if zone is not None:
                    area = self._zone_target(zone)
            if area is None:
                known = ", ".join(z.name for z in self.house.miniserver.structure.zones()) or "keine"
                raise ActionError(f"'{name}' gibt es weder als Bereich noch als Audio-Zone. Audio-Zonen: {known}")
            duplicate = any(a.id == area.id or (area.zone_uuid and a.zone_uuid == area.zone_uuid) for a in found)
            if not duplicate:
                found.append(area)
        return found

    def _zone_target(self, zone) -> Area:
        for area in self.house.areas.list():
            if area.zone_uuid == zone.uuid:
                return area
        return Area(f"zone:{zone.uuid}", zone.name, zone.uuid, None, [zone.room] if zone.room else [])

    def _all_zone_targets(self) -> list[Area]:
        return [self._zone_target(zone) for zone in self.house.miniserver.structure.zones()]

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
            elif action == "search":
                title = await house.play_query(zone, str(value or ""))
                turn.done.append(f"Musik {title} {area.name}")
                done.append({"area": area.name, "ok": True, "title": title})
                continue
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
            origin=turn.origin,
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


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", (text or "").lower())


def find_zone(zones: list, name: str):
    """An audio zone by its name or its room, exact first, then contained either way."""
    wanted = _key(re.sub(r"^(im|in der|in dem|in|am|auf der|auf dem)\s+", "", name.strip().lower()))
    if not wanted:
        return None
    for zone in zones:
        if wanted in (_key(zone.name), _key(zone.room or "")):
            return zone
    for zone in zones:
        names = [_key(zone.name), _key(zone.room or "")]
        if any(n and len(wanted) >= 3 and (wanted in n or n in wanted) for n in names):
            return zone
    return None
