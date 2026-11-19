"""Instant handling of house sentences, without a round trip to Gemini.

Lights, shades, windows, heating, colour, music, readings, timers, time and
date. Garage and alarm ask for a spoken yes first. Only a sentence that matches
exactly and resolves to one clear device set is taken. Everything else, including
weather and open questions, still goes to Gemini.
"""

from __future__ import annotations

import random
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
_SHADE = r"(?:die |den |das )?(?:rollo|rollos|rolllo|rolllos|rollladen|rolladen|rollläden|rolläden|jalousie|jalousien|raffstore|raffstores|beschattung)"
# A misheard first word ("Bimm", "Fahrdi") is read as the command verb it sounds like.
_COMMAND_VERBS = ("schalte", "mach", "fahr", "dimm", "stell")
_PREP = r"(?P<prep>im|in der|in dem|in|auf der|auf dem|am|beim)"
_ROOM = r"(?P<room>[a-zäöüß][a-zäöüß0-9\- ]{1,40}?)"
# Recognition sometimes hears "macht" or "schaltet" for the spoken imperative.
_VERB = r"(?:mal )?(?:(?:schalte|schalt|schaltet|mach|mache|macht|stell|stelle|stellt|dreh|drehe)(?: mal)? )?(?:bitte )?(?:mal )?"
_MOVE = r"(?:mal )?(?:(?:fahr|fahre|fahrt|zieh|ziehe|mach|mache|macht|stell|stelle|lass|lasse)(?: mal)? )?(?:bitte )?(?:mal )?"
_DIMV = r"(?:mal )?(?:(?:dimm|dimme|dimmt|mach|mache|macht|stell|stelle|stellt|schalte|schaltet)(?: mal)? )?(?:bitte )?(?:mal )?"
_STATE = r"(?P<state>anschalten|einschalten|ausschalten|anmachen|ausmachen|an|ein|aus|hell|dunkel)"
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

_COLORS = {
    "rot": 0, "orange": 30, "gelb": 60, "grün": 120, "gruen": 120, "türkis": 180, "tuerkis": 180,
    "blau": 240, "lila": 270, "violett": 270, "pink": 330, "rosa": 330,
}
_WHITES = {"warmweiß": 2700, "warmweiss": 2700, "kaltweiß": 6500, "kaltweiss": 6500, "neutralweiß": 4000, "neutralweiss": 4000}
_WINDOW = r"(?:das |die |den )?(?:fenster|fenstern)"
_GATE = r"(?:das |die |den )?(?P<device>garagentor|garage|tor|tore|einfahrt)"
_COLOR_WORD = "|".join(list(_COLORS) + list(_WHITES))

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("light", re.compile(rf"^{_VERB}(?P<all>alle |sämtliche )(?P<lightword>{_LIGHT})(?: {_PREP} {_ROOM})? (?:bitte )?{_STATE}{_END}$")),
    ("light", re.compile(rf"^{_VERB}(?P<lightword>{_LIGHT}) (?P<everywhere>überall|im ganzen haus) (?:bitte )?{_STATE}{_END}$")),
    ("light", re.compile(rf"^{_VERB}(?P<everywhere>überall|im ganzen haus) {_LIGHT} (?:bitte )?{_STATE}{_END}$")),
    ("light", re.compile(rf"^(?:mach|mache|macht)(?: es)? (?P<state>hell|dunkel)(?: {_PREP} {_ROOM})?{_END}$")),
    ("light", re.compile(rf"^{_VERB}(?P<lightword>{_LIGHT})(?: {_PREP} {_ROOM})?{_SPOT} (?:bitte )?{_STATE}{_END}$")),
    ("light", re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_LIGHT} {_STATE}{_END}$")),
    ("kind", re.compile(rf"^{_VERB}{_KIND}(?: {_PREP} {_ROOM})?{_SPOT} (?:bitte )?{_STATE}{_END}$")),
    ("kind", re.compile(rf"^{_VERB}{_PREP} {_ROOM} {_KIND} {_STATE}{_END}$")),
    ("mood", re.compile(rf"^(?:(?:aktiviere|starte|wähle|mach|mache|schalte)(?: mal)? )?(?:bitte )?(?:die )?(?:stimmung|lichtstimmung|szene) {_MOOD}(?: {_PREP} {_ROOM})?(?: an| ein| aktivieren)?{_END}$")),
    ("rel", re.compile(rf"^{_DIMV}{_LIGHT}(?: {_PREP} {_ROOM})? (?P<degree>deutlich |viel |etwas |ein bisschen |ein wenig |noch |ein stück )?(?P<rel>heller|dunkler|hoch|rauf|runter){_END}$")),
    ("rel", re.compile(rf"^(?:mach|mache|macht)(?: es)? (?P<degree>deutlich |viel |etwas |ein bisschen |ein wenig |noch |ein stück )?(?P<rel>heller|dunkler)(?: {_PREP} {_ROOM})?{_END}$")),
    ("dim", re.compile(rf"^{_DIMV}{_LIGHT}(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("dim", re.compile(rf"^{_DIMV}{_PREP} {_ROOM} {_LIGHT} auf {_VALUE}{_END}$")),
    ("shade_pos", re.compile(rf"^{_MOVE}{_SHADE}(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("shade_pos", re.compile(rf"^{_MOVE}{_SHADE}(?: {_PREP} {_ROOM})? halb (?:zu|runter|auf|hoch|offen){_END}$")),
    ("shade_stop", re.compile(rf"^(?:halt|halte|stoppe|stopp|stop)(?: mal)? (?:bitte )?{_SHADE}(?: {_PREP} {_ROOM})?(?: an| ab)?{_END}$")),
    ("shade_stop", re.compile(rf"^{_MOVE}{_SHADE}(?: {_PREP} {_ROOM})? (?:anhalten|stopp|stop|halt){_END}$")),
    ("shade", re.compile(rf"^{_MOVE}(?:alle |sämtliche )?{_SHADE}(?: {_PREP} {_ROOM})? (?:bitte )?{_SHADE_STATE}{_END}$")),
    ("shade", re.compile(rf"^{_MOVE}{_PREP} {_ROOM} {_SHADE} {_SHADE_STATE}{_END}$")),
    ("light", re.compile(r"^(?:please )?turn (?P<state>on|off) (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))?(?: please)?$")),
    ("light", re.compile(r"^(?:please )?turn (?:the )?lights?(?: in (?:the )?(?P<room>[a-z][a-z\- ]{1,40}?))? (?P<state>on|off)(?: please)?$")),
    ("status", re.compile(rf"^(?:ist|sind|brennt|leuchtet|läuft) {_LIGHT}(?: {_PREP} {_ROOM})?(?: (?:gerade )?(?P<state>an|aus|ein|eingeschaltet|ausgeschaltet|hell|dunkel))?$")),
    ("status", re.compile(rf"^(?:ist die |läuft die |ist |haben wir die |haben wir )heizung(?: {_PREP} (?P<room>[a-zäöüß0-9\-]+))?(?: (?:gerade )?(?P<state>an|aus|ein|eingeschaltet|ausgeschaltet))?$")),
    ("status", re.compile(rf"^(?:ist es|haben wir das licht) (?:gerade )?(?P<state>hell|dunkel|an|aus)(?: {_PREP} {_ROOM})?$")),
    ("status", re.compile(rf"^(?:haben wir) {_LIGHT}(?: {_PREP} {_ROOM})? (?:gerade )?(?P<state>an|aus|ein|eingeschaltet|ausgeschaltet)$")),
    ("status", re.compile(rf"^(?:ist|sind) {_WINDOW}(?: {_PREP} {_ROOM})? (?:gerade )?(?P<state>offen|auf|zu|geschlossen)$")),
    ("status", re.compile(rf"^(?:ist|sind) {_SHADE}(?: {_PREP} {_ROOM})? (?:gerade )?(?P<state>oben|unten|auf|zu|offen|geschlossen)$")),
    ("status", re.compile(rf"^(?:ist jemand|ist wer)(?: {_PREP} {_ROOM}| da| zuhause| zu hause)?$")),
    ("status", re.compile(r"^(?:wie viel|wieviel) (?:strom|verbrauch|energie)(?: verbrauchen wir| ziehen wir| haben wir)?$")),
    ("music", re.compile(r"^(?:dreh |drehe |mach |mache )?(?:die |das )?(?:musik )?(?:etwas |ein bisschen )?(?P<music>lauter|leiser)(?: machen)?$")),
    ("music", re.compile(r"^(?:halt|halte|stoppe|stopp|stop)(?: mal)? (?:die |das )?musik(?: (?P<music>an|ab|aus))?$")),
    ("music", re.compile(r"^(?:dreh |drehe |mach |mache |schalte |schalt )?(?:die |das )?musik (?P<music>aus|an|pause|weiter|lauter|leiser|stopp|anhalten)$")),
    ("music", re.compile(r"^(?:spiel |spiele )?(?:die musik )?(?P<music>pause|pausieren|weiter)$")),
    ("music", re.compile(rf"^(?:dreh |drehe |mach |stell |stelle )?(?:die |das )?(?:musik |lautstärke |musik lautstärke )auf {_VALUE}$")),
    ("music", re.compile(r"^(?:spiel |spiele )?(?:mal )?(?:das |den )?(?:nächste|nächstes|nächster) (?:lied|titel|song)$")),
    ("music", re.compile(r"^(?:vorheriges|vorheriger|letztes) (?:lied|titel|song)$")),
    ("music", re.compile(rf"^(?:lautstärke|musik lautstärke) auf {_VALUE}$")),
    ("music", re.compile(rf"^(?:spiel |spiele )?(?:bitte )?(?:den |die )?(?:favoriten|favorit) (?P<fav>[a-zäöüß0-9][a-zäöüß0-9\- ]{{1,40}}?)(?: {_PREP} {_ROOM})?$")),
    ("music", re.compile(rf"^(?:spiel|spiele) (?:bitte )?(?:mal )?(?:etwas )?(?P<query>[a-zäöüß0-9][a-zäöüß0-9\- ]{{1,40}}?)(?: {_PREP} {_ROOM})?$")),
    ("alarm", re.compile(r"^(?:mach |schalte |stell )?(?:bitte )?(?:den |die |das )?alarm (?P<state>scharf|unscharf|an|aus|ein)$")),
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
    ("climate", re.compile(rf"^(?:stell|stelle|setz|setze|mach|dreh|drehe|regel|regle|heiz|heize)? ?(?:die |das |den |der )?(?:heizung|temperatur)(?: {_PREP} {_ROOM})? auf (?P<value>\d{{1,2}}(?:[.,]\d)?) ?(?:grad|°)?{_END}$")),
    ("climate", re.compile(rf"^(?:heizung|temperatur)(?: {_PREP} {_ROOM})? auf (?P<value>\d{{1,2}}(?:[.,]\d)?) ?grad{_END}$")),
    ("climate", re.compile(rf"^(?:stell|stelle|setz|setze|dreh|drehe|mach) (?:sie |es )?auf (?P<value>\d{{1,2}}(?:[.,]\d)?) ?(?:grad|°)(?: {_PREP} {_ROOM})?{_END}$")),
    ("climate", re.compile(rf"^(?P<value>\d{{1,2}}(?:[.,]\d)?) ?(?:grad|°)(?: {_PREP} {_ROOM})?{_END}$")),
    ("climate", re.compile(rf"^(?:(?:stell|stelle|schalte|schalt|setze|setz|drehe|dreh|wechsel|wechsle|mach|mache|regel|regle) )?(?:die |das |den |der )?(?:heizung|temperatur)(?: {_PREP} {_ROOM})? (?:auf |in den |in |zum |zur )?(?P<mode>automatik|auto|heizen|kühlen|kuehlen|manuell)(?: modus| betrieb)?{_END}$")),
    ("climate", re.compile(rf"^(?:schalt|schalte|stell|stelle|wechsel|wechsle|setz|setze|mach|dreh|drehe) auf (?P<mode>automatik|auto|heizen|kühlen|kuehlen|manuell){_END}$")),
    ("window", re.compile(rf"^(?P<state>öffne|oeffne|schließe|schliesse|schließ|schliess|kipp|kippe)(?: mal)? (?:bitte )?(?:alle )?{_WINDOW}(?: {_PREP} {_ROOM})?{_END}$")),
    ("window", re.compile(rf"^(?:mach|mache|öffne|oeffne|schließe|schliesse|schließ|schliess|fahr|fahre|kipp|kippe)(?: mal)? (?:bitte )?(?:alle )?{_WINDOW}(?: {_PREP} {_ROOM})? (?P<state>auf|zu|öffnen|oeffnen|schließen|schliessen|stopp|anhalten|kipp|auf kipp){_END}$")),
    ("window", re.compile(rf"^(?:alle )?{_WINDOW}(?: {_PREP} {_ROOM})? (?P<state>auf|zu|öffnen|schließen|schliessen|stopp|anhalten|kippen|kipp|auf kipp){_END}$")),
    ("window", re.compile(rf"^(?:mach|fahr)(?: mal)? (?:bitte )?{_WINDOW}(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("window", re.compile(rf"^{_WINDOW}(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("slat", re.compile(rf"^(?:fahr|stell|mach)(?: mal)? (?:bitte )?(?:die )?lamellen(?: {_PREP} {_ROOM})? auf {_VALUE}{_END}$")),
    ("slat", re.compile(rf"^(?:fahr|stell|mach)(?: mal)? (?:bitte )?(?:die )?lamellen(?: {_PREP} {_ROOM})? (?P<state>auf|zu|offen){_END}$")),
    ("slat", re.compile(rf"^(?:die )?lamellen(?: {_PREP} {_ROOM})? (?P<state>auf|zu|offen|hoch|runter){_END}$")),
    ("color", re.compile(rf"^{_VERB}{_LIGHT}(?: {_PREP} {_ROOM})? (?:auf )?(?P<color>{_COLOR_WORD}){_END}$")),
    ("color", re.compile(rf"^(?:stell |stelle |mach |mache |schalte |schalt )?{_VERB}(?:die |das )?farbe(?: {_PREP} {_ROOM})? (?:auf )?(?P<color>{_COLOR_WORD}){_END}$")),
    ("gate", re.compile(rf"^(?:mach|mache|öffne|oeffne|schließe|schliesse|schließ|schliess|fahr)(?: mal)? (?:bitte )?{_GATE} (?P<state>auf|zu|öffnen|oeffnen|schließen|schliessen|stopp){_END}$")),
    ("gate", re.compile(rf"^{_GATE} (?P<state>auf|zu|öffnen|oeffnen|schließen|schliessen|stopp){_END}$")),
]

_WAKE = re.compile(rf"^(?:(?:hey|hallo|ok|okay) )?{WAKE_WORD}\b ?")
_ON = {"an", "ein", "anschalten", "einschalten", "anmachen", "hell", "on"}
_UP = {"hoch", "rauf", "auf", "ganz auf", "öffnen"}
_DEVICE_KINDS = {
    "light": "light",
    "dim": "light",
    "shade": "shading",
    "kind": "light",
    "named": "light",
    "mood": "light",
    "window": "window",
    "slat": "shading",
    "climate": "climate",
    "color": "light",
    "white": "light",
    "gate": "gate",
    "alarm": "alarm",
}
_CLIMATE_MODES = {"automatik": 0, "auto": 0, "heizen": 1, "kühlen": 2, "kuehlen": 2, "manuell": 3}
_TAIL = {
    "anmachen": ("mach", "an"), "anschalten": ("schalte", "an"), "anstellen": ("mach", "an"),
    "ausmachen": ("mach", "aus"), "ausschalten": ("schalte", "aus"), "abstellen": ("mach", "aus"), "ausstellen": ("mach", "aus"),
    "einschalten": ("schalte", "ein"), "aufmachen": ("mach", "auf"), "zumachen": ("mach", "zu"),
    "umschalten": ("stell", ""),
    "runterlassen": ("fahr", "runter"), "runterfahren": ("fahr", "runter"), "absenken": ("fahr", "runter"),
    "hochfahren": ("fahr", "hoch"), "hochlassen": ("fahr", "hoch"), "aufziehen": ("fahr", "hoch"), "hochziehen": ("fahr", "hoch"),
    "zuziehen": ("fahr", "zu"), "runterziehen": ("fahr", "runter"),
    "stellen": ("stell", ""), "umstellen": ("stell", ""), "einstellen": ("stell", ""), "schalten": ("schalte", ""),
    "machen": ("mach", ""), "fahren": ("fahr", ""),
    "dimmen": ("dimm", ""), "drehen": ("dreh", ""), "aufdrehen": ("mach", "auf"), "zudrehen": ("mach", "zu"),
    "öffnen": ("mach", "auf"), "oeffnen": ("mach", "auf"), "schließen": ("mach", "zu"), "schliessen": ("mach", "zu"),
    "setzen": ("stell", ""), "regeln": ("stell", ""), "aktivieren": ("aktiviere", ""), "starten": ("starte", ""),
    "kippen": ("kipp", ""), "anheben": ("fahr", "hoch"), "löschen": ("mach", "aus"),
}
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
    scope: str | None = None  # all = every light in the room, house = every light
    value2: int | None = None


def _norm(text: str) -> str:
    text = re.sub(r"(\d),(\d)", r"\1.\2", text.lower())
    text = re.sub(r"[!?;:„“\"',]", " ", text)
    text = re.sub(r"\.(?!\d)|(?<!\d)\.", " ", text)
    text = text.replace("oeffnen", "öffnen").replace("schliessen", "schließen")
    return re.sub(r"\s+", " ", text).strip()


def _loosen(sentence: str) -> str:
    """Fold polite wrappers, synonyms and a verb at the end into the shape the patterns know."""
    sentence = re.sub(
        r"^(?:ich möchte(?: dass du| gerne| gern)? |ich hätte gern(?:e)? |ich will |bitte |"
        r"sei so gut und |kannst du (?:mir |uns |bitte |mal )*|könntest du (?:mir |uns |bitte |mal )*|"
        r"würdest du (?:mir |uns |bitte |mal )*|magst du (?:mir |bitte |mal )*)",
        "",
        sentence,
    )
    sentence = re.sub(r"\b(?:hier|doch|eben|schnell|kurz|bitte|mal|jetzt|gleich|einmal|ruhig|einfach)\b", " ", sentence)
    sentence = re.sub(r"\b(an|aus|ein) (machen|schalten)\b", r"\1\2", sentence)
    for pattern, repl in (
        (r"\bautomatisch(?:e|en|er|em)?\b", "automatik"),
        (r"\bautomatikmodus\b", "automatik"),
        (r"\bautomodus\b", "automatik"),
        (r"\bheizbetrieb\b", "heizen"),
        (r"\bheizmodus\b", "heizen"),
        (r"\bkühlbetrieb\b", "kühlen"),
        (r"\bkuehlbetrieb\b", "kühlen"),
        (r"\bkühlmodus\b", "kühlen"),
        (r"\bheizkörper\b", "heizung"),
        (r"\bthermostat\b", "heizung"),
        (r"\braumregler\b", "heizung"),
        (r"\btemperaturregler\b", "heizung"),
        (r"\bklimaanlage\b", "heizung"),
        (r"\bjalousien\b", "jalousie"),
        (r"\brollläden\b", "rollladen"),
        (r"\brolllaeden\b", "rollladen"),
        (r"\bgaragentür\b", "garagentor"),
        (r"\brolläden\b", "rollladen"),
    ):
        sentence = re.sub(pattern, repl, sentence)
    sentence = re.sub(r"\b(?:in den|in dem|zum|zur) (automatik|heizen|kühlen|manuell)\b", r"auf \1", sentence)
    flipped = re.match(
        r"^auf (automatik|heizen|kühlen|manuell) (?:stellen|umstellen|schalten|machen|setzen) (?:die |das |den |der )?(heizung|temperatur)$",
        sentence,
    )
    if flipped:
        sentence = f"stell die {flipped.group(2)} auf {flipped.group(1)}"
    if sentence in {"automatik", "heizen", "kühlen", "manuell"}:
        sentence = f"heizung {sentence}"
    if "erinner" not in sentence:
        sentence = re.sub(r"\s+um$", "", sentence)
    sentence = re.sub(r"\s+", " ", sentence).strip()
    middle = re.match(r"^(?P<body>.+?) (?P<verb>dimmen|stellen|umstellen|fahren|machen|schalten|drehen|setzen|regeln) (?P<rest>auf .+)$", sentence)
    if middle and middle.group("verb") in _TAIL:
        front, particle = _TAIL[middle.group("verb")]
        body = middle.group("body")
        if particle and not body.endswith(" " + particle):
            body = f"{body} {particle}"
        sentence = f"{front} {body} {middle.group('rest')}".strip()
    tail = re.match(r"^(?P<body>.+?) (?P<verb>[a-zäöüß]+)$", sentence)
    if tail and tail.group("verb") in _TAIL:
        front, particle = _TAIL[tail.group("verb")]
        body = tail.group("body")
        first = body.split(" ", 1)[0]
        if particle and not body.endswith(" " + particle):
            body = f"{body} {particle}"
        if first not in {"mach", "mache", "fahr", "fahre", "stell", "stelle", "schalte", "schalt", "lass", "lasse", "dimm", "öffne", "schließ", "zieh", "ziehe"}:
            sentence = f"{front} {body}".strip()
        else:
            sentence = body
    sentence = re.sub(r"^(?:lass|lasse) ", "mach ", sentence)
    if (
        re.match(r"^(?:stell|stelle|setz|setze|regel|regle|dreh|drehe) ", sentence)
        and sentence.endswith(" ein")
        and re.search(r"\b(?:grad|prozent|%|automatik|heizen|kühlen|manuell)\b", sentence)
    ):
        sentence = sentence[:-4]
    return re.sub(r"\s+", " ", sentence).strip()


def parse(text: str) -> QuickCommand | None:
    sentence = _loosen(_WAKE.sub("", _norm(text)).strip())
    sentence = sentence.replace("auf die hälfte", "auf 50 prozent").replace("auf die haelfte", "auf 50 prozent")
    sentence = sentence.replace("auf halb", "auf 50 prozent")
    sentence = re.sub(r"\s+", " ", sentence).strip()
    # Two actions in one sentence stay with Gemini. Timers may say "eine Stunde und 30 Minuten".
    if " und " in sentence and not re.search(r"\b(?:timer|wecker|erinner)", sentence):
        return None
    found = _parse_sentence(sentence)
    if found is None:
        mood = re.match(
            rf"^(?:mach |mache |schalte |starte )?(?:es |bitte |mal )*(?P<mood>gute nacht|gemütlich|fernsehen|entspannen|lesen|kino)(?: {_PREP} {_ROOM})?$",
            sentence,
        )
        if mood:
            found = QuickCommand("scene", (mood.groupdict().get("room") or "").strip() or None, mood.groupdict().get("prep"), "mood", scene=mood.group("mood"))
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
        if kind == "rel":
            degree = (groups.get("degree") or "").strip()
            delta = 25 if degree in {"deutlich", "viel"} else 12
            if groups["rel"] in {"dunkler", "runter"}:
                delta = -delta
            return QuickCommand("rel", room, groups.get("prep"), "dim", delta)
        if kind == "shade_stop":
            return QuickCommand("stop", room, groups.get("prep"), "shade")
        if kind == "shade_pos":
            raw = groups.get("value")
            if raw:
                number = word_number(raw)
                if number is None or number != int(number) or not 0 <= int(number) <= 100:
                    return None
                value = int(number)
            else:
                value = 50
            return QuickCommand("position", room, groups.get("prep"), "shade", value)
        if kind == "climate":
            if groups.get("mode"):
                return QuickCommand("mode", room, groups.get("prep"), "climate", _CLIMATE_MODES[groups["mode"]], scene=groups["mode"])
            number = word_number(groups["value"].replace(",", "."))
            if number is None:
                return None
            return QuickCommand("temperature", room, groups.get("prep"), "climate", value=number)
        if kind == "window":
            state = groups.get("state") or ""
            if groups.get("value"):
                number = word_number(groups["value"])
                if number is None or not 0 <= int(number) <= 100:
                    return None
                return QuickCommand("position", room, groups.get("prep"), "window", int(number))
            if state in {"kippen", "kipp", "auf kipp"} or sentence.startswith(("kipp ", "kippe ")):
                return QuickCommand("position", room, groups.get("prep"), "window", 12, scene="gekippt")
            if state in {"zu", "schließen", "schliessen"} or state.startswith("schließ") or state.startswith("schlies"):
                return QuickCommand("close", room, groups.get("prep"), "window")
            if state in {"stopp", "anhalten"}:
                return QuickCommand("stop", room, groups.get("prep"), "window")
            return QuickCommand("open", room, groups.get("prep"), "window")
        if kind == "slat":
            if groups.get("value"):
                number = word_number(groups["value"])
                if number is None or not 0 <= int(number) <= 100:
                    return None
                value = int(number)
            else:
                value = 0 if groups.get("state") in {"auf", "offen", "hoch"} else 100
            return QuickCommand("slat", room, groups.get("prep"), "slat", value)
        if kind == "color":
            color = groups["color"]
            if color in _WHITES:
                return QuickCommand("white", room, groups.get("prep"), "white", _WHITES[color], scene=color)
            return QuickCommand("color", room, groups.get("prep"), "color", _COLORS[color], scene=color)
        if kind == "gate":
            state = groups["state"]
            action = "close" if state in {"zu", "schließen", "schliessen"} else "stop" if state == "stopp" else "open"
            return QuickCommand(action, room, groups.get("prep"), "gate", device=groups.get("device"))
        if kind == "alarm":
            state = groups["state"]
            return QuickCommand("disarm" if state in {"aus", "unscharf"} else "arm", None, None, "alarm")
        if kind == "status":
            if groups.get("state") and "fenster" in sentence:
                topic = "window"
            elif groups.get("state") and re.search(r"rollo|jalousie|beschattung|raffstore", sentence):
                topic = "shade"
            elif "heizung" in sentence:
                topic = "climate"
            elif "jemand" in sentence or "wer" in sentence:
                topic = "presence"
            elif "strom" in sentence or "verbrauch" in sentence or "energie" in sentence:
                topic = "power"
            else:
                topic = "light"
            return QuickCommand("read", room, groups.get("prep"), "status", device=topic)
        if kind == "music":
            if groups.get("value"):
                number = word_number(groups["value"])
                if number is None or not 0 <= int(number) <= 100:
                    return None
                return QuickCommand("volume", room, groups.get("prep"), "music", int(number))
            if groups.get("fav"):
                return QuickCommand("favorite", room, groups.get("prep"), "music", scene=groups["fav"].strip())
            if groups.get("query"):
                query = groups["query"].strip()
                if re.search(r"\b(?:licht|lampe|rollo|fenster|tor|heizung|alarm)\b", query):
                    return None
                return QuickCommand("search", room, groups.get("prep"), "music", scene=query)
            word = (groups.get("music") or "").strip()
            if sentence.startswith(("halt ", "halte ", "stopp", "stoppe ", "stop ")) and "musik" in sentence:
                word = "aus"
            if not word and "nächst" in sentence:
                word = "next"
            if not word and "vorher" in sentence or sentence.startswith("letztes"):
                word = "previous"
            mapping = {
                "aus": "pause", "pause": "pause", "pausieren": "pause", "stopp": "pause",
                "an": "play", "weiter": "play", "lauter": "louder", "leiser": "quieter",
                "next": "next", "previous": "previous",
            }
            action = mapping.get(word)
            if action is None:
                return None
            return QuickCommand(action, room, groups.get("prep"), "music")
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
            if re.search(r"\b(?:heizung|temperatur)\b", device):
                continue
            return QuickCommand(action, room, groups.get("prep"), "named", device=device)
        scope = "house" if groups.get("everywhere") else ("all" if (groups.get("all") or "").strip() else None)
        return QuickCommand("on" if groups["state"] in _ON else "off", room, groups.get("prep"), "light", scope=scope)
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


def _safety_targets(house: "House", area: "Area", command: QuickCommand, group: str) -> tuple[list["Entry"], str | None]:
    """Garage, gate and alarm stay out of the normal light filter. They still need a spoken yes."""
    pool = [
        e
        for e in house.catalog.entries(only_allowed=True)
        if e.group == group and command.action in ACTIONS.get(e.type, ())
    ]
    if command.device:
        query = _tokens(command.device)
        pool = [e for e in pool if _score(query, e) >= 1.0]
    elif command.room is not None:
        pool, _, resolved = _in_room(house, area, command, pool)
        if not resolved:
            pool = []
    else:
        local = [e for e in pool if e.room in area.rooms]
        pool = local or (pool if len(pool) == 1 else [])
    return (pool, pool[0].room if pool else None)


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
    if command.kind in ("gate", "alarm"):
        return _safety_targets(house, area, command, group)
    action = command.action
    candidates = [
        e
        for e in house.catalog.entries(only_allowed=True)
        if e.group == group and not e.critical and action in ACTIONS.get(e.type, ())
    ]
    if command.scope == "house" and group == "light":
        chosen = [e for e in candidates if e.type == "LightControllerV2"] or candidates
        return (chosen, None) if chosen else ([], None)
    if command.scope == "all" and group == "light":
        pool, room_label, resolved = _in_room(house, area, command, candidates)
        if command.room is None and not pool:
            pool = [e for e in candidates if e.room in area.rooms]
        chosen = [e for e in pool if e.type == "LightControllerV2"] or pool
        return (chosen, room_label) if resolved and chosen else ([], None)
    if command.kind == "named":
        if group == "light" and command.action in ("on", "off"):
            candidates = candidates + [
                e
                for e in house.catalog.entries(only_allowed=True)
                if e.group == "other"
                and e.type in ("Switch", "TimedSwitch", "Pushbutton")
                and not e.critical
                and command.action in ACTIONS.get(e.type, ())
            ]
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
    if group in ("shading", "window", "climate"):
        return hits, room_label
    controllers = [e for e in hits if e.type == "LightControllerV2"]
    chosen = controllers or hits
    if command.room is None and len(chosen) > 1 and not controllers:
        return [], None
    return chosen, room_label


def brightness(house: "House", entry: "Entry") -> int | None:
    """Current level in percent, if this device has one. None lets the sentence go to Gemini."""
    state = house.miniserver.state
    raw = None
    if entry.type in ("Dimmer", "EIBDimmer"):
        raw = state(entry.uuid, "position")
    elif entry.type == "LightControllerV2":
        master = house.miniserver.structure.child(entry.uuid, "/masterValue")
        raw = state(master.uuid, "position") if master is not None else None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return max(0, min(100, int(round(number))))


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


def _reading(name: str, text: str) -> str:
    """One device, said as a sentence fragment rather than a fixed 'Name: Wert'."""
    shown = (text or "unbekannt").strip()
    label = name.strip()
    return random.choice((
        f"{label} ist {shown}",
        f"bei {label} steht {shown}",
        f"{label} meldet {shown}",
        f"am {label} lese ich {shown}",
        f"für {label} gilt {shown}",
        f"{label}: {shown}",
    ))


def status_reply(house: "House", area: "Area", command: QuickCommand, lang: str, anrede: str | None, book: "PhraseBook") -> str | None:
    """A reading from the Miniserver. None when the house has no such device."""
    catalog = house.catalog
    state = house.miniserver.state
    topic = command.device

    def place(pool: list["Entry"]) -> list["Entry"]:
        if command.room is not None:
            found, _, resolved = _in_room(house, area, command, pool)
            return found if resolved else []
        local = [e for e in pool if e.room in area.rooms]
        return local or pool

    if topic == "power":
        meters = place([e for e in catalog.entries(only_allowed=True) if e.type == "Meter"])
        parts = [_reading(e.name, catalog.describe_state(e, state)) for e in meters if catalog.describe_state(e, state)]
        if not parts:
            return None
        wert = "; ".join(parts)
    elif topic == "climate":
        rooms = place([e for e in catalog.entries(only_allowed=True) if e.type == "IRoomControllerV2"])
        if not rooms:
            return None
        wert = ", ".join(_reading(e.name, catalog.describe_state(e, state)) for e in rooms)
    elif topic == "presence":
        people = place([e for e in catalog.entries(only_allowed=True) if e.type == "PresenceDetector"])
        if not people:
            return None
        wert = ", ".join(_reading(e.name, catalog.describe_state(e, state)) for e in people)
    elif topic == "window":
        monitors = place([e for e in catalog.entries(only_allowed=True) if e.type == "WindowMonitor"])
        windows = place([e for e in catalog.entries(only_allowed=True) if e.type == "Window"])
        chosen = monitors or windows
        if not chosen:
            return None
        wert = ", ".join(_reading(e.name, catalog.describe_state(e, state)) for e in chosen)
    else:
        probe = QuickCommand("up" if topic == "shade" else "on", command.room, command.prep, "shade" if topic == "shade" else "light")
        found, _ = targets(house, area, probe)
        if not found:
            return None
        wert = ", ".join(_reading(e.name, catalog.describe_state(e, state)) for e in found)
    return book.pick("state", lang, anrede=anrede, wert=wert, raum="")


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
    ("shade", "stop"): "shade_stop",
    ("shade", "position"): "shade_set",
    ("window", "open"): "window",
    ("window", "close"): "window",
    ("window", "stop"): "window",
    ("window", "position"): "window",
    ("slat", "slat"): "slat",
    ("climate", "temperature"): "climate",
    ("climate", "mode"): "climate",
    ("color", "color"): "color",
    ("white", "white"): "color",
}


def reply(command: QuickCommand, room_label: str | None, lang: str, anrede: str | None, ok: bool, book: "PhraseBook") -> str:
    if not ok:
        return book.pick("failed", lang, anrede=anrede)
    intent = INTENT[(command.kind, command.action)]
    if command.kind == "mood":
        wert = command.scene or ""
    elif command.kind == "climate" and command.action == "temperature":
        wert = f"{float(command.value):g} Grad" if lang != "en" else f"{float(command.value):g} degrees"
    elif command.kind == "climate":
        wert = {"automatik": "Automatik", "auto": "Automatik", "heizen": "Heizen", "kühlen": "Kühlen", "kuehlen": "Kühlen", "manuell": "Manuell"}.get(command.scene or "", command.scene or "")
    elif command.kind == "window" and command.action == "open":
        wert = "auf"
    elif command.kind == "window" and command.action == "close":
        wert = "zu"
    elif command.kind == "window" and command.action == "stop":
        wert = "an"
    elif command.kind in ("color", "white"):
        wert = command.scene or command.value or ""
    else:
        wert = command.scene if command.scene else (command.value or "")
    return book.pick(intent, lang, raum=_anrede_and_place(command, room_label, lang), anrede=anrede, wert=wert)
