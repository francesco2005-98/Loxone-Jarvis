"""Instant handling of the most common sentence: a light on or off, optionally in a named room.

Only whole sentences that match exactly are taken, everything else goes to
Gemini. A room that does not resolve to allowed lights also goes to Gemini.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..loxone.catalog import ACTIONS

if TYPE_CHECKING:
    from ..areas import Area
    from ..house import House
    from ..loxone.catalog import Entry

_LIGHT = r"(?:das |die |den )?(?:licht|lichter|lampe|lampen|beleuchtung)"
_PREP = r"(?P<prep>im|in der|in dem|in|auf der|auf dem|am|beim)"
_ROOM = r"(?P<room>[a-zäöüß][a-zäöüß0-9\- ]{1,40}?)"
_VERB = r"(?:(?:schalte|schalt|mach|mache)(?: mal)? )?(?:bitte )?"
_STATE = r"(?P<state>an|ein|aus|anschalten|einschalten|ausschalten|anmachen|ausmachen)"
_END = r"(?: bitte)?"

PATTERNS = [
    re.compile(rf"^{_VERB}{_LIGHT}(?: {_PREP} {_ROOM})? (?:bitte )?{_STATE}{_END}$"),
    re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_LIGHT} {_STATE}{_END}$"),
    re.compile(r"^(?:please )?turn (?P<state>on|off) (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))?(?: please)?$"),
    re.compile(r"^(?:please )?turn (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))? (?P<state>on|off)(?: please)?$"),
]

_WAKE = re.compile(r"^(?:(?:hey|hallo|ok|okay) )?(?:jarvis|dschawis|javis)\b ?")
_ON = {"an", "ein", "anschalten", "einschalten", "anmachen", "on"}


@dataclass
class QuickCommand:
    action: str  # on | off
    room: str | None
    prep: str | None


def _norm(text: str) -> str:
    text = re.sub(r"[.,!?;:„“\"']", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def parse(text: str) -> QuickCommand | None:
    sentence = _WAKE.sub("", _norm(text)).strip()
    for pattern in PATTERNS:
        match = pattern.match(sentence)
        if match:
            groups = match.groupdict()
            room = (groups.get("room") or "").strip() or None
            action = "on" if groups["state"] in _ON else "off"
            return QuickCommand(action, room, groups.get("prep"))
    return None


def _room_key(name: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]", "", name.lower())


def targets(house: "House", area: "Area", command: QuickCommand) -> tuple[list["Entry"], str | None]:
    """Lights to switch and the room name to say, or ([], None) when Gemini should decide."""
    lights = [
        e
        for e in house.catalog.entries(only_allowed=True)
        if e.group == "light" and not e.critical and command.action in ACTIONS.get(e.type, ())
    ]
    if command.room is None:
        main = house.catalog.get(area.main_light) if area.main_light else None
        if main is not None and main in lights:
            return [main], None
        hits = [e for e in lights if e.room in area.rooms]
        room_label = None
    else:
        key = _room_key(command.room)
        hits = [e for e in lights if e.room and _room_key(e.room) == key]
        room_label = hits[0].room if hits else None
        if not hits:
            named = house.areas.find(command.room)
            if named is not None:
                main = house.catalog.get(named.main_light) if named.main_light else None
                hits = [main] if main is not None and main in lights else [e for e in lights if e.room in named.rooms]
                room_label = named.name
    controllers = [e for e in hits if e.type == "LightControllerV2"]
    chosen = controllers or hits
    if command.room is None and len(chosen) > 1 and not controllers:
        return [], None
    return chosen, room_label


def reply(command: QuickCommand, room_label: str | None, lang: str, anrede: str | None, ok: bool) -> str:
    if lang == "en":
        if not ok:
            return "That didn't work."
        where = f" in the {room_label}" if room_label else ""
        return f"Lights{where} {'on' if command.action == 'on' else 'off'}" + (f", {anrede}." if anrede else ".")
    if not ok:
        return "Das hat nicht geklappt."
    state = "an" if command.action == "on" else "aus"
    where = f" {command.prep or 'im'} {room_label}" if room_label else ""
    return f"Licht{where} ist {state}" + (f", {anrede}." if anrede else ".")
