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
from ..voice.correct import closest
from ..voice.wake import WAKE_WORD
from .when import parse_duration, word_number

if TYPE_CHECKING:
    from ..areas import Area
    from .phrases import PhraseBook
    from ..house import House
    from ..loxone.catalog import Entry

_LIGHT = r"(?:das |die |den )?(?:licht|lichter|lampe|lampen|beleuchtung)"
_SHADE = r"(?:die |den |das )?(?:rollo|rollos|rolllo|rolllos|rollladen|rolladen|rollläden|jalousie|jalousien|raffstore|raffstores|beschattung)"
# A misheard first word ("Bimm", "Fahrdi") is read as the command verb it sounds like.
_COMMAND_VERBS = ("schalte", "mach", "fahr", "dimm", "stell")
_PREP = r"(?P<prep>im|in der|in dem|in|auf der|auf dem|am|beim)"
_ROOM = r"(?P<room>[a-zäöüß][a-zäöüß0-9\- ]{1,40}?)"
# Recognition sometimes hears "macht" or "schaltet" for the spoken imperative.
_VERB = r"(?:(?:schalte|schalt|schaltet|mach|mache|macht)(?: mal)? )?(?:bitte )?"
_MOVE = r"(?:(?:fahr|fahre|fahrt|mach|mache|macht)(?: mal)? )?(?:bitte )?"
_DIMV = r"(?:(?:dimm|dimme|dimmt|mach|mache|macht|stell|stelle|stellt|schalte|schaltet)(?: mal)? )?(?:bitte )?"
_STATE = r"(?P<state>an|ein|aus|anschalten|einschalten|ausschalten|anmachen|ausmachen)"
_SHADE_STATE = r"(?P<state>hoch|rauf|ganz auf|ganz zu|auf|öffnen|runter|herunter|zu|schließen)(?: fahren)?"
# Digits from Whisper, words ("dreißig Prozent") from Kroko.
_VALUE = r"(?P<value>\d{1,3}|[a-zäöüß]+) ?(?:prozent|%)?"
_END = r"(?: bitte)?"
_DEVICE = r"(?P<device>(?:das |die |den |der )?[a-zäöüß][a-zäöüß0-9\- ]{2,60}?)"
_SPOT = r"(?: (?:am|an der|an dem|beim|bei der|über dem|über der|neben dem|neben der|hinter dem|hinter der) (?P<spot>[a-zäöüß0-9\- ]{2,30}?))?"
_KIND = r"(?:das |die )?(?P<kind>indirekte|indirektes|indirekten|direkte|direktes|direkten) (?:licht|beleuchtung)"
_MOOD = r"(?P<mood>[a-zäöüß][a-zäöüß0-9\- ]{2,30}?)"
_TIMER_VERB = r"(?:(?:stell|stelle|setz|setze|mach|mache|starte|start|leg|lege)(?: mir| uns)? )?(?:bitte )?(?:mal )?"
_TIMER_WORD = r"(?:einen |nen |den |ein )?(?:timer|wecker|kurzzeitwecker|eieruhr|countdown)"
_REASON = r"(?: (?P<reason>(?:da|dann|weil|denn|wegen|damit|für|bis|zum|zur|das) .+))?"
_REASON_WORDS = {"da", "dann", "weil", "denn", "wegen", "damit", "für", "bis"}

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
    ("temp", re.compile(rf"^(?:wie warm|wie kalt|welche temperatur|wie viel grad|wieviel grad)(?: ist es| hat es| haben wir| herrscht)?(?: {_PREP} {_ROOM}| (?P<outside>draußen|draussen|außen|aussen|im freien))?{_END}$")),
    ("temp", re.compile(rf"^(?:temperatur)(?: {_PREP} {_ROOM}| (?P<outside>draußen|draussen|außen|aussen))?{_END}$")),
    ("timer", re.compile(rf"^{_TIMER_VERB}{_TIMER_WORD}(?: (?:auf|für|in|von|über))? (?P<dur>.+?){_REASON}(?: an| ein| bitte)?$")),
    ("timer", re.compile(rf"^{_TIMER_VERB}(?:in|für) (?P<dur>.+?) (?:einen |nen )?(?:timer|wecker){_REASON}(?: bitte)?$")),
    ("timer", re.compile(r"^(?:set|start) (?:a )?timer (?:for )?(?P<dur>.+?)(?: (?P<reason>(?:for|because|when) .+))?$")),
    ("reminder", re.compile(r"^erinnere? mich(?: bitte)? (?:in (?P<dur>.+?)|um (?P<clock>.+?)) (?P<reason>(?:an|ans|daran|dass|das|zu|zum|zur) .+)$")),
    ("reminder", re.compile(r"^erinnere? mich(?: bitte)? (?P<reason>(?:an|ans|daran|dass) .+?) (?:in (?P<dur>.+)|um (?P<clock>.+))$")),
    ("timer_left", re.compile(r"^(?:wie lange|wieviel zeit|wie viel zeit)(?: noch)?(?: läuft| hat| dauert| braucht)?(?: der| mein| unser)? (?:timer|wecker)(?: noch)?$")),
    ("timer_cancel", re.compile(r"^(?:(?:stopp|stoppe|stop|lösch|lösche|beende|brich|cancel)(?: den| meinen| alle| die)? (?:timer|wecker)(?: ab)?|(?:timer|wecker) (?:aus|stopp|stoppen|abbrechen|löschen|beenden))$")),
    ("time", re.compile(r"^(?:wie spät ist es|wie viel uhr ist es|wieviel uhr ist es|wie spät haben wir|was ist die uhrzeit|uhrzeit)(?: bitte)?$")),
    ("time", re.compile(r"^(?:what time is it|what's the time|what is the time)(?: please)?$")),
    ("date", re.compile(r"^(?:welcher tag ist heute|welches datum (?:haben wir|ist heute)|der wievielte ist heute|was ist heute für ein tag)$")),
    ("date", re.compile(r"^(?:what day is it|what's the date|what is the date)(?: today)?$")),
]

