"""Spoken sentences for instant answers, in Jarvis' personality.

Instant answers skip Gemini, so their wording comes from a phrase book. Gemini
writes that book once from the configured personality and Jarvis picks a
different variant each time. Until it exists, or without Gemini, a built-in
butler-style book is used.

Placeholders: {raum} (e.g. "im Esszimmer", may be empty), {, anrede}
(", Sir" or empty), {wert}, {uhrzeit}, {datum}.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .gemini import Brain

log = logging.getLogger(__name__)

INTENTS = (
    "light_on", "light_off", "dim", "scene", "shade_up", "shade_down", "time", "date", "temperature", "failed", "offline",
    "offer_done", "offer_declined",
    "timer_set", "reminder_set", "timer_left", "timer_none", "timer_cancel", "timer_due", "timer_due_plain", "reminder_due",
)
REQUIRED = {
    "light_on": "{raum}", "light_off": "{raum}", "shade_up": "{raum}", "shade_down": "{raum}",
    "dim": "{wert}", "scene": "{wert}", "temperature": "{wert}", "time": "{uhrzeit}", "date": "{datum}",
    "timer_set": "{wert}", "reminder_set": "{wert}", "timer_left": "{wert}", "timer_due": "{wert}", "reminder_due": "{wert}",
}
PLACEHOLDERS = ("{raum}", "{, anrede}", "{wert}", "{uhrzeit}", "{datum}")
MIN_VARIANTS = 2

DEFAULT: dict[str, dict[str, list[str]]] = {
    "de": {
        "light_on": [
            "Sehr wohl{, anrede}. Licht {raum} ist an.",
            "Licht {raum} ist an{, anrede}.",
            "Erledigt{, anrede}. Es werde Licht {raum}.",
            "Wie gewünscht{, anrede}, das Licht {raum} brennt.",
        ],
        "light_off": [
            "Sehr wohl{, anrede}. Licht {raum} ist aus.",
            "Licht {raum} ist aus{, anrede}.",
            "Erledigt{, anrede}. Es ist jetzt dunkel {raum}.",
            "Wie gewünscht{, anrede}, das Licht {raum} ist gelöscht.",
        ],
        "dim": [
            "Licht {raum} auf {wert} Prozent{, anrede}.",
            "Sehr wohl{, anrede}. {wert} Prozent {raum}.",
            "Gedimmt auf {wert} Prozent {raum}{, anrede}.",
        ],
        "scene": [
            "Stimmung {wert} {raum}{, anrede}.",
            "Sehr wohl{, anrede}. {wert} {raum} ist aktiv.",
            "Wie gewünscht{, anrede}, Stimmung {wert}.",
        ],
        "shade_up": [
            "Die Rollos {raum} fahren hoch{, anrede}.",
            "Sehr wohl{, anrede}. Rollos {raum} öffnen sich.",
            "Etwas Tageslicht {raum}, kommt sofort{, anrede}.",
        ],
        "shade_down": [
            "Die Rollos {raum} fahren runter{, anrede}.",
            "Sehr wohl{, anrede}. Rollos {raum} schließen sich.",
            "Rollos {raum} gehen zu{, anrede}.",
        ],
        "time": [
            "Es ist {uhrzeit}{, anrede}.",
            "Wir haben {uhrzeit}{, anrede}.",
            "Gerade ist es {uhrzeit}{, anrede}.",
        ],
        "date": [
            "Heute ist {datum}{, anrede}.",
            "Wir haben {datum}{, anrede}.",
        ],
        "temperature": [
            "Es hat {wert} {raum}{, anrede}.",
            "Gerade sind es {wert} {raum}{, anrede}.",
            "Aktuell {wert} {raum}{, anrede}.",
        ],
        "failed": [
            "Verzeihung{, anrede}, das hat nicht geklappt.",
            "Das ließ sich leider nicht ausführen{, anrede}.",
        ],
        "offline": [
            "Verzeihung{, anrede}, Gemini ist gerade nicht erreichbar. Licht und Rollos gehen weiter.",
        ],
        "offer_done": [
            "Sehr wohl{, anrede}.",
            "Erledigt{, anrede}.",
            "Wie Sie wünschen{, anrede}.",
            "Ist veranlasst{, anrede}.",
        ],
        "offer_declined": [
            "Sehr wohl{, anrede}.",
            "Wie Sie meinen{, anrede}.",
            "Dann lasse ich es so{, anrede}.",
        ],
        "timer_set": [
            "Sehr wohl{, anrede}. Timer läuft, {wert}.",
            "Timer auf {wert} gestellt{, anrede}.",
            "In {wert} melde ich mich{, anrede}.",
        ],
        "reminder_set": [
            "Sehr wohl{, anrede}, ich erinnere Sie {wert}.",
            "Notiert{, anrede}. Ich melde mich {wert}.",
            "Ich denke daran{, anrede}, {wert}.",
        ],
        "timer_left": [
            "Noch {wert}{, anrede}.",
            "Der Timer läuft noch {wert}{, anrede}.",
        ],
        "timer_none": [
            "Es läuft gerade kein Timer{, anrede}.",
            "Derzeit ist kein Timer gestellt{, anrede}.",
        ],
        "timer_cancel": [
            "Timer gestoppt{, anrede}.",
            "Erledigt{, anrede}, der Timer ist abgebrochen.",
        ],
        "timer_due": [
            "Verzeihung{, anrede}, die Zeit für {wert} ist um.",
            "Ihr Timer für {wert} ist abgelaufen{, anrede}.",
        ],
        "timer_due_plain": [
            "Verzeihung{, anrede}, Ihr Timer ist abgelaufen.",
            "Die Zeit ist um{, anrede}.",
        ],
        "reminder_due": [
            "Erinnerung{, anrede}: {wert}.",
            "Sie wollten daran denken{, anrede}: {wert}.",
        ],
    },
    "en": {
        "light_on": ["Very good{, anrede}. Lights {raum} are on.", "Lights {raum} on{, anrede}.", "Done{, anrede}. Let there be light {raum}."],
        "light_off": ["Very good{, anrede}. Lights {raum} are off.", "Lights {raum} off{, anrede}.", "Done{, anrede}. Lights out {raum}."],
        "dim": ["Lights {raum} at {wert} percent{, anrede}.", "Dimmed to {wert} percent {raum}{, anrede}."],
        "shade_up": ["Raising the blinds {raum}{, anrede}.", "Blinds {raum} going up{, anrede}."],
        "shade_down": ["Lowering the blinds {raum}{, anrede}.", "Blinds {raum} going down{, anrede}."],
        "time": ["It is {uhrzeit}{, anrede}.", "The time is {uhrzeit}{, anrede}."],
        "date": ["Today is {datum}{, anrede}.", "It is {datum}{, anrede}."],
        "temperature": ["It is {wert} {raum}{, anrede}.", "Currently {wert} {raum}{, anrede}."],
        "failed": ["My apologies{, anrede}, that did not work.", "I'm afraid that failed{, anrede}."],
        "offline": ["My apologies{, anrede}, Gemini is unreachable. Lights and blinds still work."],
        "scene": ["Mood {wert} {raum}{, anrede}.", "Very good{, anrede}. {wert} is set."],
        "offer_done": ["Very good{, anrede}.", "Done{, anrede}.", "As you wish{, anrede}."],
        "offer_declined": ["Very well{, anrede}.", "As you wish{, anrede}.", "I'll leave it then{, anrede}."],
        "timer_set": ["Very good{, anrede}. Timer set for {wert}.", "I'll let you know in {wert}{, anrede}."],
        "reminder_set": ["Very good{, anrede}, I'll remind you {wert}.", "Noted{, anrede}. I'll remind you {wert}."],
        "timer_left": ["{wert} to go{, anrede}.", "The timer has {wert} left{, anrede}."],
        "timer_none": ["No timer is running{, anrede}.", "There is no timer set{, anrede}."],
        "timer_cancel": ["Timer cancelled{, anrede}.", "Done{, anrede}, the timer is off."],
        "timer_due": ["Pardon me{, anrede}, time is up for {wert}.", "Your {wert} timer is done{, anrede}."],
        "timer_due_plain": ["Pardon me{, anrede}, your timer is done.", "Time is up{, anrede}."],
        "reminder_due": ["A reminder{, anrede}: {wert}.", "You wanted me to remind you{, anrede}: {wert}."],
    },
}

GENERATE = """Formuliere Bestätigungssätze für einen Sprachassistenten, genau in deinem Stil und deiner Persönlichkeit.
Jede Situation bekommt 6 abwechslungsreiche, kurze Sätze (höchstens 12 Wörter), die gesprochen werden.
Platzhalter exakt so übernehmen:
- {raum}: Ortsangabe wie "im Esszimmer" oder leer. Muss in light_on, light_off, shade_up, shade_down vorkommen.
- {, anrede}: wird zu ", Sir" oder entfällt. Nur dort, wo eine Anrede mit Komma natürlich klingt.
- {wert}: Prozentzahl in dim, Name der Stimmung in scene, Temperatur in temperature; dort Pflicht. {uhrzeit}: Uhrzeit, Pflicht in time. {datum}: Datum, Pflicht in date.
Situationen: light_on (Licht an), light_off (Licht aus), dim (Licht gedimmt), scene (Lichtstimmung {wert} aktiv),
shade_up (Rollos hoch), shade_down (Rollos runter), time (Uhrzeit nennen), date (Datum nennen),
temperature (Temperatur {wert} nennen, {raum} wie "im Bad" oder "draußen"),
failed (Aktion fehlgeschlagen), offline (KI nicht erreichbar, Licht und Rollos funktionieren weiter),
offer_done (dein eigener Vorschlag wurde nach einem Ja ausgeführt, sehr kurz),
offer_declined (dein Vorschlag wurde abgelehnt, kurz und gelassen),
timer_set (Timer gestellt, {wert} = Dauer wie "eine Stunde"), reminder_set (Erinnerung notiert, {wert} = "in 20 Minuten" oder "um 18 Uhr"),
timer_left (Restzeit {wert} wie "42 Minuten für Essen"), timer_none (kein Timer läuft), timer_cancel (Timer gestoppt),
timer_due (Timer abgelaufen, {wert} = Name wie "Essen"), timer_due_plain (Timer abgelaufen, ohne Namen),
reminder_due (Erinnerung fällig, {wert} = Inhalt wie "die Wäsche").
Antworte nur mit JSON: {"de": {"light_on": [...], ...}, "en": {"light_on": [...], ...}}. "en" auf Englisch."""


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"^([.,]\s*)+", "", text).strip()
    return text[:1].upper() + text[1:] if text else text


def fill(template: str, *, raum: str = "", anrede: str | None = None, wert: Any = "", uhrzeit: str = "", datum: str = "") -> str:
    out = (
        template.replace("{, anrede}", f", {anrede}" if anrede else "")
        .replace("{raum}", raum)
        .replace("{wert}", str(wert))
        .replace("{uhrzeit}", uhrzeit)
        .replace("{datum}", datum)
    )
    return _clean(out)


def validate(data: Any) -> dict[str, dict[str, list[str]]]:
    """Keep only well-formed variants; an intent with too few falls back to the defaults."""
    book: dict[str, dict[str, list[str]]] = {}
    for lang in ("de", "en"):
        section = data.get(lang) if isinstance(data, dict) else None
        book[lang] = {}
        for intent in INTENTS:
            variants = section.get(intent) if isinstance(section, dict) else None
            good = []
            for text in variants if isinstance(variants, list) else []:
                if not isinstance(text, str) or not 3 <= len(text) <= 160:
                    continue
                if REQUIRED.get(intent) and REQUIRED[intent] not in text:
                    continue
                stray = re.findall(r"\{[^}]*\}", text)
                if any(token not in PLACEHOLDERS for token in stray):
                    continue
                good.append(text.strip())
            book[lang][intent] = good if len(good) >= MIN_VARIANTS else list(DEFAULT[lang][intent])
    return book


class PhraseBook:
    def __init__(self, path: Path):
        self.path = path
        self._book = DEFAULT
        self.persona_hash: str | None = None
        self.generated_at: float | None = None
        self._last: dict[tuple[str, str], str] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        self._book = validate(data.get("book"))
        self.persona_hash = data.get("persona_hash")
        self.generated_at = data.get("generated_at")

    @staticmethod
    def hash_of(persona: str) -> str:
        return hashlib.sha256(persona.strip().encode("utf-8")).hexdigest()[:16]

    def pick(self, intent: str, lang: str, **values: Any) -> str:
        variants = self._book.get("en" if lang == "en" else "de", {}).get(intent) or DEFAULT["de"][intent]
        key = (lang, intent)
        choices = [v for v in variants if v != self._last.get(key)] or variants
        template = random.choice(choices)
        self._last[key] = template
        return fill(template, **values)

    def examples(self, lang: str = "de") -> list[str]:
        book = self._book.get(lang, {})
        return [
            fill(book["light_on"][0], raum="im Esszimmer", anrede="Sir"),
            fill(book["shade_down"][0], raum="im Wohnzimmer", anrede="Sir"),
            fill(book["time"][0], uhrzeit="10 Uhr 30", anrede="Sir"),
        ]

    def current_for(self, persona: str) -> bool:
        return self.persona_hash == self.hash_of(persona)

    async def generate(self, brain: "Brain", persona: str) -> None:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=persona,
            response_mime_type="application/json",
            thinking_config=brain._thinking(),
            max_output_tokens=4096,
        )
        response = await brain.generate(
            [types.Content(role="user", parts=[types.Part.from_text(text=GENERATE)])], config
        )
        data = json.loads(response.text or "{}")
        self._book = validate(data)
        self.persona_hash = self.hash_of(persona)
        self.generated_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"book": self._book, "persona_hash": self.persona_hash, "generated_at": self.generated_at},
                ensure_ascii=False,
                indent=1,
            ),
            "utf-8",
        )
        log.info("Sofortantworten aus der Persönlichkeit neu formuliert")
