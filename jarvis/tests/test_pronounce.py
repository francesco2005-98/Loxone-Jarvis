import unicodedata

from jarvis.audio.pronounce import _ENGLISH, _HYPHENATED, clip, pronounce

# German words and loanwords Piper already says properly. None of these may change.
_UNCHANGED = (
    "Links steht der Standard. Der Computer spielt Radio, der Timer läuft. "
    "Das Video hat einen guten Status. Der Code stimmt. Die Pause ist vorbei. "
    "Der Bass im Stereo, das Training und das Album. Die Post, die Tour und der Start. "
    "Stop, der Funk und ein Brand. Tests, Tipps und Fitness. "
    "Update, Download und Bluetooth sind online. Team, Job und Netflix auch. "
    "Dieses alles und etwas Neues, nichts rechts, abends, muss, dass, Haus und Eis. "
    "WLAN, TV und PC bleiben. Gesetz und Installation und Apparat und Popmusik."
)


def test_decomposed_umlauts_are_joined_for_the_voice():
    decomposed = unicodedata.normalize("NFD", "Licht in der Küche, schön, über, Käse, Grüße.")
    assert decomposed != "Licht in der Küche, schön, über, Käse, Grüße."
    assert pronounce(decomposed, "de") == "Licht in der Küche, schön, über, Käse, Grüße."
    assert pronounce(decomposed, "en") == "Licht in der Küche, schön, über, Käse, Grüße."
    assert pronounce("Rollläden im Bü\u00adro", "de") == "Rollläden im Büro", "a soft hyphen splits the word"


def test_english_words_keep_their_sound_in_german():
    said = pronounce("Sehr wohl, Sir. Ich starte Spotify.", "de")
    assert "[[sˈɜː]]" in said and "[[spˈɒtɪfˌaɪ]]" in said
    assert "Sir" not in said and "Spotify" not in said
    assert pronounce("Wie Sie wünschen, Ma'am.", "de").endswith("[[mˈæm]] .")
    assert pronounce("Erledigt, Sir. Das Licht ist an.", "de").endswith("[[sˈɜː]] . Das Licht ist an.")
    assert pronounce("Wie Sie wünschen, Mam.", "de").endswith("[[mˈæm]] .")
    assert pronounce("SPOTIFY und Ok und iPhone", "de") == pronounce("Spotify und OK und IPhone", "de")


def test_common_loanwords_and_plurals():
    said = pronounce(
        "YouTube und Wi-Fi, Live aus der Cloud. Zwei Apps, drei Meetings, die E-Mails und ein T-Shirt. Jazz, okay?",
        "de",
    )
    assert "YouTube" not in said and "Wi-Fi" not in said and "Live" not in said
    assert "Cloud" not in said and "Apps" not in said and "E-Mails" not in said
    assert said.count("[[") >= 8
    assert pronounce("Apps", "de") == "[[ˈæps]]"
    assert pronounce("Meetings", "de") == "[[mˈiːtɪŋz]]"
    assert pronounce("Playlists", "de") == "[[plˈeɪlɪsts]]"
    assert pronounce("E-Mails", "de") == "[[ˈiːmˈeɪlz]]"
    assert pronounce("T-Shirts", "de") == "[[tˈiːʃˈɜːts]]"
    assert pronounce("Storys", "de") == "[[stˈɔːɹɪz]]"
    assert pronounce("Selfies", "de") == "[[sˈɛlfɪz]]"
    assert pronounce("Partys", "de") == "[[pˈɑːtɪz]]"
    assert pronounce("Sir's", "de") == "[[sˈɜːz]]"
    assert "]]s" not in said
    assert pronounce("Video-Call", "de").startswith("Video-[[")
    assert pronounce("Live-Stream", "de").endswith("-Stream")
    assert pronounce("USB", "de").startswith("[[")
    assert "jˌuː" not in pronounce("USB und LED", "de")


def test_german_words_and_english_speech_stay_untouched():
    assert pronounce(_UNCHANGED, "de") == _UNCHANGED
    sentence = "Very good, Sir. Spotify is playing."
    assert pronounce(sentence, "en") == sentence
    assert pronounce("Das Licht im Wohnzimmer ist an.", "de") == "Das Licht im Wohnzimmer ist an."
    assert pronounce("Tipps", "de") == "Tipps"
    assert pronounce("links", "de") == "links"
    assert pronounce("Pausen", "de") == "Pausen"


def test_markers_are_not_rewritten_twice():
    for word, phonemes in {**_ENGLISH, **_HYPHENATED}.items():
        once = pronounce(word, "de")
        assert once == f"[[{phonemes}]]"
        assert pronounce(once, "de") == once


def test_clip_keeps_phoneme_markers_intact():
    text = pronounce("Sir " * 400, "de")
    short = clip(text, 80)
    assert len(short) <= 80
    assert short.count("[[") == short.count("]]")
    assert clip("kurz", 80) == "kurz"