_WAKE = re.compile(rf"^(?:(?:hey|hallo|ok|okay) )?{WAKE_WORD}\b ?")
_ON = {"an", "ein", "anschalten", "einschalten", "anmachen", "on"}
_UP = {"hoch", "rauf", "auf", "ganz auf", "öffnen"}
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
    reason: str | None = None
    clock: str | None = None


def _norm(text: str) -> str:
    text = re.sub(r"[.,!?;:„“\"']", " ", text.lower()).replace("oeffnen", "öffnen").replace("schliessen", "schließen")
    return re.sub(r"\s+", " ", text).strip()


def parse(text: str) -> QuickCommand | None:
    sentence = _WAKE.sub("", _norm(text)).strip()
    found = _parse_sentence(sentence)
    if found is None:
        first, _, rest = sentence.partition(" ")
        verb = closest(first, _COMMAND_VERBS) if rest else None
        if verb:
            found = _parse_sentence(f"{verb} {rest}")
    return found


def _parse_sentence(sentence: str) -> QuickCommand | None:
    for kind, pattern in PATTERNS:
        match = pattern.match(sentence)
        if not match:
            continue
        groups = match.groupdict()
        room = (groups.get("room") or "").strip() or None
        if kind in ("time", "date", "timer_left", "timer_cancel"):
            return QuickCommand(kind, None, None, kind)
        if kind in ("timer", "reminder"):
            reason = (groups.get("reason") or "").strip() or None
            if groups.get("clock"):
                return QuickCommand(kind, None, None, kind, reason=reason, clock=groups["clock"].strip())
            seconds = parse_duration(groups.get("dur") or "")
            if seconds is None:
                return None
            return QuickCommand(kind, None, None, kind, value=seconds, reason=reason)
        if kind == "temp":
            return QuickCommand("temp", room, groups.get("prep"), "temp", device="outside" if groups.get("outside") else None)
        if kind == "dim":
            number = word_number(groups["value"])
            if number is None or number != int(number):
                continue
            value = int(number)
            if value > 100:
                return None
            return QuickCommand("dim", room, groups.get("prep"), "dim", value)
        if kind == "shade":
            room = re.sub(r"\s+ganz$", "", room) if room else None
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


