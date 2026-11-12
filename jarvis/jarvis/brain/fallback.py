"""The four sentences that still work while Gemini is unreachable."""

from __future__ import annotations

import re
from dataclasses import dataclass

_LIGHT = r"\b(licht|lichter|lampe|lampen|beleuchtung|light|lights|lamp)\b"
_SHADE = r"\b(rollo|rollos|rollladen|rolladen|rollläden|jalousie|jalousien|beschattung|raffstore|blind|blinds|shutter|shutters)\b"
_ON = r"\b(an|ein|einschalten|anmachen|on)\b"
_OFF = r"\b(aus|ausschalten|ausmachen|off)\b"
_UP = r"\b(auf|hoch|rauf|öffnen|oeffnen|up|open|raise)\b"
_DOWN = r"\b(zu|runter|herunter|schließen|schliessen|down|close|lower)\b"


@dataclass(frozen=True)
class FallbackCommand:
    target: str  # "light" | "shade"
    action: str  # "on" | "off" | "up" | "down"


def _last(patterns: dict[str, str], text: str) -> str | None:
    best: tuple[int, str] | None = None
    for name, pattern in patterns.items():
        for match in re.finditer(pattern, text):
            if best is None or match.start() > best[0]:
                best = (match.start(), name)
    return best[1] if best else None


def parse(text: str) -> FallbackCommand | None:
    lowered = text.lower()
    if re.search(_LIGHT, lowered):
        action = _last({"on": _ON, "off": _OFF}, lowered)
        if action:
            return FallbackCommand("light", action)
    if re.search(_SHADE, lowered):
        action = _last({"up": _UP, "down": _DOWN}, lowered)
        if action:
            return FallbackCommand("shade", action)
    return None


def reply(command: FallbackCommand, lang: str, ok: bool) -> str:
    if not ok:
        return (
            "Das kann ich gerade nicht ausführen."
            if lang == "de"
            else "I can't do that right now."
        )
    de = {
        ("light", "on"): "Licht an.",
        ("light", "off"): "Licht aus.",
        ("shade", "up"): "Rollos fahren hoch.",
        ("shade", "down"): "Rollos fahren runter.",
    }
    en = {
        ("light", "on"): "Lights on.",
        ("light", "off"): "Lights off.",
        ("shade", "up"): "Raising the blinds.",
        ("shade", "down"): "Lowering the blinds.",
    }
    table = de if lang == "de" else en
    return table[(command.target, command.action)]


def offline_notice(lang: str) -> str:
    if lang == "de":
        return "Gemini ist gerade nicht erreichbar. Licht und Rollos gehen weiter."
    return "Gemini is unreachable at the moment. Lights and blinds still work."
