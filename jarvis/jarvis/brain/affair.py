"""A requested scene that waits because something informative is still open.

The server owns the order, the hint and whether it already ran. Gemini only
chooses the words. Silence does not start the order, and a second transcript
does not start it again.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

_BEDTIME = re.compile(r"gute nacht|gute-nacht|schlaf|ins bett", re.IGNORECASE)
_OPEN_NAME = re.compile(r"tür|terrasse|balkon|garten|fenster", re.IGNORECASE)
_ACK = re.compile(
    r"\b(ich weiß|ich weiss|weiss ich|weiß ich|bleibt offen|bleibt heute offen|dann bleibt|"
    r"lass sie offen|lass die tür offen|ist mir bekannt|schon klar|mach trotzdem|trotzdem)\b",
    re.IGNORECASE,
)
_PAUSE = re.compile(r"\b(warte|moment|gleich|mache ich|mach ich|noch zu)\b", re.IGNORECASE)
_CANCEL = re.compile(r"\b(lass es|lass das|vergiss es|doch nicht|abbrechen|für heute nicht|lass es für heute|nicht mehr)\b", re.IGNORECASE)
_QUESTION = re.compile(r"\b(welche|warum|wieso|weshalb|regnet|wetter|wie wird|morgen|heute nacht)\b|\?", re.IGNORECASE)


@dataclass
class Affair:
    id: str
    scene: str
    entry_uuid: str
    hint: str
    person_id: str | None
    hint_spoken: bool = False
    executed: bool = False
    executing: bool = False
    paused: bool = False
    asks_to_close: bool = False
    line: str = ""

    def note(self) -> str:
        state = "pausiert" if self.paused else "wartet"
        heard = "Der Hinweis wurde gehört." if self.hint_spoken else "Der Hinweis wurde noch nicht abgespielt."
        close = " Zuletzt wurde ausdrücklich nach dem Schließen gefragt." if self.asks_to_close else ""
        return (
            f"Offener Auftrag ({state}): Szene „{self.scene}“, noch nicht ausgeführt. "
            f"Hinweis, nur zur Information, keine Sperre: {self.hint}. {heard}{close} "
            "Die Szene startet nicht von allein und nicht ein zweites Mal."
        )


def bedtime(scene: str) -> bool:
    return bool(_BEDTIME.search(scene or ""))


def open_hints(house) -> list[str]:
    """Open doors or windows worth mentioning. A closed one is not a hint."""
    hints: list[str] = []
    state = house.miniserver.state
    for entry in house.catalog.entries(only_allowed=True):
        if not _OPEN_NAME.search(entry.name or ""):
            continue
        if entry.type == "Window":
            position = state(entry.uuid, "position")
            try:
                open_ = float(position) > 0.02
            except (TypeError, ValueError):
                open_ = False
            if open_:
                hints.append(f"{entry.name} ist offen")
        elif entry.type == "WindowMonitor":
            count = state(entry.uuid, "numOpen")
            try:
                open_ = float(count) > 0
            except (TypeError, ValueError):
                open_ = False
            if open_:
                hints.append(f"{entry.name}: {int(float(count))} offen")
    return hints


def reaction(text: str) -> str:
    """What the words do to an open hint. Unclear stays with the conversation."""
    if _CANCEL.search(text or ""):
        return "cancel"
    if _PAUSE.search(text or ""):
        return "pause"
    if _QUESTION.search(text or ""):
        return "question"
    if re.search(r"\b(?:weiß|weiss) (?:ich )?nicht\b", text or "", re.I):
        return "unclear"
    words = (text or "").strip(" .!,")
    if _ACK.fullmatch(words) or re.fullmatch(
        r"(?:die|sie|die tür) bleibt (?:heute )?offen|(?:ich weiß|ich weiss),? wir sitzen noch draußen",
        words, re.I,
    ):
        return "ack"
    if re.fullmatch(r"(?:jetzt |bitte )?(?:weiter|fortsetzen|weitermachen|mach (?:jetzt )?weiter)", words, re.I):
        return "resume"
    return "unclear"


def new_affair(scene: str, entry_uuid: str, hint: str, person_id: str | None) -> Affair:
    return Affair(id=uuid.uuid4().hex[:8], scene=scene, entry_uuid=entry_uuid, hint=hint, person_id=person_id)
