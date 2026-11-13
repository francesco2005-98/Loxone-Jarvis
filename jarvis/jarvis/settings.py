"""Household settings stored as JSON values in SQLite."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import Database, dumps, loads

DEFAULT_MODEL = "gemini-3.8-flash"

DEFAULT_PERSONA = (
    "Du bist Jarvis, der Hausassistent dieses Hauses. Du sprichst ruhig, trocken und knapp, "
    "mit feinem britischem Humor, nie albern und nie unterwürfig. Antworte in der Sprache, in der "
    "du angesprochen wirst. Deine Antworten werden über Lautsprecher gesprochen: ein bis zwei kurze "
    "Sätze, keine Aufzählungen, kein Markdown, keine Emojis, keine Abkürzungen, die man nicht "
    "aussprechen kann. Bestätige Schaltungen in wenigen Worten."
)


@dataclass(frozen=True)
class Field:
    default: Any
    validate: Callable[[Any], Any]


def _str(max_len: int = 200, pattern: str | None = None) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise ValueError("Text erwartet")
        value = value.strip()
        if len(value) > max_len:
            raise ValueError(f"höchstens {max_len} Zeichen")
        if pattern and value and not re.fullmatch(pattern, value):
            raise ValueError("Format ungültig")
        return value

    return check


def _int(lo: int, hi: int) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("Zahl erwartet")
        number = int(value)
        if not lo <= number <= hi:
            raise ValueError(f"zwischen {lo} und {hi}")
        return number

    return check


def _float(lo: float, hi: float) -> Callable[[Any], float]:
    def check(value: Any) -> float:
        number = float(value)
        if not lo <= number <= hi:
            raise ValueError(f"zwischen {lo} und {hi}")
        return round(number, 2)

    return check


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError("Ja/Nein erwartet")


def _choice(*options: str) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value not in options:
            raise ValueError("unbekannte Auswahl")
        return value

    return check


def _timezone(value: Any) -> str:
    value = _str(64)(value)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unbekannte Zeitzone") from exc
    return value


HOST = r"[A-Za-z0-9.\-:\[\]]+"
HOSTS = rf"{HOST}(?:[\s,;]+{HOST})*"
MODEL = r"[a-z0-9][a-z0-9.\-]{2,80}"
VOICE = r"[a-z]{2}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)(#[A-Za-z0-9_]+)?"

FIELDS: dict[str, Field] = {
    "gemini.model": Field(DEFAULT_MODEL, _str(80, MODEL)),
    "gemini.system_prompt": Field(DEFAULT_PERSONA, _str(4000)),
    "voice.de": Field("de_DE-thorsten-high", _str(80, VOICE)),
    "voice.en": Field("en_GB-alan-medium", _str(80, VOICE)),
    "voice.speed": Field(1.0, _float(0.7, 1.4)),
    "stt.model": Field("small", _choice("small", "base")),
    "stt.language": Field("auto", _choice("auto", "de", "en")),
    "mic.noise_filter": Field(2, _int(0, 4)),
    "mic.sensitivity": Field(50, _int(0, 100)),
    "announce.volume": Field(0, _int(0, 100)),
    "announce.silence_ms": Field(600, _int(0, 2000)),
    "house.timezone": Field("Europe/Berlin", _timezone),
    "house.notes": Field("", _str(4000)),
    "loxone.host": Field("", _str(120, HOST)),
    "loxone.user": Field("jarvis", _str(64, r"[^/\s]+")),
    "loxone.tls": Field(False, _bool),
    "audioserver.host": Field("", _str(400, HOSTS)),
    "mcp.url": Field("", _str(300, r"https?://[^\s]+")),
    "mics.wifi_ssid": Field("", _str(32)),
    "mics.jarvis_address": Field("", _str(120, HOST)),
    "log.retention_days": Field(3, _int(1, 30)),
}

SECRET_KEYS = {
    "gemini.api_key",
    "loxone.password",
    "mics.wifi_password",
}


class Settings:
    def __init__(self, db: Database):
        self._db = db
        self._cache: dict[str, Any] = {}
        for row in db.all("SELECT key, value FROM settings"):
            self._cache[row["key"]] = loads(row["value"])
        self._listeners: list[Callable[[set[str]], None]] = []

    def get(self, key: str) -> Any:
        if key in self._cache:
            return self._cache[key]
        if key in FIELDS:
            return FIELDS[key].default
        raise KeyError(key)

    def all(self) -> dict[str, Any]:
        return {key: self.get(key) for key in FIELDS}

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key, value in values.items():
            field = FIELDS.get(key)
            if field is None:
                errors[key] = "unbekannte Einstellung"
                continue
            try:
                clean[key] = field.validate(value)
            except (ValueError, TypeError) as exc:
                errors[key] = str(exc)
        if errors:
            raise SettingsError(errors)
        for key, value in clean.items():
            self._db.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, dumps(value)),
            )
            self._cache[key] = value
        changed = set(clean)
        for listener in list(self._listeners):
            listener(changed)
        return clean

    def on_change(self, listener: Callable[[set[str]], None]) -> None:
        self._listeners.append(listener)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.get("house.timezone"))


class SettingsError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors
