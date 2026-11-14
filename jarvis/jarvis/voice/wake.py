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