def original_words(original: str, reason: str | None) -> str:
    """The reason as the speaker said it, with capitals, e.g. 'da ist das Essen fertig'."""
    if not reason:
        return ""
    words = re.findall(r"[\wäöüßÄÖÜ'-]+", original)
    lower = [w.lower() for w in words]
    wanted = reason.split()
    for i in range(len(lower) - len(wanted) + 1):
        if lower[i : i + len(wanted)] == wanted:
            return " ".join(words[i : i + len(wanted)])
    return reason


_LABEL_SKIP = {"jarvis", "timer", "wecker", "stunde", "stunden", "minute", "minuten", "sekunden", "sir", "uhr", "erinnerung"}


def timer_label(original: str, reason: str | None) -> str | None:
    """A short name for the timer: the first noun of the reason ('Essen'), else its last word."""
    said = original_words(original, reason)
    if not said:
        return None
    words = said.split()
    nouns = [w for w in words[1:] if w[:1].isupper() and w.lower() not in _LABEL_SKIP]
    if nouns:
        return nouns[0][:40]
    rest = [w for w in words if w.lower() not in _REASON_WORDS | {"das", "die", "der", "den", "an", "ans", "daran", "dass", "zu", "zum", "zur"}]
    return rest[-1][:40].capitalize() if rest else None


def reminder_text(original: str, reason: str | None) -> str:
    said = original_words(original, reason)
    words = said.split()
    while words and words[0].lower() in ("an", "ans", "daran", "zu"):
        words = words[1:]
    return " ".join(words)


def _anrede_and_place(command: QuickCommand, room_label: str | None, lang: str) -> str:
    if not room_label:
        return ""
    return f"in the {room_label}" if lang == "en" else f"{command.prep or 'im'} {room_label}"


_OUTSIDE = re.compile(r"au(ß|ss)en|outdoor|outside|garten|terrasse|wetter", re.I)
_TEMP_WORDS = re.compile(r"temp", re.I)


def temperature_reply(house: "House", area: "Area", command: QuickCommand, lang: str, anrede: str | None, book: "PhraseBook") -> str | None:
    """Room temperature from the room controller, outside temperature from a sensor. None lets Gemini answer."""
    state = house.miniserver.state
    entries = house.catalog.entries(only_allowed=False)
    value = None
    label = None
    if command.device == "outside":
        for e in entries:
            if e.type == "InfoOnlyAnalog" and _OUTSIDE.search(e.name) and _TEMP_WORDS.search(e.name):
                value = state(e.uuid, "value")
                label = "draußen" if lang != "en" else "outside"
                break
    else:
        controllers = [e for e in entries if e.type == "IRoomControllerV2"]
        sensors = [e for e in entries if e.type == "InfoOnlyAnalog" and _TEMP_WORDS.search(e.name) and not _OUTSIDE.search(e.name)]
        if command.room is not None:
            key = _room_key(command.room)
            pick = lambda pool: [e for e in pool if e.room and _room_key(e.room) == key]  # noqa: E731
            found = pick(controllers) or pick(sensors)
        else:
            found = [e for e in controllers if e.room in area.rooms] or [e for e in sensors if e.room in area.rooms]
        if len(found) == 1:
            e = found[0]
            value = state(e.uuid, "tempActual" if e.type == "IRoomControllerV2" else "value")
            label = _anrede_and_place(command, e.room, lang) if command.room else ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    wert = f"{number:.1f}".replace(".", ",") + " Grad" if lang != "en" else f"{number:.1f} degrees"
    return book.pick("temperature", lang, raum=label or "", anrede=anrede, wert=wert)


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
