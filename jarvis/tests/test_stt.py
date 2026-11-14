import numpy as np

from jarvis.voice.stt import pick_language, prepare_audio


def test_quiet_speech_is_lifted_and_silence_stays_quiet():
    quiet = np.full(160, 0.1, dtype=np.float32)
    assert prepare_audio(quiet)[0] == np.float32(0.5)
    noise = np.full(160, 0.005, dtype=np.float32)
    assert prepare_audio(noise)[0] == np.float32(0.005)
    loud = np.full(160, 0.8, dtype=np.float32)
    assert prepare_audio(loud)[0] == np.float32(0.8)


def test_short_german_is_not_taken_for_english():
    assert pick_language("en", {"en": 0.42, "de": 0.36}, None) == "de"
    assert pick_language("en", {"en": 0.8, "de": 0.1}, None) == "en"
    assert pick_language("nl", {"nl": 0.5, "de": 0.2, "en": 0.1}, None) == "de"
    assert pick_language("en", {}, "en") == "en"
    assert pick_language("de", {}, None) == "de"
    assert pick_language("pl", {}, None) == "de"
