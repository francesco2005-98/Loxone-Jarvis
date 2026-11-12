"""Spoken confirmation for alarm, lock, garage and Config changes.

Decided locally on the transcript, never by the language model.
"""

from __future__ import annotations

import re
from enum import Enum

_FILLER = {"jarvis", "bitte", "please", "sir", "ok", "okay", "dann", "then", "ja,"}
_YES = {
    ("ja",),
    ("jawohl",),
    ("yes",),
    ("yep",),
    ("mach", "das"),
    ("mach", "es"),
    ("do", "it"),
}
_NO_WORDS = {"nein", "no", "nicht", "abbrechen", "stopp", "stop", "cancel", "lieber", "warte", "wait", "don't", "dont"}


class Answer(str, Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


def _words(text: str) -> list[str]:
    cleaned = re.sub(r"[^\wäöüß' ]+", " ", text.lower())
    return [w for w in cleaned.split() if w]


def classify(text: str) -> Answer:
    words = _words(text)
    if not words:
        return Answer.UNCLEAR
    if any(w in _NO_WORDS for w in words):
        return Answer.NO
    core = tuple(w for w in words if w not in _FILLER)
    if not core and "ok" not in words and "okay" not in words:
        return Answer.UNCLEAR
    if core in _YES:
        return Answer.YES
    if core and core[0] == "ja" and len(core) <= 3:
        return Answer.YES
    if len(core) >= 2 and core[:2] == ("mach", "das") and len(core) <= 3:
        return Answer.YES
    return Answer.UNCLEAR
