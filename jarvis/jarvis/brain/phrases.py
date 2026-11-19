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
    "light_on", "light_off", "dim", "scene", "shade_up", "shade_down", "shade_stop", "shade_set",
    "state", "window", "slat", "climate", "color", "music", "confirm_ask",
    "time", "date", "temperature", "failed", "offline",
    "offer_done", "offer_declined",
    "timer_set", "reminder_set", "timer_left", "timer_none", "timer_cancel", "timer_due", "timer_due_plain", "reminder_due",
)
REQUIRED = {
    "light_on": "{raum}", "light_off": "{raum}", "shade_up": "{raum}", "shade_down": "{raum}", "shade_stop": "{raum}",
    "dim": "{wert}", "scene": "{wert}", "shade_set": "{wert}", "state": "{wert}", "window": "{wert}",
    "slat": "{wert}", "climate": "{wert}", "color": "{wert}", "music": "{wert}", "confirm_ask": "{wert}",
    "temperature": "{wert}", "time": "{uhrzeit}", "date": "{datum}",
    "timer_set": "{wert}", "reminder_set": "{wert}", "timer_left": "{wert}", "timer_due": "{wert}", "reminder_due": "{wert}",
}
PLACEHOLDERS = ("{raum}", "{, anrede}", "{wert}", "{uhrzeit}", "{datum}")
MIN_VARIANTS = 2

