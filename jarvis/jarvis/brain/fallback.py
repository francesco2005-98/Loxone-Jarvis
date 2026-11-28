"""The four sentences that still work while Gemini is unreachable."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import quick


@dataclass(frozen=True)
class FallbackCommand:
    target: str  # "light" | "shade"
    action: str  # "on" | "off" | "up" | "down"


def parse(text: str) -> FallbackCommand | None:
    """The offline path only controls the current room."""
    if re.search(r"\b(?:nicht|kein\w*|außer|ausser|statt|not|except)\b|\?", text, re.I):
        return None
    command = quick.parse(text)
    if command and command.room is None and command.scope is None:
        if command.kind == "light" and command.action in ("on", "off"):
            return FallbackCommand("light", command.action)
        if command.kind == "shade" and command.action in ("up", "down"):
            return FallbackCommand("shade", command.action)
    if re.fullmatch(r"\s*(?:the )?lights? (on|off)[.! ]*", text, re.I):
        return FallbackCommand("light", "on" if "on" in text.lower() else "off")
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
