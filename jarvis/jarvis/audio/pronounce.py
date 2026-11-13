"""English words a German Piper voice would otherwise read by German spelling.

The markers are espeak phonemes. Piper speaks exactly those sounds and keeps
the surrounding sentence German, so "Live" is "Leif" and "Jazz" is
"Dschäss" instead of "Liewe" and "Jats".

German words stay German even when the spelling looks English: Computer,
Radio, Video, Pause, Timer, Status, Code, Bass, Stereo, Start, Stop, links,
Funk, Brand. Piper already says many loanwords correctly (Team, Job, Bluetooth,
Netflix, Update), and those are not listed. USB, LED and the other short
names use the German letter names.
"""

from __future__ import annotations

import re

# Whole words only. Plurals ("Apps", "Meetings", "Storys") and the possessive
# "Sir's" add the plural sound inside the marker. "Tipps" is not "tip",
# and "links" is not "link".
_ENGLISH: dict[str, str] = {
    # Anrede
    'sir':   'sˈɜː',
    "ma'am": 'mˈæm',
    'mam':   'mˈæm',
    # Name
    'jarvis': 'dʒˈɑːvɪs',
    # Alltag
    'ok':      'ˌəʊkˈeɪ',
    'okay':    'əʊkˈeɪ',
    'nice':    'nˈaɪs',
    'bye':     'bˈaɪ',
    'wow':     'wˈaʊ',
    'weekend': 'wiːkˈɛnd',
    'coffee':  'kˈɒfɪ',
    'happy':   'hˈæpɪ',
    'feeling': 'fˈiːlɪŋ',
    'tip':     'tˈɪp',
    'fit':     'fˈɪt',
    'set':     'sˈɛt',
    'party':   'pˈɑːtɪ',
    # Zuhause und Geräte
    'smart':      'smˈɑːt',
    'home':       'hˈəʊm',
    'wifi':       'wˈaɪfaɪ',
    'app':        'ˈæp',
    'tablet':     'tˈæblət',
    'laptop':     'lˈæptɒp',
    'smartphone': 'smˈɑːtfəʊn',
    'iphone':     'ˈaɪfəʊn',
    'ipad':       'ˈaɪpæd',
    'display':    'dɪsplˈeɪ',
    'headset':    'hˈɛdsɛt',
    'watch':      'wˈɒtʃ',
    'airpods':    'ˈeəpɒdz',
    'alexa':      'ɐlˈɛksɐ',
    'siri':       'sˈiəɹɪ',
    'apple':      'ˈæpəl',
    'oled':       'ˈəʊlɛd',
    # Dienste
    'youtube':   'jˈuːtjuːb',
    'youtuber':  'jˈuːtjuːbɐ',
    'spotify':   'spˈɒtɪfˌaɪ',
    'whatsapp':  'wˈɒtsæp',
    'instagram': 'ˈɪnstɐɡɹˌæm',
    'facebook':  'fˈeɪsbʊk',
    'gmail':     'dʒˈiːmˈeɪl',
    'safari':    'sɐfˈɑːɹɪ',
    'amazon':    'ˈæməzən',
    'prime':     'pɹˈaɪm',
    'disney':    'dˈɪznɪ',
    'outlook':   'ˈaʊtlʊk',
    'android':   'ˈændɹɔɪd',
    'messenger': 'mˈɛsɪndʒɐ',
    'slack':     'slˈæk',
    'deezer':    'dˈiːzɐ',
    'tidal':     'tˈaɪdəl',
    'python':    'pˈaɪθən',
    # Computer
    'cloud':      'klˈaʊd',
    'backup':     'bˈækʌp',
    'upgrade':    'ˈʌpɡɹeɪd',
    'password':   'pˈɑːswɜːd',
    'admin':      'ˈædmɪn',
    'setup':      'sˈɛtʌp',
    'reset':      'ɹɪsˈɛt',
    'restart':    'ɹɪstˈɑːt',
    'install':    'ɪnstˈɔːl',
    'profile':    'pɹˈəʊfaɪl',
    'avatar':     'ˈævɐtˌɑː',
    'scan':       'skˈæn',
    'blog':       'blˈɒɡ',
    'vlog':       'vˈiːlˈɒɡ',
    'chat':       'tʃˈæt',
    'check':      'tʃˈɛk',
    'live':       'lˈaɪv',
    'streaming':  'stɹˈiːmɪŋ',
    'podcast':    'pˈɒdkɑːst',
    'playlist':   'plˈeɪlɪst',
    'volume':     'vˈɒljuːm',
    'mute':       'mjˈuːt',
    'loop':       'lˈuːp',
    'random':     'ɹˈændəm',
    'skip':       'skˈɪp',
    'shuffle':    'ʃˈʌfəl',
    'repeat':     'ɹɪpˈiːt',
    'homeoffice': 'hˈəʊmiːʲˌɒfɪs',
    'smartwatch': 'smˈɑːtwɒtʃ',
    'livestream': 'lˈaɪvstɹiːm',
    'powerpoint': 'pˈaʊəpˌɔɪnt',
    'layout':     'lˈeɪaʊt',
    'content':    'kˈɒntɛnt',
    'support':    'səpˈɔːt',
    'client':     'klˈaɪənt',
    'issue':      'ˈɪʃuː',
    'channel':    'tʃˈænəl',
    'tutorial':   'tjuːtˈɔːɹɪʲəl',
    'creator':    'kɹiːʲˈeɪtɐ',
    'interview':  'ˈɪntəvjˌuː',
    # Musik und Medien
    'jazz':       'dʒˈæz',
    'pop':        'pˈɒp',
    'chill':      'tʃˈɪl',
    'lounge':     'lˈaʊndʒ',
    'soul':       'sˈəʊl',
    'reggae':     'ɹˈɛɡiː',
    'techno':     'tˈɛknəʊ',
    'remix':      'ɹɪmˈɪks',
    'soundtrack': 'sˈaʊndtɹæk',
    'comedy':     'kˈɒmədɪ',
    'comic':      'kˈɒmɪk',
    'cartoon':    'kɑːtˈuːn',
    'manga':      'mˈæŋɡɐ',
    'action':     'ˈækʃən',
    'feature':    'fˈiːtʃɐ',
    'spoiler':    'spˈɔɪlɐ',
    'story':      'stˈɔːɹɪ',
    'game':       'ɡˈeɪm',
    'gaming':     'ɡˈeɪmɪŋ',
    'controller': 'kəntɹˈəʊlɐ',
    'equalizer':  'ˈiːkwəlˌaɪzɐ',
    'surround':   'səɹˈaʊnd',
    'subwoofer':  'sˈʌbwuːfɐ',
    'dolby':      'dˈɒlbɪ',
    'vibe':       'vˈaɪb',
    'mood':       'mˈuːd',
    'track':      'tɹˈæk',
    'tune':       'tjˈuːn',
    'hit':        'hˈɪt',
    'club':       'klˈʌb',
    'fan':        'fˈæn',
    'streamer':   'stɹˈiːmɐ',
    'reel':       'ɹˈiːl',
    # Arbeit
    'meeting':   'mˈiːtɪŋ',
    'feedback':  'fˈiːdbæk',
    'deadline':  'dˈɛdlaɪn',
    'coaching':  'kˈəʊtʃɪŋ',
    'manager':   'mˈænɪdʒɐ',
    'marketing': 'mˈɑːkɪtɪŋ',
    'sale':      'sˈeɪl',
    # Essen, Kleidung, Social
    'snack':      'snˈæk',
    'sneaker':    'snˈiːkɐ',
    'sandwich':   'sˈændwɪtʃ',
    'sandwiches': 'sˈændwɪtʃɪz',
    'burger':     'bˈɜːɡɐ',
    'steak':      'stˈeɪk',
    'cocktail':   'kˈɒkteɪl',
    'selfie':     'sˈɛlfɪ',
    'hashtag':    'hˈæʃtæɡ',
    'influencer': 'ˈɪnfluːənsɐ',
    'follower':   'fˈɒləʊɐ',
    'share':      'ʃˈeə',
    'follow':     'fˈɒləʊ',
    'comment':    'kˈɒmɛnt',
    'call':       'kˈɔːl',
    'host':       'hˈəʊst',
    'guest':      'ɡˈɛst',
    'makeup':     'mˈeɪkʌp',
    # Buchstaben, wie man sie auf Deutsch sagt
    'usb': 'ˈuː ˈɛs bˈeː',
    'led': 'ˈɛl ˈeː dˈeː',
    'url': 'ˈuː ˈɛɾ ˈɛl',
    'faq': 'ˈɛf ˈɑː kˈuː',
    'ceo': 'tsˈeː ˈeː ˈoː',
    'suv': 'ˈɛs ˈuː fˈaʊ',
}

