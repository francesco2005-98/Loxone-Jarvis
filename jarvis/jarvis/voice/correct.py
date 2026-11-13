"""Fix words speech recognition heard slightly wrong, using the names of this house.

"Mach das Licht im Cafzimmer an" becomes "Mach das Licht im Schlafzimmer an"
when the house has a room called Schlafzimmer and nothing else is close.
Only words that are not themselves a known name are touched, and only when one
candidate is clearly nearer than every other.
"""

from __future__ import annotations

import re
import unicodedata

MIN_LENGTH = 5
MAX_DISTANCE = 2
MAX_DISTANCE_LONG = 3
MIN_SHARE = 0.6


def _fold(text: str) -> str:
    text = text.lower().replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _sound(text: str) -> str:
    """Soften the consonants Whisper most often drops: sch, sp and st."""
    return _fold(text).replace("sch", "s").replace("sp", "s").replace("st", "s")


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


def _known(house) -> list[tuple[str, str]]:
    """(folded, original spelling) for every name in the house."""
    raw: list[str] = []
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
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in raw:
        for word in re.findall(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*", name or ""):
            folded = _fold(word)
            if len(folded) >= MIN_LENGTH and folded not in seen:
                seen.add(folded)
                out.append((folded, word))
    return out


def correct(text: str, house) -> str:
    known = _known(house)
    if not known:
        return text
    known = [(_sound(name), original) for name, original in known]
    folded_names = {_fold(original) for _, original in known}

    def fix(match: re.Match) -> str:
        word = match.group(0)
        folded = _sound(word)
        if len(folded) < MIN_LENGTH or _fold(word) in folded_names:
            return word
        limit = MAX_DISTANCE_LONG if len(folded) >= 9 else MAX_DISTANCE
        ranked = sorted((_distance(folded, name, limit), name, original) for name, original in known)
        best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
        if best[0] > limit or best[0] / len(folded) > 1 - MIN_SHARE:
            return word
        if second is not None and second[0] <= best[0]:
            return word
        return best[2]

    return re.sub(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*", fix, text)
