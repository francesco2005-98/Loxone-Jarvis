"""English words a German Piper voice would otherwise read letter by letter.

The markers are espeak phonemes taken from an English voice. Piper speaks them
as written and keeps the surrounding sentence German.
"""

from __future__ import annotations

import re

# Whole words only. "Mam" is the form the interface shows, "Ma'am" the spoken one.
_ENGLISH = {
    "sir": "sˈɜː",
    "ma'am": "mˈæm",
    "mam": "mˈæm",
    "spotify": "spˈɒtɪfˌaɪ",
}

_WORD = re.compile(r"[A-Za-zÄÖÜäöüß']+")


def pronounce(text: str, lang: str) -> str:
    if lang == "en" or "[[" in text:
        return text

    def replace(match: re.Match) -> str:
        phonemes = _ENGLISH.get(match.group(0).lower())
        return f"[[{phonemes}]]" if phonemes else match.group(0)

    return _WORD.sub(replace, text)