DEFAULT: dict[str, dict[str, list[str]]] = {
    "de": {
        "light_on": [
            "Sehr wohl{, anrede}. Licht {raum} ist an.",
            "Erledigt{, anrede}. Es werde Licht {raum}.",
            "Wie gewünscht{, anrede}, das Licht {raum} brennt.",
            "Selbstverständlich{, anrede}. Helligkeit {raum}.",
            "Ist gemacht{, anrede}. Ich habe das Licht {raum} eingeschaltet.",
            "Einen Moment{, anrede}. Das Licht {raum} kommt.",
            "Natürlich{, anrede}. Es ist hell {raum}.",
            "Zu Befehl{, anrede}. Die Beleuchtung {raum} leuchtet.",
            "Geht klar{, anrede}. Die Lampen {raum} sind an.",
            "Aber gern{, anrede}. Ich sorge für Helligkeit {raum}.",
            "Schon geschehen{, anrede}, es brennt {raum}.",
            "Wie Sie wünschen{, anrede}. Das Licht {raum} habe ich angemacht.",
            "Sofort{, anrede}. Die Beleuchtung {raum} ist eingeschaltet.",
            "Mache ich{, anrede}. Das Licht {raum} geht an.",
            "Notiert{, anrede}. Es leuchtet {raum}.",
            "Kommt sofort{, anrede}. Ich schalte das Licht {raum} ein.",
        ],
        "light_off": [
            "Sehr wohl{, anrede}. Licht {raum} ist aus.",
            "Erledigt{, anrede}. Es ist jetzt dunkel {raum}.",
            "Wie gewünscht{, anrede}, das Licht {raum} ist gelöscht.",
            "Selbstverständlich{, anrede}. Es wird dunkel {raum}.",
            "Ist gemacht{, anrede}. Ich habe das Licht {raum} ausgeschaltet.",
            "Wie Sie wünschen{, anrede}. Das Licht {raum} bleibt aus.",
            "Natürlich{, anrede}. Es ist dunkel {raum}.",
            "Zu Befehl{, anrede}. Die Beleuchtung {raum} ist aus.",
            "Geht klar{, anrede}. Die Lampen {raum} sind aus.",
            "Schon geschehen{, anrede}, und es ist dunkel {raum}.",
            "Sofort{, anrede}. Ich lösche das Licht {raum}.",
            "Mache ich{, anrede}. Das Licht {raum} geht aus.",
            "Notiert{, anrede}. Das Licht {raum} ist ausgeschaltet.",
            "Aber gern{, anrede}. Ich mache es dunkel {raum}.",
            "Einen Moment{, anrede}. Das Licht {raum} erlischt.",
            "Kommt sofort{, anrede}. Ich schalte das Licht {raum} aus.",
        ],
        "dim": [
            "Licht {raum} auf {wert} Prozent{, anrede}.",
            "Sehr wohl{, anrede}. {wert} Prozent {raum}.",
            "Gedimmt auf {wert} Prozent {raum}{, anrede}.",
            "Helligkeit {raum}: {wert} Prozent{, anrede}.",
            "Selbstverständlich{, anrede}. {wert} Prozent {raum}.",
            "Ist eingestellt{, anrede}. {wert} Prozent {raum}.",
            "Wie gewünscht{, anrede}, das Licht {raum} steht auf {wert} Prozent.",
            "Ich dimme auf {wert} Prozent {raum}{, anrede}.",
            "Helligkeit angepasst{, anrede}: {wert} Prozent {raum}.",
            "So sei es{, anrede}. {wert} Prozent {raum}.",
            "Gemacht{, anrede}. Das Licht {raum} auf {wert} Prozent.",
            "Notiert{, anrede}. {wert} Prozent Helligkeit {raum}.",
        ],
        "scene": [
            "Stimmung {wert} {raum}{, anrede}.",
            "Sehr wohl{, anrede}. {wert} {raum} ist aktiv.",
            "Wie gewünscht{, anrede}, Stimmung {wert}.",
            "Ich lege {wert} {raum} auf{, anrede}.",
            "Atmosphäre {wert}{, anrede}.",
            "Szene {wert} läuft {raum}{, anrede}.",
            "Gemacht{, anrede}. {wert} {raum}.",
            "Selbstverständlich{, anrede}. {wert} ist gewählt.",
        ],
        "shade_up": [
            "Die Rollos {raum} fahren hoch{, anrede}.",
            "Sehr wohl{, anrede}. Rollos {raum} öffnen sich.",
            "Etwas Tageslicht {raum}, kommt sofort{, anrede}.",
            "Selbstverständlich{, anrede}. Rollos {raum} gehen auf.",
            "Ist veranlasst{, anrede}. Die Rollos {raum} öffnen sich.",
            "Wie gewünscht{, anrede}, mehr Licht {raum}.",
            "Ich fahre die Rollos {raum} hoch{, anrede}.",
            "Geht klar{, anrede}. Beschattung {raum} öffnet sich.",
            "Schon unterwegs{, anrede}. Die Rollos {raum} gehen auf.",
            "Zu Befehl{, anrede}. Hoch mit den Rollos {raum}.",
            "Tageslicht kommt {raum}{, anrede}.",
            "Mache ich{, anrede}. Die Rollos {raum} geben den Blick frei.",
        ],
        "shade_down": [
            "Die Rollos {raum} fahren runter{, anrede}.",
            "Sehr wohl{, anrede}. Rollos {raum} schließen sich.",
            "Rollos {raum} gehen zu{, anrede}.",
            "Selbstverständlich{, anrede}. Die Rollos {raum} fahren zu.",
            "Ist veranlasst{, anrede}. Schatten {raum}.",
            "Wie gewünscht{, anrede}, die Rollos {raum} schließen sich.",
            "Ich lasse die Rollos {raum} herunter{, anrede}.",
            "Geht klar{, anrede}. Beschattung {raum} fährt zu.",
            "Schon unterwegs{, anrede}. Die Rollos {raum} gehen runter.",
            "Zu Befehl{, anrede}. Zu mit den Rollos {raum}.",
            "Schatten kommt {raum}{, anrede}.",
            "Mache ich{, anrede}. Die Rollos {raum} schließen.",
        ],
        "shade_stop": [
            "Die Rollos {raum} halten an{, anrede}.",
            "Sehr wohl{, anrede}. Rollos {raum} gestoppt.",
            "Angehalten {raum}{, anrede}.",
            "Stopp {raum}{, anrede}. Die Rollos bleiben, wo sie sind.",
            "Ich halte die Rollos {raum} an{, anrede}.",
            "Schon gestoppt {raum}{, anrede}.",
            "Geht klar{, anrede}. Beschattung {raum} steht.",
            "Zu Befehl{, anrede}. Die Rollos {raum} halten.",
        ],
        "shade_set": [
            "Rollos {raum} auf {wert} Prozent{, anrede}.",
            "Sehr wohl{, anrede}. {wert} Prozent {raum}.",
            "Position {wert} Prozent {raum}{, anrede}.",
            "Ich fahre die Rollos {raum} auf {wert} Prozent{, anrede}.",
            "Beschattung {raum}: {wert} Prozent{, anrede}.",
            "Wie gewünscht{, anrede}, {wert} Prozent {raum}.",
            "Gemacht{, anrede}. Rollos {raum} auf {wert} Prozent.",
            "So sei es{, anrede}. {wert} Prozent {raum}.",
        ],
        "time": [
            "Es ist {uhrzeit}{, anrede}.",
            "Wir haben {uhrzeit}{, anrede}.",
            "Gerade ist es {uhrzeit}{, anrede}.",
            "Die Uhr zeigt {uhrzeit}{, anrede}.",
            "Nach meiner Uhr ist es {uhrzeit}{, anrede}.",
            "Punkt {uhrzeit}{, anrede}.",
            "Ich lese {uhrzeit}{, anrede}.",
            "Aktuell ist es {uhrzeit}{, anrede}.",
        ],
        "date": [
            "Heute ist {datum}{, anrede}.",
            "Wir haben {datum}{, anrede}.",
            "Heute schreiben wir den {datum}{, anrede}.",
            "Der heutige Tag ist {datum}{, anrede}.",
            "Laut Kalender ist {datum}{, anrede}.",
            "Heute: {datum}{, anrede}.",
        ],
        "temperature": [
            "Es hat {wert} {raum}{, anrede}.",
            "Gerade sind es {wert} {raum}{, anrede}.",
            "Aktuell {wert} {raum}{, anrede}.",
            "Ich messe {wert} {raum}{, anrede}.",
            "Das Thermometer sagt {wert} {raum}{, anrede}.",
            "Die Temperatur liegt bei {wert} {raum}{, anrede}.",
            "Hier die Temperatur{, anrede}: {wert} {raum}.",
            "Soweit ich sehe, {wert} {raum}{, anrede}.",
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
        "state": [
            "{wert}{, anrede}.",
            "Gerade gilt{, anrede}: {wert}.",
            "Ich sehe{, anrede}, {wert}.",
            "Nach dem Stand{, anrede}: {wert}.",
            "Kurz gesagt{, anrede}, {wert}.",
            "So ist die Lage{, anrede}. {wert}.",
            "Soweit ich das sehe{, anrede}, {wert}.",
            "Das kann ich sagen{, anrede}: {wert}.",
            "Aktuell{, anrede}: {wert}.",
            "Hier die Auskunft{, anrede}. {wert}.",
            "Moment{, anrede}. {wert}.",
            "Wenn ich hinsehe{, anrede}: {wert}.",
        ],
        "window": [
            "Das Fenster {raum} geht {wert}{, anrede}.",
            "Sehr wohl{, anrede}. Fenster {raum} {wert}.",
            "Ist veranlasst{, anrede}. Fenster {raum} {wert}.",
            "Ich fahre das Fenster {raum} {wert}{, anrede}.",
            "Wie gewünscht{, anrede}, Fenster {raum} {wert}.",
            "Geht klar{, anrede}. Das Fenster {raum}: {wert}.",
            "Schon unterwegs{, anrede}. Fenster {raum} {wert}.",
            "Zu Befehl{, anrede}. Fenster {raum} {wert}.",
        ],
        "slat": [
            "Lamellen {raum} auf {wert} Prozent{, anrede}.",
            "Sehr wohl{, anrede}. Lamellen {wert} Prozent {raum}.",
            "Ich stelle die Lamellen {raum} auf {wert} Prozent{, anrede}.",
            "Lamellenstellung {wert} Prozent {raum}{, anrede}.",
            "Wie gewünscht{, anrede}, Lamellen {raum} auf {wert} Prozent.",
            "Gemacht{, anrede}. {wert} Prozent an den Lamellen {raum}.",
        ],
        "climate": [
            "Die Heizung {raum} steht auf {wert}{, anrede}.",
            "Sehr wohl{, anrede}. {wert} {raum}.",
            "Ist eingestellt{, anrede}. Heizung {raum} auf {wert}.",
            "Wie gewünscht{, anrede}, {wert} {raum}.",
            "Der Regler {raum} steht auf {wert}{, anrede}.",
            "Gemacht{, anrede}. {wert} für die Heizung {raum}.",
            "Selbstverständlich{, anrede}. Heizung {raum}: {wert}.",
            "Ich stelle die Heizung {raum} auf {wert}{, anrede}.",
            "Temperaturwunsch notiert{, anrede}: {wert} {raum}.",
            "So sei es{, anrede}. {wert} {raum}.",
            "Geht klar{, anrede}. {wert} {raum}.",
            "Zu Befehl{, anrede}. Der Raumregler {raum} steht auf {wert}.",
        ],
        "color": [
            "Das Licht {raum} wird {wert}{, anrede}.",
            "Sehr wohl{, anrede}. {wert} {raum}.",
            "Farbe {wert} {raum}{, anrede}.",
            "Ich färbe das Licht {raum} {wert}{, anrede}.",
            "Wie gewünscht{, anrede}, {wert} {raum}.",
            "Farbton {wert}{, anrede}.",
            "Gemacht{, anrede}. Das Licht {raum} in {wert}.",
            "Selbstverständlich{, anrede}. {wert} {raum}.",
        ],
        "music": [
            "Sehr wohl{, anrede}. {wert}.",
            "Musik: {wert}{, anrede}.",
            "Ist veranlasst{, anrede}. {wert}.",
            "Wie gewünscht{, anrede}, {wert}.",
            "Selbstverständlich{, anrede}. {wert}.",
            "Mache ich{, anrede}. {wert}.",
            "Kommt sofort{, anrede}: {wert}.",
            "Erledigt{, anrede}. {wert}.",
            "Geht klar{, anrede}. {wert}.",
            "Zu Befehl{, anrede}. {wert}.",
            "Schon geschehen{, anrede}. {wert}.",
            "Ich kümmere mich darum{, anrede}: {wert}.",
        ],
        "confirm_ask": [
            "Soll ich {wert}{, anrede}?",
            "Darf ich {wert}{, anrede}?",
            "Wirklich {wert}{, anrede}?",
            "Möchten Sie, dass ich {wert}{, anrede}?",
            "Wollen Sie, dass ich {wert}{, anrede}?",
            "Ein Ja genügt{, anrede}, dann werde ich {wert}?",
            "Soll das so sein{, anrede}: {wert}?",
            "Ich würde {wert}{, anrede}. Einverstanden?",
        ],
    },
    "en": {
        "light_on": [
            "Very good{, anrede}. Lights {raum} are on.",
            "Lights {raum} on{, anrede}.",
            "Done{, anrede}. Let there be light {raum}.",
            "Certainly{, anrede}. The lights {raum} are lit.",
            "As you wish{, anrede}. Switching the lights {raum} on.",
            "Right away{, anrede}. Brightness {raum}.",
            "Consider it done{, anrede}. Lights {raum} coming on.",
            "Of course{, anrede}. It is bright {raum}.",
        ],
        "light_off": [
            "Very good{, anrede}. Lights {raum} are off.",
            "Lights {raum} off{, anrede}.",
            "Done{, anrede}. Lights out {raum}.",
            "Certainly{, anrede}. The lights {raum} are dark.",
            "As you wish{, anrede}. Switching the lights {raum} off.",
            "Right away{, anrede}. Darkness {raum}.",
            "Consider it done{, anrede}. Lights {raum} going out.",
            "Of course{, anrede}. It is dark {raum}.",
        ],
        "dim": ["Lights {raum} at {wert} percent{, anrede}.", "Dimmed to {wert} percent {raum}{, anrede}."],
        "shade_up": ["Raising the blinds {raum}{, anrede}.", "Blinds {raum} going up{, anrede}.", "Very good{, anrede}. Opening the blinds {raum}."],
        "shade_down": ["Lowering the blinds {raum}{, anrede}.", "Blinds {raum} going down{, anrede}.", "Very good{, anrede}. Closing the blinds {raum}."],
        "shade_stop": ["Stopping the blinds {raum}{, anrede}.", "Blinds {raum} stopped{, anrede}."],
        "shade_set": ["Blinds {raum} at {wert} percent{, anrede}.", "Very good{, anrede}. {wert} percent {raum}."],
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
        "state": [
            "{wert}{, anrede}.",
            "Right now: {wert}{, anrede}.",
            "I see {wert}{, anrede}.",
            "At the moment{, anrede}: {wert}.",
            "As it stands{, anrede}, {wert}.",
            "Here is the reading{, anrede}: {wert}.",
            "Currently{, anrede}: {wert}.",
            "From what I can tell{, anrede}, {wert}.",
        ],
        "window": ["The window {raum} is going {wert}{, anrede}.", "Very good{, anrede}. Window {raum} {wert}."],
        "slat": ["Slats {raum} at {wert} percent{, anrede}.", "Very good{, anrede}. Slats at {wert} percent."],
        "climate": ["Heating {raum} set to {wert}{, anrede}.", "Very good{, anrede}. {wert} {raum}."],
        "color": ["The light {raum} turns {wert}{, anrede}.", "Very good{, anrede}. {wert} {raum}."],
        "music": ["Very good{, anrede}. {wert}.", "Music {wert}{, anrede}."],
        "confirm_ask": ["Shall I {wert}{, anrede}?", "May I {wert}{, anrede}?"],
    },
}

GENERATE = """Formuliere Bestätigungssätze für einen Sprachassistenten, genau in deinem Stil und deiner Persönlichkeit.
Jede Situation bekommt 8 kurze Sätze (höchstens 12 Wörter), die gesprochen werden.
Die 8 Sätze einer Situation müssen sich in der Satzform unterscheiden. Nicht denselben Kernsatz mit anderem Vorspann wiederholen, also nicht mehrfach „Das Licht ist an“ oder „Sehr wohl, das Licht ist an“.
Platzhalter exakt so übernehmen:
- {raum}: Ortsangabe wie "im Esszimmer" oder leer. Muss in light_on, light_off, shade_up, shade_down vorkommen.
- {, anrede}: wird zu ", Sir" oder entfällt. Nur dort, wo eine Anrede mit Komma natürlich klingt.
- {wert}: Prozentzahl in dim, Name der Stimmung in scene, Temperatur in temperature; dort Pflicht. {uhrzeit}: Uhrzeit, Pflicht in time. {datum}: Datum, Pflicht in date.
Situationen: light_on (Licht an), light_off (Licht aus), dim (Licht gedimmt), scene (Lichtstimmung {wert} aktiv),
shade_up (Rollos hoch), shade_down (Rollos runter), shade_stop (Rollos angehalten, {raum} Pflicht),
shade_set (Rollos auf {wert} Prozent, {wert} und {raum} Pflicht),
state (Zustand vorlesen, {wert} ist der ganze Stand), window (Fenster, {wert} ist auf, zu oder eine Stellung),
slat (Lamellen auf {wert} Prozent), climate (Heizung auf {wert}), color (Lichtfarbe {wert}),
music ({wert} ist pausiert, der Titel oder lauter), confirm_ask (Ja/Nein-Frage, {wert} ist die Handlung),
time (Uhrzeit nennen), date (Datum nennen),
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
        self._recent: dict[tuple[str, str], list[str]] = {}
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
        lang_key = "en" if lang == "en" else "de"
        active = self._book.get(lang_key, {}).get(intent) or []
        base = DEFAULT.get(lang_key, DEFAULT["de"]).get(intent) or DEFAULT["de"][intent]
        variants = list(dict.fromkeys([*active, *base])) or list(base)
        key = (lang_key, intent)
        recent = self._recent.setdefault(key, [])
        choices = [v for v in variants if v not in recent] or variants
        template = random.choice(choices)
        recent.append(template)
        del recent[: max(0, len(recent) - max(1, len(variants) - 1))]
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
