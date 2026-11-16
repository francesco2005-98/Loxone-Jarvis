"""The spellings speech recognition produces for "Jarvis"."""

from __future__ import annotations

import re

# Known spellings, then the shape of the near misses Kroko writes ("Javas", "Jervus",
# "Jahwest", "Dervis"): J, Dsch, Tsch or D, a vowel, maybe r/l/h, v or w, a vowel, an s sound.
WAKE_WORD = (
    r"(?:jarvis|jarviz|jarwis|jarvi|jarves|javis|jervis|dschawis|dschavis|dschaavis|tschawis|tschavis|chavis"
    r"|ja,? wis"
    r"|(?:j|dsch|tsch|d)[aeiouä]{1,2}[rlh]?[vw][aeiou]{1,2}(?:s|ss|ce|st|z))"
)
WAKE = re.compile(rf"\b{WAKE_WORD}\b", re.IGNORECASE)
WAKE_LEAD = 24


def split_wake(text: str) -> tuple[bool, str]:
    """(woke, rest): the wake word near the start of the sentence and what follows it."""
    match = WAKE.search(text or "")
    if match is None or match.start() > WAKE_LEAD:
        return False, ""
    rest = text[match.end() :].strip(" \t,.;:!?-–—\"'„“")
    return True, rest


def has_words(text: str) -> bool:
    return sum(ch.isalpha() for ch in text or "") >= 2
