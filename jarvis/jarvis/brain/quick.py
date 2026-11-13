"""Instant handling of the most common sentences, without a round trip to Gemini.

Lights on, off or dimmed, blinds up or down, optionally in a named room, plus
time and date. Only whole sentences that match exactly are taken, everything
else goes to Gemini. A room that does not resolve to allowed devices also goes
to Gemini.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..loxone.catalog import ACTIONS

if TYPE_CHECKING:
    from ..areas import Area
    from ..house import House
    from ..loxone.catalog import Entry

_LIGHT = r"(?:das |die |den )?(?:licht|lichter|lampe|lampen|beleuchtung)"
_SHADE = r"(?:die |den |das )?(?:rollo|rollos|rollladen|rolladen|rollläden|jalousie|jalousien|raffstore|raffstores|beschattung)"
_PREP = r"(?P<prep>im|in der|in dem|in|auf der|auf dem|am|beim)"
_ROOM = r"(?P<room>[a-zäöüß][a-zäöüß0-9\- ]{1,40}?)"
_VERB = r"(?:(?:schalte|schalt|mach|mache)(?: mal)? )?(?:bitte )?"
_MOVE = r"(?:(?:fahr|fahre|mach|mache)(?: mal)? )?(?:bitte )?"
_DIMV = r"(?:(?:dimm|dimme|mach|mache|stell|stelle|schalte)(?: mal)? )?(?:bitte )?"
_STATE = r"(?P<state>an|ein|aus|anschalten|einschalten|ausschalten|anmachen|ausmachen)"
_SHADE_STATE = r"(?P<state>hoch|rauf|auf|öffnen|runter|herunter|zu|schließen)(?: fahren)?"
_VALUE = r"(?P<value>\d{1,3}) ?(?:prozent|%)?"
_END = r"(?: bitte)?"

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("light", re.compile(rf"^{_VERB}{_LIGHT}(?: {_PREP} {_ROOM})? (?:bitte )?{_STATE}{_END}$")),
    ("light", re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_LIGHT} {_STATE}{_END}$")),
    ("dim", re.compile(rf"^{_DIMV}{_LIGHT}(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("dim", re.compile(rf"^{_DIMV}{_PREP} {_ROOM} {_LIGHT} auf {_VALUE}{_END}$")),
    ("shade", re.compile(rf"^{_MOVE}{_SHADE}(?: {_PREP} {_ROOM})? (?:bitte )?{_SHADE_STATE}{_END}$")),
    ("shade", re.compile(rf"^{_MOVE}{_PREP} {_ROOM} {_SHADE} {_SHADE_STATE}{_END}$")),
    ("light", re.compile(r"^(?:please )?turn (?P<state>on|off) (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))?(?: please)?$")),
    ("light", re.compile(r"^(?:please )?turn (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))? (?P<state>on|off)(?: please)?$")),
    ("time", re.compile(r"^(?:wie spät ist es|wie viel uhr ist es|wieviel uhr ist es|wie spät haben wir|was ist die uhrzeit|uhrzeit)(?: bitte)?$")),
    ("time", re.compile(r"^(?:what time is it|what's the time|what is the time)(?: please)?$")),
    ("date", re.compile(r"^(?:welcher tag ist heute|welches datum (?:haben wir|ist heute)|der wievielte ist heute|was ist heute für ein tag)$")),
    ("date", re.compile(r"^(?:what day is it|what's the date|what is the date)(?: today)?$")),
]

_WAKE = re.compile(r"^(?:(?:hey|hallo|ok|okay) )?(?:jarvis|dschawis|javis)\b ?")
_ON = {"an", "ein", "anschalten", "einschalten", "anmachen", "on"}
_UP = {"hoch", "rauf", "auf", "öffnen"}
_DEVICE_KINDS = {"light": "light", "dim": "light", "shade": "shading"}
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")


@dataclass
class QuickCommand:
    action: str  # on | off | dim | up | down | time | date
    room: str | None
    prep: str | None
    kind: str = "light"
    value: int | None = None


def _norm(text: str) -> str:
    text = re.sub(r"[.,!?;:„“\"']", " ", text.lower()).replace("oeffnen", "öffnen").replace("schliessen", "schließen")
    return re.sub(r"\s+", " ", text).strip()


def parse(text: str) -> QuickCommand | None:
    sentence = _WAKE.sub("", _norm(text)).strip()
    for kind, pattern in PATTERNS:
        match = pattern.match(sentence)
        if not match:
            continue
        groups = match.groupdict()
        room = (groups.get("room") or "").strip() or None
        if kind in ("time", "date"):
            return QuickCommand(kind, None, None, kind)
        if kind == "dim":
            value = int(groups["value"])
            if value > 100:
                return None
            return QuickCommand("dim", room, groups.get("prep"), "dim", value)
        if kind == "shade":
            return QuickCommand("up" if groups["state"] in _UP else "down", room, groups.get("prep"), "shade")
        return QuickCommand("on" if groups["state"] in _ON else "off", room, groups.get("prep"), "light")
    return None


def _room_key(name: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]", "", name.lower())


def targets(house: "House", area: "Area", command: QuickCommand) -> tuple[list["Entry"], str | None]:
    """Devices to switch and the room name to say, or ([], None) when Gemini should decide."""
    group = _DEVICE_KINDS.get(command.kind)
    if group is None:
        return [], None
    candidates = [
        e
        for e in house.catalog.entries(only_allowed=True)
        if e.group == group and not e.critical and command.action in ACTIONS.get(e.type, ())
    ]
    if command.room is None:
        main = house.catalog.get(area.main_light) if group == "light" and area.main_light else None
        if main is not None and main in candidates:
            return [main], None
        hits = [e for e in candidates if e.room in area.rooms]
        room_label = None
    else:
        key = _room_key(command.room)
        hits = [e for e in candidates if e.room and _room_key(e.room) == key]
        room_label = hits[0].room if hits else None
        if not hits:
            named = house.areas.find(command.room)
            if named is not None:
                main = house.catalog.get(named.main_light) if group == "light" and named.main_light else None
                hits = [main] if main is not None and main in candidates else [e for e in candidates if e.room in named.rooms]
                room_label = named.name
    if group == "shading":
        return hits, room_label
    controllers = [e for e in hits if e.type == "LightControllerV2"]
    chosen = controllers or hits
    if command.room is None and len(chosen) > 1 and not controllers:
        return [], None
    return chosen, room_label


def clock_reply(command: QuickCommand, now: datetime, lang: str, anrede: str | None) -> str:
    tail = f", {anrede}." if anrede else "."
    if lang == "en":
        if command.kind == "time":
            return f"It's {now:%H:%M}{tail}"
        return f"Today is {now:%A, %B} {now.day}{tail}"
    if command.kind == "time":
        return f"Es ist {now.hour} Uhr {now.minute:02d}{tail}" if now.minute else f"Es ist {now.hour} Uhr{tail}"
    return f"Heute ist {WEEKDAYS[now.weekday()]}, der {now.day}. {MONTHS[now.month - 1]}{tail}"


def reply(command: QuickCommand, room_label: str | None, lang: str, anrede: str | None, ok: bool) -> str:
    tail = f", {anrede}." if anrede else "."
    if lang == "en":
        if not ok:
            return "That didn't work."
        where = f" in the {room_label}" if room_label else ""
        if command.kind == "shade":
            return f"Blinds{where} going {'up' if command.action == 'up' else 'down'}{tail}"
        if command.kind == "dim":
            return f"Lights{where} at {command.value} percent{tail}"
        return f"Lights{where} {'on' if command.action == 'on' else 'off'}{tail}"
    if not ok:
        return "Das hat nicht geklappt."
    where = f" {command.prep or 'im'} {room_label}" if room_label else ""
    if command.kind == "shade":
        return f"Die Rollos{where} fahren {'hoch' if command.action == 'up' else 'runter'}{tail}"
    if command.kind == "dim":
        return f"Licht{where} auf {command.value} Prozent{tail}"
    state = "an" if command.action == "on" else "aus"
    return f"Licht{where} ist {state}{tail}"
