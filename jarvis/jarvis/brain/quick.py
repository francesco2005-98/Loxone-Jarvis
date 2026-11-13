"""Instant handling of the most common sentences, without a round trip to Gemini.

Lights on, off or dimmed, a named light circuit ("die indirekte Beleuchtung am
Fernseher"), direct or indirect light, a light mood, blinds up or down,
optionally in a named room, plus time and date. Only whole sentences that match
exactly and resolve to one clear device set are taken, everything else goes to
Gemini.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ..loxone.catalog import ACTIONS

if TYPE_CHECKING:
    from ..areas import Area
    from .phrases import PhraseBook
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
_DEVICE = r"(?P<device>(?:das |die |den |der )?[a-zäöüß][a-zäöüß0-9\- ]{2,60}?)"
_SPOT = r"(?: (?:am|an der|an dem|beim|bei der|über dem|über der|neben dem|neben der|hinter dem|hinter der) (?P<spot>[a-zäöüß0-9\- ]{2,30}?))?"
_KIND = r"(?:das |die )?(?P<kind>indirekte|indirektes|indirekten|direkte|direktes|direkten) (?:licht|beleuchtung)"
_MOOD = r"(?P<mood>[a-zäöüß][a-zäöüß0-9\- ]{2,30}?)"

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("light", re.compile(rf"^{_VERB}(?P<lightword>{_LIGHT})(?: {_PREP} {_ROOM})?{_SPOT} (?:bitte )?{_STATE}{_END}$")),
    ("light", re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_LIGHT} {_STATE}{_END}$")),
    ("kind", re.compile(rf"^{_VERB}{_KIND}(?: {_PREP} {_ROOM})?{_SPOT} (?:bitte )?{_STATE}{_END}$")),
    ("kind", re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_KIND} {_STATE}{_END}$")),
    ("mood", re.compile(rf"^(?:(?:aktiviere|starte|wähle|mach|mache|schalte)(?: mal)? )?(?:bitte )?(?:die )?(?:stimmung|lichtstimmung|szene) {_MOOD}(?: {_PREP} {_ROOM})?(?: an| ein| aktivieren)?{_END}$")),
    ("dim", re.compile(rf"^{_DIMV}{_LIGHT}(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("dim", re.compile(rf"^{_DIMV}{_PREP} {_ROOM} {_LIGHT} auf {_VALUE}{_END}$")),
    ("shade", re.compile(rf"^{_MOVE}{_SHADE}(?: {_PREP} {_ROOM})? (?:bitte )?{_SHADE_STATE}{_END}$")),
    ("shade", re.compile(rf"^{_MOVE}{_PREP} {_ROOM} {_SHADE} {_SHADE_STATE}{_END}$")),
    ("light", re.compile(r"^(?:please )?turn (?P<state>on|off) (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))?(?: please)?$")),
    ("light", re.compile(r"^(?:please )?turn (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))? (?P<state>on|off)(?: please)?$")),
    ("named", re.compile(rf"^{_VERB}{_DEVICE}(?: {_PREP} {_ROOM})?{_SPOT} (?:bitte )?{_STATE}{_END}$")),
    ("named", re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_DEVICE}{_SPOT} {_STATE}{_END}$")),
    ("time", re.compile(r"^(?:wie spät ist es|wie viel uhr ist es|wieviel uhr ist es|wie spät haben wir|was ist die uhrzeit|uhrzeit)(?: bitte)?$")),
    ("time", re.compile(r"^(?:what time is it|what's the time|what is the time)(?: please)?$")),
    ("date", re.compile(r"^(?:welcher tag ist heute|welches datum (?:haben wir|ist heute)|der wievielte ist heute|was ist heute für ein tag)$")),
    ("date", re.compile(r"^(?:what day is it|what's the date|what is the date)(?: today)?$")),
]

_WAKE = re.compile(r"^(?:(?:hey|hallo|ok|okay) )?(?:jarvis|dschawis|javis)\b ?")
_ON = {"an", "ein", "anschalten", "einschalten", "anmachen", "on"}
_UP = {"hoch", "rauf", "auf", "öffnen"}
_DEVICE_KINDS = {"light": "light", "dim": "light", "shade": "shading", "kind": "light", "named": "light", "mood": "light"}
_STOP = {"das", "die", "den", "der", "dem", "im", "in", "am", "an", "beim", "bei", "über", "neben", "hinter", "mal", "bitte"}
_WEAK = {"licht", "lichter", "beleuchtung", "lampe", "lampen", "leuchte"}
_SYNONYMS = {"fernseher": "tv", "fernsehen": "tv", "fernseh": "tv", "glotze": "tv", "esstisch": "tisch", "couch": "sofa"}
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")


@dataclass
class QuickCommand:
    action: str  # on | off | dim | up | down | time | date
    room: str | None
    prep: str | None
    kind: str = "light"
    value: int | None = None
    device: str | None = None
    light_kind: str | None = None
    scene: str | None = None


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
        if kind == "mood":
            return QuickCommand("scene", room, groups.get("prep"), "mood", scene=groups["mood"].strip())
        action = "on" if groups["state"] in _ON else "off"
        spot = (groups.get("spot") or "").strip()
        if spot and kind in ("light", "kind"):
            words = groups.get("lightword") or f"{groups['kind']} beleuchtung"
            return QuickCommand(action, room, groups.get("prep"), "named", device=f"{words} {spot}")
        if kind == "kind":
            wanted = "indirect" if groups["kind"].startswith("indirekt") else "direct"
            return QuickCommand(action, room, groups.get("prep"), "kind", light_kind=wanted)
        if kind == "named":
            device = " ".join(filter(None, [groups["device"], groups.get("spot")]))
            return QuickCommand(action, room, groups.get("prep"), "named", device=device)
        return QuickCommand("on" if groups["state"] in _ON else "off", room, groups.get("prep"), "light")
    return None


def _room_key(name: str) -> str:
    return re.sub(r"[^a-zäöüß0-9]", "", name.lower())


def _tokens(text: str) -> list[str]:
    out = []
    for word in re.findall(r"[a-zäöüß0-9]+", text.lower()):
        if word in _STOP:
            continue
        word = _SYNONYMS.get(word, word)
        for suffix in ("es", "en", "er", "e", "s"):
            if len(word) > 5 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        out.append(word)
    return out


def _match(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def _words_of(entry: "Entry") -> list[str]:
    words = _tokens(" ".join([entry.name, *entry.aliases]))
    if entry.kind == "indirect":
        words.append("indirekt")
    elif entry.kind == "direct":
        words.append("direkt")
    return words


def _score(query: list[str], entry: "Entry") -> float:
    """How well the spoken words cover a device: strong words count 1, generic light words 0.3."""
    words = _words_of(entry)
    score = 0.0
    for token in query:
        if any(_match(token, w) for w in words):
            score += 0.3 if token in _WEAK else 1.0
        elif token not in _WEAK:
            score -= 0.5
    return score


def _clear_match(query: list[str], entry: "Entry") -> bool:
    """Every spoken word fits, and the sentence names a light or covers the whole device name."""
    words = _words_of(entry)
    strong = [t for t in query if t not in _WEAK]
    if not all(any(_match(t, w) for w in words) for t in strong):
        return False
    if any(t in _WEAK or t in ("indirekt", "direkt") for t in query):
        return True
    names = [w for w in _tokens(" ".join([entry.name, *entry.aliases])) if w not in _WEAK]
    return all(any(_match(w, t) for t in strong) for w in names)


def _in_room(house: "House", area: "Area", command: QuickCommand, candidates: list["Entry"]) -> tuple[list["Entry"], str | None, bool]:
    """Candidates in the named room (or the Bereich), the room label and whether a room was resolved."""
    if command.room is None:
        return [e for e in candidates if e.room in area.rooms], None, True
    key = _room_key(command.room)
    hits = [e for e in candidates if e.room and _room_key(e.room) == key]
    if hits:
        return hits, hits[0].room, True
    named = house.areas.find(command.room)
    if named is not None:
        return [e for e in candidates if e.room in named.rooms], named.name, True
    return [], None, False


def targets(house: "House", area: "Area", command: QuickCommand) -> tuple[list["Entry"], str | None]:
    """Devices to switch and the room name to say, or ([], None) when Gemini should decide."""
    group = _DEVICE_KINDS.get(command.kind)
    if group is None:
        return [], None
    action = command.action
    candidates = [
        e
        for e in house.catalog.entries(only_allowed=True)
        if e.group == group and not e.critical and action in ACTIONS.get(e.type, ())
    ]
    if command.kind == "named":
        query = _tokens(command.device or "")
        if not any(t not in _WEAK for t in query):
            return [], None
        pool, room_label, resolved = _in_room(house, area, command, candidates)
        if not resolved:
            return [], None
        if command.room is None and not pool:
            pool = candidates
        ranked = sorted(((_score(query, e), e) for e in pool), key=lambda pair: pair[0], reverse=True)
        if not ranked or ranked[0][0] < 1.0:
            return [], None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 1.0:
            return [], None
        best = ranked[0][1]
        if not _clear_match(query, best):
            return [], None
        return [best], room_label or (best.room if command.room else None)
    if command.kind == "kind":
        pool, room_label, resolved = _in_room(house, area, command, candidates)
        chosen = [e for e in pool if e.kind == command.light_kind and e.type != "LightControllerV2"]
        return (chosen, room_label) if resolved and chosen else ([], None)
    if command.kind == "mood":
        pool, room_label, resolved = _in_room(house, area, command, candidates)
        wanted = _tokens(command.scene or "")
        found = []
        for entry in pool:
            if entry.type != "LightControllerV2":
                continue
            for mood in house.catalog.moods(entry.uuid, house.miniserver.state):
                name = str(mood.get("name") or "")
                if wanted and all(any(_match(t, w) for w in _tokens(name)) for t in wanted):
                    found.append((entry, name))
                    break
        if not resolved or len(found) != 1:
            return [], None
        command.scene = found[0][1]
        return [found[0][0]], room_label
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


def _anrede_and_place(command: QuickCommand, room_label: str | None, lang: str) -> str:
    if not room_label:
        return ""
    return f"in the {room_label}" if lang == "en" else f"{command.prep or 'im'} {room_label}"


def clock_reply(command: QuickCommand, now: datetime, lang: str, anrede: str | None, book: "PhraseBook") -> str:
    if lang == "en":
        uhrzeit = f"{now:%H:%M}"
        datum = f"{now:%A, %B} {now.day}"
    else:
        uhrzeit = f"{now.hour} Uhr {now.minute:02d}" if now.minute else f"{now.hour} Uhr"
        datum = f"{WEEKDAYS[now.weekday()]}, der {now.day}. {MONTHS[now.month - 1]}"
    return book.pick(command.kind, lang, anrede=anrede, uhrzeit=uhrzeit, datum=datum)


INTENT = {
    ("light", "on"): "light_on",
    ("light", "off"): "light_off",
    ("named", "on"): "light_on",
    ("named", "off"): "light_off",
    ("kind", "on"): "light_on",
    ("kind", "off"): "light_off",
    ("mood", "scene"): "scene",
    ("dim", "dim"): "dim",
    ("shade", "up"): "shade_up",
    ("shade", "down"): "shade_down",
}


def reply(command: QuickCommand, room_label: str | None, lang: str, anrede: str | None, ok: bool, book: "PhraseBook") -> str:
    if not ok:
        return book.pick("failed", lang, anrede=anrede)
    intent = INTENT[(command.kind, command.action)]
    wert = command.scene if command.kind == "mood" else (command.value or "")
    return book.pick(intent, lang, raum=_anrede_and_place(command, room_label, lang), anrede=anrede, wert=wert)
