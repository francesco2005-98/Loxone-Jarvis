"""German (and simple English) durations and clock times, for timers and reminders without Gemini."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_ONES = {
    "null": 0, "ein": 1, "eine": 1, "einen": 1, "einer": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
    "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13,
    "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}
_TENS = {"zwanzig": 20, "dreißig": 30, "dreissig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60}
_FRACTIONS = {"anderthalb": 1.5, "eineinhalb": 1.5, "halb": 0.5, "halbe": 0.5, "halben": 0.5, "half": 0.5}
UNITS = {
    "sekunde": 1, "sekunden": 1, "sek": 1, "second": 1, "seconds": 1,
    "minute": 60, "minuten": 60, "min": 60, "minutes": 60,
    "stunde": 3600, "stunden": 3600, "std": 3600, "hour": 3600, "hours": 3600,
}
_FILLER = {"und", "in", "für", "auf", "von", "ca", "circa", "etwa", "ungefähr", "so", "and", "for"}
MAX_SECONDS = 24 * 3600


def word_number(word: str) -> float | None:
    if re.fullmatch(r"\d+(?:[.,]\d+)?", word):
        return float(word.replace(",", "."))
    if word in _ONES:
        return float(_ONES[word])
    if word in _TENS:
        return float(_TENS[word])
    if word in _FRACTIONS:
        return _FRACTIONS[word]
    match = re.fullmatch(r"(\w+?)und(zwanzig|dreißig|dreissig|vierzig|fünfzig)", word)
    if match and match.group(1) in _ONES:
        return float(_ONES[match.group(1)] + _TENS[match.group(2)])
    return None


def parse_duration(text: str) -> int | None:
    """'1 Stunde', 'eine halbe Stunde', 'anderthalb Stunden', '1 Stunde und 30 Minuten', '1 Stunde 30' -> seconds."""
    t = f" {text.lower().strip()} "
    t = re.sub(r"\b(?:eine |einer )?dreiviertelstunde\b", " 45 minuten ", t)
    t = re.sub(r"\b(?:eine |einer )?viertelstunde\b", " 15 minuten ", t)
    t = re.sub(r"\b(?:eine |einer )?halben? stunde\b", " 30 minuten ", t)
    t = re.sub(r"\b(stunden?) und (?:eine )?halbe?\b", r"\1 30 minuten", t)
    t = re.sub(r"\ban hour and a half\b", " 90 minutes ", t)
    tokens = re.findall(r"\d+(?:[.,]\d+)?|[a-zäöüß]+", t)
    total = 0.0
    pending: float | None = None
    last_unit = 0
    for token in tokens:
        number = word_number(token)
        if number is not None:
            if pending is not None and pending == 1 and number == 0.5:
                pending = 1.5
                continue
            if pending is not None:
                return None
            pending = number
        elif token in UNITS:
            total += (pending if pending is not None else 1.0) * UNITS[token]
            last_unit = UNITS[token]
            pending = None
        elif token in _FILLER:
            continue
        else:
            return None
    if pending is not None:
        if last_unit == 3600 and pending < 60:
            total += pending * 60
        elif last_unit == 60 and pending < 60:
            total += pending
        else:
            return None
    if not last_unit or total <= 0 or total > MAX_SECONDS:
        return None
    return int(round(total))


def parse_clock(text: str, now: datetime) -> datetime | None:
    """'18 Uhr', '18:30', '18 Uhr 30', 'halb sieben', '7' -> next such moment after now (house time zone)."""
    t = text.lower().strip().removeprefix("um ").strip()
    hour = minute = None
    exact = False
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(?: uhr)?(?: (\d{1,2}))?", t)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or match.group(3) or 0)
        exact = bool(match.group(2)) or hour >= 13
    else:
        match = re.fullmatch(r"halb (\w+)", t)
        number = word_number(match.group(1)) if match else None
        if number is not None:
            hour, minute = int(number) - 1, 30
        else:
            match = re.fullmatch(r"(\w+)(?: uhr)?(?: (\w+))?", t)
            if match and word_number(match.group(1)) is not None:
                hour = int(word_number(match.group(1)) or 0)
                minute = int(word_number(match.group(2)) or 0) if match.group(2) else 0
    if hour is None or minute is None or not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    hour %= 24
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now and hour < 12 and not exact:
        later = candidate + timedelta(hours=12)
        if later > now:
            return later
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def human_duration(seconds: int, lang: str = "de") -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if lang == "en":
        parts = []
        if hours:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if secs and not hours:
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")
        return " and ".join(parts) or "a moment"
    if hours == 0 and minutes == 30 and not secs:
        return "eine halbe Stunde"
    if hours == 1 and minutes == 30 and not secs:
        return "anderthalb Stunden"
    parts = []
    if hours:
        parts.append("eine Stunde" if hours == 1 else f"{hours} Stunden")
    if minutes:
        parts.append("eine Minute" if minutes == 1 else f"{minutes} Minuten")
    if secs and not hours:
        parts.append("eine Sekunde" if secs == 1 else f"{secs} Sekunden")
    return " und ".join(parts) or "einen Moment"


def human_clock(moment: datetime, lang: str = "de") -> str:
    if lang == "en":
        return f"{moment:%H:%M}"
    return f"{moment.hour} Uhr" if moment.minute == 0 else f"{moment.hour} Uhr {moment.minute:02d}"