_HYPHENATED: dict[str, str] = {
    'e-mail':  'ˈiːmˈeɪl',
    't-shirt': 'tˈiːʃˈɜːt',
    'wi-fi':   'wˈaɪfˌaɪ',
    'make-up': 'mˌeɪkˈʌp',
    'to-do':   'tədˈuː',
}

# Plural and possessive: "s" after a marker is read as the letter "es".
# The extra sound belongs inside the marker, voiced or voiceless like in English.
_VOICELESS = set("ptkfθ")
_SIBILANT = set("szʃʒ")

_BLOCK = re.compile(r"\[\[[^\]]*\]\]")
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß']+")
# A mark glued to the following comma or period swallows the next word.
_SEPARATE = re.compile(r"\]\](?=[^\w\s\[])")
_HYPHEN_RE = re.compile(
    r"(?<![\w-])(" + "|".join(sorted(_HYPHENATED, key=len, reverse=True)) + r")(s)?(?![\w])",
    re.IGNORECASE,
)


def pronounce(text: str, lang: str) -> str:
    """Rewrite German speech so known English words keep their sound."""
    if lang == "en" or not text:
        return text
    return _separate(_join(_BLOCK.split(text), _BLOCK.findall(text), _apply))


def _separate(text: str) -> str:
    return _SEPARATE.sub("]] ", text)


