"""Fix words speech recognition heard slightly wrong, using the names of this house.

"Mach das Licht im Cafzimmer an" becomes "Mach das Licht im Schlafzimmer an"
when the house has a room called Schlafzimmer and nothing else is close.
"Wohn Zimmer" becomes "Wohnzimmer". A word that already is a known name keeps
its ending, so "Rollos" stays plural. Everyday words such as "nicht" are
never rewritten, even when a device is called "Licht".
"""

from __future__ import annotations

import re
import unicodedata

MIN_LENGTH = 4
MAX_DISTANCE = 2
MAX_DISTANCE_LONG = 3
MIN_SHARE = 0.6
_PLURAL = {"s", "es", "en", "er", "e", "n"}
# Always worth hearing correctly, even when no device carries the name.
_LEXICON = ("Jarvis", "Spotify")
# Heard words that must stay, however close a room or device name is.
_KEEP = {
    "nicht", "nichts", "keine", "keiner", "keinem", "keinen", "kein",
    "doch", "schon", "bitte", "danke", "gerne", "klar", "jetzt", "heute",
    "morgen", "abend", "immer", "wieder", "noch", "auch", "einfach",
    "vielleicht", "etwas", "alles", "diese", "dieser", "dieses",
    "oder", "aber", "denn", "weil", "dass", "wenn", "dann", "hier", "dort",
    "oben", "unten", "links", "rechts", "mach", "mache", "schalt", "schalte",
    "fahr", "fahre", "stell", "stelle", "dimm", "dimme",
}
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*")


def _fold(text: str) -> str:
    text = text.lower().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _sound(text: str) -> str:
    """Soften the consonants and vowels Whisper most often swaps."""
    text = _fold(text)
    for source, target in (
        ("sch", "s"), ("sp", "s"), ("st", "s"),
        ("ck", "k"), ("ph", "f"), ("th", "t"), ("tz", "z"),
        ("ie", "i"), ("ai", "ei"), ("ay", "ei"), ("eu", "oi"),
    ):
        text = text.replace(source, target)
    return re.sub(r"(.)\1+", r"\1", text)


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance, cut off once it passes the limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        smallest = i
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            smallest = min(smallest, cur[-1])
        if smallest > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _names(house) -> list[str]:
    raw = []
    for area in house.areas.list():
        raw.append(area.name)
        raw.extend(area.rooms)
    for entry in house.catalog.entries(only_allowed=False):
        raw.append(entry.name)
        raw.extend(entry.aliases)
        if entry.room:
            raw.append(entry.room)
    for zone in house.miniserver.structure.zones():
        raw.append(zone.name)
        if zone.room:
            raw.append(zone.room)
    for person in house.people.list():
        raw.append(person["name"])
    raw.extend(_LEXICON)
    return raw


def _entries(house) -> list[tuple[str, str, str]]:
    """(sound, folded, original spelling) for every name in the house."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for name in _names(house):
        for word in _WORD.findall(name or ""):
            folded = _fold(word)
            if len(folded) >= MIN_LENGTH and folded not in seen:
                seen.add(folded)
                out.append((_sound(word), folded, word))
    return out


def _fix_token(word: str, entries: list[tuple[str, str, str]]) -> str:
    fold = _fold(word)
    if len(fold) < MIN_LENGTH or fold in _KEEP:
        return word
    exact = [original for _, folded, original in entries if folded == fold]
    if exact:
        for original in exact:
            if original == word:
                return word
        return exact[0]
    sound = _sound(word)
    limit = MAX_DISTANCE_LONG if len(sound) >= 9 else MAX_DISTANCE
    ranked = sorted((_distance(sound, sounded, limit), original) for sounded, _, original in entries)
    best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    if best[0] > limit or best[0] / len(sound) >= 1 - MIN_SHARE:
        return word
    if second is not None and second[0] <= best[0]:
        return word
    target = _fold(best[1])
    if fold.startswith(target) and fold[len(target):] in _PLURAL:
        return word
    return best[1]


def _join(first: str, second: str, entries: list[tuple[str, str, str]]) -> str | None:
    """'Wohn Zimmer' -> 'Wohnzimmer' when that room exists and neither half is a name of its own."""
    folds = {folded for _, folded, _ in entries}
    if _fold(first) in folds or _fold(second) in folds:
        return None
    joined = first + second
    if len(_fold(joined)) < MIN_LENGTH:
        return None
    fixed = _fix_token(joined, entries)
    if fixed == joined or _fold(fixed) not in folds:
        return None
    # Only glue the two words when together they really are that name.
    if _distance(_sound(joined), _sound(fixed), 1) > 1:
        return None
    return fixed


def correct(text: str, house) -> str:
    entries = _entries(house)
    if not entries:
        return text
    pieces = re.split(r"([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*)", text)
    index = 0
    out: list[str] = []
    while index < len(pieces):
        piece = pieces[index]
        if not _WORD.fullmatch(piece or ""):
            out.append(piece)
            index += 1
            continue
        if (
            index + 2 < len(pieces)
            and _WORD.fullmatch(pieces[index + 2] or "")
            and (joined := _join(piece, pieces[index + 2], entries))
        ):
            out.append(joined)
            index += 3
            continue
        out.append(_fix_token(piece, entries))
        index += 1
    return "".join(out)
