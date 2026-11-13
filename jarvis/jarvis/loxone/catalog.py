"""The allowlist: which Loxone controls Jarvis may switch, under which names.

Gemini only ever sees short ids like L3 and the speech names. UUIDs stay
server-side. Every action is validated here before a command is built.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import Database, dumps, loads
from .structure import LoxControl, Structure

StateGetter = Callable[[str, str], Any]

LIGHT_TYPES = {"LightControllerV2", "LightsceneRGB", "ColorPickerV2", "Dimmer", "EIBDimmer"}
SWITCH_TYPES = {"Switch", "Pushbutton", "TimedSwitch"}
SHADING_TYPES = {"Jalousie", "CentralJalousie"}
CLIMATE_TYPES = {"IRoomControllerV2"}
GATE_TYPES = {"Gate", "CentralGate"}
ALARM_TYPES = {"Alarm"}
OTHER_TYPES = {"Radio", "Slider", "ValueSelector", "UpDownAnalog", "UpDownDigital"}
WINDOW_TYPES = {"Window"}
# Read-only blocks: Jarvis can answer questions about them but never switches anything.
SENSOR_TYPES = {"InfoOnlyAnalog", "InfoOnlyDigital", "InfoOnlyText", "TextState", "Meter", "PresenceDetector", "WindowMonitor"}
SUPPORTED = (
    LIGHT_TYPES | SWITCH_TYPES | SHADING_TYPES | CLIMATE_TYPES | GATE_TYPES | ALARM_TYPES | OTHER_TYPES
    | WINDOW_TYPES | SENSOR_TYPES
)

ACTIONS: dict[str, tuple[str, ...]] = {
    "Switch": ("on", "off"),
    "Pushbutton": ("on", "off", "press"),
    "TimedSwitch": ("on", "off"),
    "Dimmer": ("on", "off", "dim"),
    "EIBDimmer": ("on", "off", "dim"),
    "LightControllerV2": ("on", "off", "dim", "scene"),
    "ColorPickerV2": ("on", "off", "dim", "color", "white"),
    "LightsceneRGB": ("on", "off", "scene"),
    "Jalousie": ("up", "down", "stop", "shade", "position", "slat"),
    "CentralJalousie": ("up", "down", "stop", "shade"),
    "Gate": ("open", "close", "stop"),
    "CentralGate": ("open", "close", "stop"),
    "IRoomControllerV2": ("temperature", "mode"),
    "Alarm": ("arm", "disarm", "quit"),
    "Radio": ("select", "off"),
    "Slider": ("set",),
    "ValueSelector": ("set",),
    "UpDownAnalog": ("set",),
    "UpDownDigital": ("up", "down"),
    "Window": ("open", "close", "stop", "position"),
    **{t: () for t in SENSOR_TYPES},
}

# Words Gemini (or a person) may use for an action, per block family. Mapped before validation.
_COMMON_ALIASES = {
    "an": "on", "ein": "on", "einschalten": "on", "anschalten": "on", "turn_on": "on", "switch_on": "on", "activate": "on",
    "aus": "off", "ausschalten": "off", "turn_off": "off", "switch_off": "off", "deactivate": "off",
    "halt": "stop", "anhalten": "stop", "stopp": "stop",
}
_TYPE_ALIASES: dict[str, dict[str, str]] = {
    "shading": {
        "open": "up", "raise": "up", "hoch": "up", "rauf": "up", "auf": "up", "fullup": "up", "öffnen": "up",
        "close": "down", "lower": "down", "runter": "down", "zu": "down", "fulldown": "down", "schließen": "down",
        "set": "position", "move": "position", "lamelle": "slat", "lamellen": "slat", "tilt": "slat", "beschatten": "shade",
    },
    "window": {"auf": "open", "öffnen": "open", "up": "open", "zu": "close", "schließen": "close", "down": "close", "set": "position", "move": "position"},
    "gate": {"up": "open", "auf": "open", "on": "open", "down": "close", "zu": "close", "off": "close"},
    "dimmable": {"set": "dim", "brightness": "dim", "level": "dim", "value": "dim", "helligkeit": "dim", "position": "dim"},
    "color": {"hue": "color", "farbe": "color", "temperature": "white", "kelvin": "white", "weiß": "white", "white_temp": "white"},
    "climate": {"set": "temperature", "temp": "temperature", "setpoint": "temperature", "heat": "temperature", "betriebsart": "mode"},
    "value": {"dim": "set", "position": "set", "value": "set", "level": "set"},
    "alarm": {"on": "arm", "activate": "arm", "scharf": "arm", "off": "disarm", "unscharf": "disarm", "acknowledge": "quit"},
    "radio": {"set": "select", "on": "select", "choose": "select"},
    "updown": {"open": "up", "raise": "up", "close": "down", "lower": "down"},
}

ALL_ACTIONS = sorted({a for actions in ACTIONS.values() for a in actions})

GROUP_PREFIX = {"light": "L", "climate": "K", "shading": "B", "gate": "T", "alarm": "A", "window": "F", "sensor": "S", "other": "X"}
GROUP_LABEL = {
    "light": "Licht",
    "climate": "Klima",
    "shading": "Beschattung",
    "gate": "Tor",
    "alarm": "Alarm",
    "window": "Fenster",
    "sensor": "Sensor",
    "other": "Sonstiges",
}

_LIGHT_WORDS = re.compile(r"licht|light|lampe|leuchte|beleuchtung|spot|led|strahler|lamp", re.I)
_CRITICAL_WORDS = re.compile(r"schloss|lock|garage|\btor\b|garagentor|alarm", re.I)

LIGHT_KINDS = ("auto", "direct", "indirect")
_INDIRECT = re.compile(
    r"indirekt|indirect|\bled\b|led[- ]?streifen|streifen|stripe?|ambient|ambiente|akzent|accent|sockel|voute|"
    r"hinterleucht|backlight|schrank|nische|kontur|cove|deko",
    re.I,
)
_DIRECT = re.compile(r"\bdirekt|direct|decke|ceiling|spot|pendel|pendant|haupt|main|einbau|downlight|strahler", re.I)


def detect_light_kind(control: LoxControl | None, name: str, aliases: list[str]) -> str:
    """Direct or indirect light from what Loxone holds for the block, then from its names."""
    if control is not None:
        for key, value in (control.details or {}).items():
            text = f"{key} {value}" if isinstance(value, (str, int, float, bool)) else str(key)
            if _INDIRECT.search(text) and not re.search(r"\bdirect\b", text, re.I):
                return "indirect"
            if re.search(r"\bdirect\b|\bdirekt\b", text, re.I):
                return "direct"
    words = " ".join([name, *aliases])
    if _INDIRECT.search(words):
        return "indirect"
    if _DIRECT.search(words):
        return "direct"
    return "unknown"
_DOOR_OPENER = re.compile(r"t(ü|ue)r\s*(ö|oe)ffn|door\s*open|summer|buzzer|t(ü|ue)r\s*auf", re.I)

MOOD_OFF = 778
MOOD_BRIGHT = 777

ROOM_MODES = {0: "Automatik", 1: "Automatik Heizen", 2: "Automatik Kühlen", 3: "Manuell", 4: "Manuell Heizen", 5: "Manuell Kühlen"}

# Usage hints per block type for the device list Gemini reads.
TYPE_HINTS = {
    "Dimmer": "dim 0-100 %",
    "EIBDimmer": "dim 0-100 %",
    "LightControllerV2": "dim 0-100 % für alle Kreise, scene = Stimmung",
    "ColorPickerV2": "color = Farbton 0-360 (0 rot, 30 orange, 60 gelb, 120 grün, 180 türkis, 240 blau, 300 lila), white = 2700-6500 K, value2 = Helligkeit",
    "TimedSwitch": "on startet den Nachlauf",
    "Pushbutton": "press = Tastendruck",
    "Jalousie": "position 0 = ganz offen, 100 = ganz zu; slat = Lamellen 0 offen bis 100 zu; shade = Beschattungsposition",
    "CentralJalousie": "wirkt auf mehrere Rollos zugleich",
    "Window": "position 0 = zu, 100 = ganz offen",
    "Gate": "open, close, stop",
    "CentralGate": "wirkt auf mehrere Tore",
    "IRoomControllerV2": "temperature = Komfort-Solltemperatur 5-30 °C; mode 0 Automatik, 1 Auto Heizen, 2 Auto Kühlen, 3 Manuell, 4 Manuell Heizen, 5 Manuell Kühlen",
    "Alarm": "arm, disarm, quit",
    "UpDownDigital": "up/down als kurzer Impuls",
    **{t: "nur zum Ablesen" for t in SENSOR_TYPES},
}


class ActionError(ValueError):
    pass


def group_of(control: LoxControl) -> str | None:
    t = control.type
    if t not in SUPPORTED:
        return None
    if t in SHADING_TYPES:
        return "shading"
    if t in CLIMATE_TYPES:
        return "climate"
    if t in GATE_TYPES:
        return "gate"
    if t in ALARM_TYPES:
        return "alarm"
    if t in WINDOW_TYPES:
        return "window"
    if t in SENSOR_TYPES:
        return "sensor"
    if t in LIGHT_TYPES:
        return "light"
    if t in SWITCH_TYPES:
        if (control.cat_type or "").lower() == "lights":
            return "light"
        if _LIGHT_WORDS.search(control.name) or _LIGHT_WORDS.search(control.category or ""):
            return "light"
        return "other"
    return "other"


def is_door_opener(control: LoxControl) -> bool:
    return bool(_DOOR_OPENER.search(control.name))


def default_critical(control: LoxControl, group: str) -> bool:
    if group in ("gate", "alarm"):
        return True
    return bool(_CRITICAL_WORDS.search(control.name))


@dataclass
class Entry:
    uuid: str
    short_id: str
    name: str
    type: str
    group: str
    room: str | None
    category: str | None
    parent: str | None
    allowed: bool
    critical: bool
    secured: bool
    aliases: list[str] = field(default_factory=list)
    light_kind: str = "auto"
    kind: str = "unknown"

    def public(self) -> dict:
        return {
            "id": self.short_id,
            "name": self.name,
            "type": self.type,
            "group": self.group,
            "room": self.room,
            "category": self.category,
            "allowed": self.allowed,
            "critical": self.critical,
            "secured": self.secured,
            "aliases": self.aliases,
            "actions": list(ACTIONS.get(self.type, ())),
            "child": self.parent is not None,
            "light_kind": self.light_kind,
            "kind": self.kind,
        }


class Catalog:
    def __init__(self, db: Database):
        self._db = db
        self.structure = Structure()
        self._secured: set[str] = set()

    # ------------------------------------------------------------------ sync
    def sync(self, structure: Structure) -> None:
        self.structure = structure
        existing = {r["uuid"]: r for r in self._db.all("SELECT * FROM controls")}
        used = {r["short_id"] for r in existing.values()}
        counters: dict[str, int] = {}
        for short in used:
            prefix, number = short[0], short[1:]
            if number.isdigit():
                counters[prefix] = max(counters.get(prefix, 0), int(number))
        seen: set[str] = set()
        self._secured = set()
        for control in structure.controls.values():
            if control.uuid.endswith(("/masterValue", "/masterColor")):
                continue
            group = group_of(control)
            if group is None or is_door_opener(control):
                continue
            seen.add(control.uuid)
            if control.secured:
                self._secured.add(control.uuid)
            row = existing.get(control.uuid)
            if row is None:
                prefix = GROUP_PREFIX[group]
                counters[prefix] = counters.get(prefix, 0) + 1
                short = f"{prefix}{counters[prefix]}"
                while short in used:
                    counters[prefix] += 1
                    short = f"{prefix}{counters[prefix]}"
                used.add(short)
                self._db.execute(
                    "INSERT INTO controls(uuid, short_id, name, type, room, category, parent, allowed, critical, aliases, present) "
                    "VALUES(?,?,?,?,?,?,?,?,?,'[]',1)",
                    (
                        control.uuid,
                        short,
                        control.name,
                        control.type,
                        control.room,
                        control.category,
                        control.parent,
                        int(group == "sensor"),
                        int(default_critical(control, group)),
                    ),
                )
            else:
                self._db.execute(
                    "UPDATE controls SET name=?, type=?, room=?, category=?, parent=?, present=1 WHERE uuid=?",
                    (control.name, control.type, control.room, control.category, control.parent, control.uuid),
                )
        for uuid in set(existing) - seen:
            self._db.execute("UPDATE controls SET present=0, allowed=0 WHERE uuid=?", (uuid,))
        # A secured block needs the visualisation password, which Jarvis never holds.
        for uuid in self._secured:
            self._db.execute("UPDATE controls SET allowed=0 WHERE uuid=?", (uuid,))

    # --------------------------------------------------------------- queries
    def _entry(self, row: dict) -> Entry:
        control = self.structure.controls.get(row["uuid"])
        group = group_of(control) if control else None
        aliases = loads(row["aliases"], [])
        override = row.get("light_kind") or "auto"
        kind = "unknown"
        if group == "light":
            kind = override if override in ("direct", "indirect") else detect_light_kind(control, row["name"], aliases)
        return Entry(
            uuid=row["uuid"],
            short_id=row["short_id"],
            name=row["name"],
            type=row["type"],
            group=group or "other",
            room=row["room"],
            category=row["category"],
            parent=row["parent"],
            allowed=bool(row["allowed"]),
            critical=bool(row["critical"]),
            secured=row["uuid"] in self._secured,
            aliases=aliases,
            light_kind=override or "auto",
            kind=kind,
        )

    def entries(self, only_allowed: bool = False) -> list[Entry]:
        sql = "SELECT * FROM controls WHERE present=1"
        if only_allowed:
            sql += " AND allowed=1"
        rows = self._db.all(sql + " ORDER BY room, name")
        return [self._entry(r) for r in rows if r["uuid"] in self.structure.controls]

    def get(self, short_id: str) -> Entry | None:
        row = self._db.one("SELECT * FROM controls WHERE short_id = ? AND present=1", (short_id.strip().upper(),))
        if not row or row["uuid"] not in self.structure.controls:
            return None
        return self._entry(row)

    def by_uuid(self, uuid: str) -> Entry | None:
        row = self._db.one("SELECT * FROM controls WHERE uuid = ?", (uuid,))
        return self._entry(row) if row else None

    def update(
        self,
        short_id: str,
        *,
        allowed: bool | None = None,
        critical: bool | None = None,
        aliases: list[str] | None = None,
        light_kind: str | None = None,
    ) -> Entry:
        entry = self.get(short_id)
        if entry is None:
            raise KeyError(short_id)
        if allowed is not None:
            if allowed and entry.secured:
                raise ActionError("Dieser Baustein verlangt das Visualisierungspasswort und bleibt gesperrt.")
            self._db.execute("UPDATE controls SET allowed=? WHERE uuid=?", (int(allowed), entry.uuid))
        if critical is not None:
            if not critical and entry.group in ("gate", "alarm"):
                raise ActionError("Tor und Alarm bleiben hinter der gesprochenen Bestätigung.")
            self._db.execute("UPDATE controls SET critical=? WHERE uuid=?", (int(critical), entry.uuid))
        if aliases is not None:
            clean = []
            for alias in aliases:
                alias = str(alias).strip()
                if alias and alias.lower() not in (a.lower() for a in clean) and len(alias) <= 60:
                    clean.append(alias)
            self._db.execute("UPDATE controls SET aliases=? WHERE uuid=?", (dumps(clean[:20]), entry.uuid))
        if light_kind is not None:
            if light_kind not in LIGHT_KINDS:
                raise ActionError("Lichtart ist auto, direct oder indirect")
            self._db.execute("UPDATE controls SET light_kind=? WHERE uuid=?", (light_kind, entry.uuid))
        return self.get(short_id)  # type: ignore[return-value]

    def children(self, entry: Entry, only_allowed: bool = True) -> list[Entry]:
        return [e for e in self.entries(only_allowed=only_allowed) if e.parent == entry.uuid]

    def add_alias(self, short_id: str, alias: str) -> Entry:
        entry = self.get(short_id)
        if entry is None:
            raise KeyError(short_id)
        return self.update(short_id, aliases=entry.aliases + [alias])

    def rooms(self) -> list[str]:
        return sorted({r for r in self.structure.rooms.values() if r})

    def zones(self) -> list[dict]:
        return [
            {"uuid": z.uuid, "name": z.name, "room": z.room, "player_id": z.details.get("playerid")}
            for z in self.structure.zones()
        ]

    def moods(self, uuid: str, state: StateGetter) -> list[dict]:
        raw = state(uuid, "moodList")
        if not raw:
            return []
        try:
            moods = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return []
        return [m for m in moods if isinstance(m, dict) and "id" in m]

    def counts(self, state: StateGetter) -> dict[str, int]:
        entries = self.entries()
        scenes = 0
        for entry in entries:
            if entry.type == "LightControllerV2":
                scenes += len([m for m in self.moods(entry.uuid, state) if m.get("id") != MOOD_OFF])
        return {
            "light": sum(1 for e in entries if e.group == "light"),
            "climate": sum(1 for e in entries if e.group == "climate"),
            "shading": sum(1 for e in entries if e.group == "shading"),
            "scenes": scenes,
            "allowed": sum(1 for e in entries if e.allowed),
            "rooms": len(self.rooms()),
        }

    # ------------------------------------------------------------- describe
    def describe_state(self, entry: Entry, state: StateGetter) -> str:
        t = entry.type
        get = lambda name: state(entry.uuid, name)  # noqa: E731
        try:
            if t in ("Switch", "Pushbutton"):
                return "an" if (get("active") or 0) > 0 else "aus"
            if t == "TimedSwitch":
                return "an" if (get("deactivationDelay") or 0) != 0 else "aus"
            if t in ("Dimmer", "EIBDimmer"):
                pos = get("position")
                return "aus" if not pos else f"{pos:.0f} %"
            if t == "LightControllerV2":
                active = get("activeMoods")
                ids = json.loads(active) if isinstance(active, str) and active else []
                if not ids or ids == [MOOD_OFF]:
                    return "aus"
                names = {m["id"]: m.get("name", "") for m in self.moods(entry.uuid, state)}
                return "Szene " + ", ".join(names.get(i, str(i)) for i in ids)
            if t == "ColorPickerV2":
                return _describe_color(get("color"))
            if t == "Jalousie":
                pos = get("position")
                if pos is None:
                    return ""
                slat = get("shadePosition")
                text = "ganz offen" if pos <= 0.01 else "ganz zu" if pos >= 0.99 else f"{pos * 100:.0f} % geschlossen"
                return text + (f", Lamellen {slat * 100:.0f} %" if slat is not None else "")
            if t == "Window":
                pos = get("position")
                if pos is None:
                    return ""
                return "zu" if pos <= 0.01 else "ganz offen" if pos >= 0.99 else f"{pos * 100:.0f} % offen"
            if t == "InfoOnlyAnalog":
                value = get("value")
                return "" if value is None else _format(self._details(entry).get("format"), value)
            if t == "InfoOnlyDigital":
                active = bool(get("active"))
                texts = self._details(entry).get("text") or {}
                return str(texts.get("on" if active else "off") or ("an" if active else "aus"))
            if t in ("InfoOnlyText", "TextState"):
                return _plain_text(get("textAndIcon") or get("text"))
            if t == "Meter":
                d = self._details(entry)
                actual, total = get("actual"), get("total")
                parts = []
                if actual is not None:
                    parts.append("aktuell " + _format(d.get("actualFormat"), actual))
                if total is not None:
                    parts.append("gesamt " + _format(d.get("totalFormat"), total))
                return ", ".join(parts)
            if t == "PresenceDetector":
                return "Anwesenheit erkannt" if get("active") else "niemand erkannt"
            if t == "WindowMonitor":
                counts = [(get("numOpen"), "offen"), (get("numTilted"), "gekippt"), (get("numClosed"), "zu")]
                return ", ".join(f"{int(n)} {label}" for n, label in counts if n is not None)
            if t == "Gate":
                pos = get("position")
                if pos is None:
                    return ""
                return "offen" if pos >= 0.99 else "geschlossen" if pos <= 0.01 else f"{pos * 100:.0f} % offen"
            if t == "IRoomControllerV2":
                actual, target = get("tempActual"), get("tempTarget")
                if actual is None:
                    return ""
                text = f"ist {actual:.1f} °C, Soll {target:.1f} °C" if target is not None else f"ist {actual:.1f} °C"
                mode = get("operatingMode")
                return text + (f", {ROOM_MODES.get(int(mode), '')}" if mode is not None and int(mode) in ROOM_MODES else "")
            if t == "Alarm":
                return "scharf" if (get("armed") or 0) > 0 else "unscharf"
            if t == "Radio":
                active = int(get("activeOutput") or 0)
                outputs = self._details(entry).get("outputs") or {}
                return outputs.get(str(active), "aus" if active == 0 else str(active))
            if t in ("Slider", "ValueSelector", "UpDownAnalog"):
                value = get("value")
                return "" if value is None else _format(self._details(entry).get("format"), value)
        except (TypeError, ValueError, KeyError):
            return ""
        return ""

    def _details(self, entry: Entry) -> dict:
        control = self.structure.controls.get(entry.uuid)
        return control.details if control else {}

    def extras(self, entry: Entry, state: StateGetter) -> str:
        t = entry.type
        hint = TYPE_HINTS.get(t, "")
        more = self._extras(entry, state)
        return "; ".join(x for x in (more, hint) if x)

    def _extras(self, entry: Entry, state: StateGetter) -> str:
        if entry.type == "LightControllerV2":
            moods = [m.get("name", "") for m in self.moods(entry.uuid, state) if m.get("id") != MOOD_OFF]
            return "Szenen: " + ", ".join(m for m in moods if m) if moods else ""
        if entry.type == "LightsceneRGB":
            scenes = self._details(entry).get("sceneList") or {}
            return "Szenen: " + ", ".join(f"{k}={v}" for k, v in scenes.items()) if scenes else ""
        if entry.type == "Radio":
            outputs = self._details(entry).get("outputs") or {}
            return "Auswahl: " + ", ".join(f"{k}={v}" for k, v in outputs.items()) if outputs else ""
        if entry.type in ("Slider", "ValueSelector", "UpDownAnalog"):
            d = self._details(entry)
            if "min" in d and "max" in d:
                unit = _unit(d.get("format"))
                return f"Wert {d['min']:g}..{d['max']:g}{' ' + unit if unit else ''}"
        return ""

    def is_on(self, entry: Entry, state: StateGetter) -> bool:
        text = self.describe_state(entry, state)
        return bool(text) and not re.match(r"^(aus|zu|ganz offen|unscharf|geschlossen|niemand)", text)

    def normalize(self, entry: Entry, action: str, value: Any, state: StateGetter) -> tuple[str, Any]:
        """Map the many ways to say an action onto what this block understands."""
        t = entry.type
        allowed = ACTIONS.get(t, ())
        action = str(action or "").strip().lower().replace(" ", "_")
        if action in allowed:
            pass
        else:
            action = _COMMON_ALIASES.get(action, action)
            family = _family(t)
            for name in family:
                action = _TYPE_ALIASES.get(name, {}).get(action, action)
        if t == "LightControllerV2" and action in ("mood", "stimmung", "szene"):
            action = "scene"
        if action == "toggle":
            action = "off" if self.is_on(entry, state) else "on"
            action = _TYPE_ALIASES.get("gate", {}).get(action, action) if t in GATE_TYPES else action
        number = _number(value)
        if action == "dim" and number == 0 and "off" in allowed:
            action = "off"
        elif action == "on" and number is not None and 0 < number < 100 and "dim" in allowed:
            action = "dim"
        return action, value

    # -------------------------------------------------------------- commands
    def commands(self, entry: Entry, action: str, value: Any, value2: Any, state: StateGetter) -> list[tuple[str, str]]:
        allowed = ACTIONS.get(entry.type, ())
        if not allowed:
            raise ActionError(f"{entry.name} ist nur zum Ablesen da: {self.describe_state(entry, state) or 'kein Wert'}")
        action, value = self.normalize(entry, action, value, state)
        if action not in allowed:
            raise ActionError(f"{entry.name} kann '{action}' nicht. Möglich: {', '.join(allowed)}")
        t = entry.type
        uuid = entry.uuid

        def number(lo: float, hi: float, v: Any = value, what: str = "Wert") -> float:
            try:
                n = float(v)
            except (TypeError, ValueError) as exc:
                raise ActionError(f"{what} fehlt") from exc
            if math.isnan(n):
                raise ActionError(f"{what} fehlt")
            return max(lo, min(hi, n))

        if t == "Switch":
            return [(uuid, action)]
        if t == "Pushbutton":
            return [(uuid, "pulse" if action == "press" else action)]
        if t == "TimedSwitch":
            return [(uuid, "pulse" if action == "on" else "off")]
        if t in ("Dimmer", "EIBDimmer"):
            if action == "dim":
                return [(uuid, f"{number(0, 100):.0f}")]
            return [(uuid, "on" if action == "on" else "off")] if t == "Dimmer" else [(uuid, "On" if action == "on" else "Off")]
        if t == "LightControllerV2":
            moods = self.moods(uuid, state)
            ids = {m["id"] for m in moods}
            if action == "on":
                return [(uuid, f"changeTo/{MOOD_BRIGHT}" if MOOD_BRIGHT in ids else "plus")]
            if action == "off":
                return [(uuid, f"changeTo/{MOOD_OFF}" if MOOD_OFF in ids or not ids else "changeTo/0")]
            if action == "dim":
                master = self.structure.child(uuid, "/masterValue")
                if master is None:
                    raise ActionError("Dieser Lichtbaustein hat keinen Gesamtdimmer.")
                return [(master.uuid, f"{number(0, 100):.0f}")]
            mood = _find_mood(moods, value)
            if mood is None:
                names = ", ".join(m.get("name", "") for m in moods if m.get("id") != MOOD_OFF)
                raise ActionError(f"Szene nicht gefunden. Vorhanden: {names}")
            return [(uuid, f"changeTo/{mood['id']}")]
        if t == "ColorPickerV2":
            h, s, v = _hsv(state(uuid, "color"))
            if action == "on":
                return [(uuid, f"hsv({h},{s},100)")]
            if action == "off":
                return [(uuid, f"hsv({h},{s},0)")]
            if action == "dim":
                return [(uuid, f"hsv({h},{s},{number(0, 100):.0f})")]
            if action == "color":
                hue = number(0, 360, value, "Farbton")
                bri = number(1, 100, value2 if value2 is not None else (v or 100), "Helligkeit")
                return [(uuid, f"hsv({hue:.0f},100,{bri:.0f})")]
            kelvin = number(2700, 6500, value, "Farbtemperatur")
            bri = number(1, 100, value2 if value2 is not None else (v or 100), "Helligkeit")
            return [(uuid, f"temp({bri:.0f},{kelvin:.0f})")]
        if t == "LightsceneRGB":
            if action in ("on", "off"):
                return [(uuid, action)]
            return [(uuid, f"{number(0, 99):.0f}")]
        if t in ("Jalousie", "CentralJalousie"):
            mapping = {"up": "FullUp", "down": "FullDown", "stop": "stop", "shade": "shade"}
            if action == "position":
                return [(uuid, f"manualPosition/{number(0, 100):.0f}")]
            if action == "slat":
                return [(uuid, f"manualLamelle/{number(0, 100):.0f}")]
            return [(uuid, mapping[action])]
        if t == "Window":
            if action == "position":
                return [(uuid, f"moveToPosition/{number(0, 100):.0f}")]
            return [(uuid, {"open": "fullopen", "close": "fullclose", "stop": "stop"}[action])]
        if t in GATE_TYPES:
            return [(uuid, action)]
        if t == "IRoomControllerV2":
            if action == "temperature":
                return [(uuid, f"setComfortTemperature/{number(5, 30, what='Temperatur'):.1f}")]
            return [(uuid, f"setOperatingMode/{number(0, 5):.0f}")]
        if t == "Alarm":
            return [(uuid, {"arm": "on/1", "disarm": "off", "quit": "quit"}[action])]
        if t == "Radio":
            if action == "off":
                return [(uuid, "reset")]
            outputs = self._details(entry).get("outputs") or {}
            if isinstance(value, str) and not value.strip().isdigit():
                for key, name in outputs.items():
                    if name.lower() == value.strip().lower():
                        return [(uuid, str(key))]
                raise ActionError("Auswahl nicht gefunden")
            return [(uuid, f"{number(1, 16):.0f}")]
        if t in ("Slider", "ValueSelector", "UpDownAnalog"):
            d = self._details(entry)
            lo = float(d.get("min", -1e9)) if d.get("min") is not None else -1e9
            hi = float(d.get("max", 1e9)) if d.get("max") is not None else 1e9
            return [(uuid, f"{number(lo, hi):g}")]
        if t == "UpDownDigital":
            return [(uuid, "PulseUp" if action == "up" else "PulseDown")]
        raise ActionError("nicht unterstützt")


def _find_mood(moods: list[dict], value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        wanted = int(float(value))
        return next((m for m in moods if m.get("id") == wanted), None)
    text = str(value).strip().lower()
    for mood in moods:
        if str(mood.get("name", "")).lower() == text:
            return mood
    for mood in moods:
        name = str(mood.get("name", "")).lower()
        if text in name or name in text:
            return mood
    return None


def _hsv(color: Any) -> tuple[int, int, int]:
    if isinstance(color, str):
        match = re.match(r"hsv\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)", color)
        if match:
            h, s, v = (int(float(x)) for x in match.groups())
            return h, s, v
    return 0, 0, 100


def _family(t: str) -> tuple[str, ...]:
    if t in SHADING_TYPES:
        return ("shading",)
    if t in WINDOW_TYPES:
        return ("window",)
    if t in GATE_TYPES:
        return ("gate",)
    if t == "ColorPickerV2":
        return ("color", "dimmable")
    if t in ("Dimmer", "EIBDimmer", "LightControllerV2", "LightsceneRGB"):
        return ("dimmable",)
    if t in CLIMATE_TYPES:
        return ("climate",)
    if t in ("Slider", "ValueSelector", "UpDownAnalog"):
        return ("value",)
    if t in ALARM_TYPES:
        return ("alarm",)
    if t == "Radio":
        return ("radio",)
    if t == "UpDownDigital":
        return ("updown",)
    return ()


def _number(value: Any) -> float | None:
    try:
        n = float(str(value).replace(",", ".").rstrip("%").strip())
    except (TypeError, ValueError):
        return None
    return None if math.isnan(n) else n


_PRINTF = re.compile(r"%(\.(\d+))?[fdi]")


def _format(fmt: Any, value: Any) -> str:
    """Apply a Loxone printf format like '%.1f°C' or '%.0f%%'."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not isinstance(fmt, str) or "%" not in fmt:
        return f"{number:g}"
    match = _PRINTF.search(fmt)
    if not match:
        return f"{number:g} {fmt.replace('%%', '%')}".strip()
    digits = int(match.group(2)) if match.group(2) else (0 if fmt[match.end() - 1] in "di" else 2)
    rendered = f"{number:.{digits}f}"
    return (fmt[: match.start()] + rendered + fmt[match.end():]).replace("%%", "%").strip()


def _unit(fmt: Any) -> str:
    if not isinstance(fmt, str):
        return ""
    return _PRINTF.sub("", fmt).replace("%%", "%").strip()


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return value[:120]
        if isinstance(data, dict):
            return str(data.get("text") or "")[:120]
    return "" if value is None else str(value)[:120]


def _describe_color(color: Any) -> str:
    if not isinstance(color, str):
        return ""
    temp = re.match(r"temp\(\s*([\d.]+)\s*,\s*([\d.]+)\s*\)", color)
    if temp:
        bri, kelvin = float(temp.group(1)), float(temp.group(2))
        return "aus" if bri <= 0 else f"weiß {kelvin:.0f} K, {bri:.0f} %"
    h, s, v = _hsv(color)
    if v <= 0:
        return "aus"
    names = [(15, "rot"), (45, "orange"), (75, "gelb"), (165, "grün"), (195, "türkis"), (265, "blau"), (330, "lila"), (360, "rot")]
    name = next(label for limit, label in names if h <= limit)
    return f"{name if s > 20 else 'weiß'}, {v} %"