def _apply(text: str) -> str:
    # Hyphenated words first ("E-Mail", "Wi-Fi"), then single words.
    # Phoneme markers are skipped, so a marker is never rewritten.
    rewritten = _hyphen(text)
    return _join(_BLOCK.split(rewritten), _BLOCK.findall(rewritten), _words)


def _join(parts: list[str], blocks: list[str], rewrite) -> str:
    out: list[str] = []
    for index, part in enumerate(parts):
        out.append(rewrite(part))
        if index < len(blocks):
            out.append(blocks[index])
    return "".join(out)


def clip(text: str, limit: int = 1500) -> str:
    """Shorten text for Piper without cutting through a phoneme marker."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit + 1)
    if cut < 0:
        cut = limit
    chunk = text[:cut]
    opened = chunk.rfind("[[")
    closed = chunk.rfind("]]")
    if opened > closed:
        chunk = chunk[:opened]
    return chunk.rstrip()


def _mark(phonemes: str) -> str:
    return f"[[{phonemes}]]"


def _plural(phonemes: str) -> str:
    last = ""
    for char in reversed(phonemes):
        if char not in "ˈˌːˑ ":
            last = char
            break
    if last in _SIBILANT:
        return phonemes + "ɪz"
    if last in _VOICELESS:
        return phonemes + "s"
    return phonemes + "z"


def _hyphen(text: str) -> str:
    def replace(match: re.Match) -> str:
        phonemes = _HYPHENATED[match.group(1).lower()]
        if match.group(2):
            phonemes = _plural(phonemes)
        return _mark(phonemes)

    return _HYPHEN_RE.sub(replace, text)


def _words(text: str) -> str:
    def replace(match: re.Match) -> str:
        key = match.group(0).lower()
        phonemes = _ENGLISH.get(key)
        plural = False
        if phonemes is None and key.endswith("'s"):
            phonemes = _ENGLISH.get(key[:-2])
            plural = phonemes is not None
        if phonemes is None and len(key) >= 5 and key.endswith("ies"):
            phonemes = _ENGLISH.get(key[:-3] + "y")
            plural = phonemes is not None
        if phonemes is None and len(key) >= 4 and key.endswith("s"):
            stem = key[:-1]
            if len(stem) >= 3:
                phonemes = _ENGLISH.get(stem)
                plural = phonemes is not None
        if not phonemes:
            return match.group(0)
        return _mark(_plural(phonemes) if plural else phonemes)

    return _WORD.sub(replace, text)
