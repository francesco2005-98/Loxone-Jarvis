import numpy as np

from jarvis.brain import confirm, fallback
from jarvis.voice.arbitration import ARBITRATION_WINDOW, AreaSession, Offer, Phase
from jarvis.voice.speaker import identify, profile_from
from jarvis.voice.vad import Endpointer, VadConfig


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def tone(seconds, amplitude):
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2").tobytes()


def test_loudest_board_wins_and_others_are_rejected():
    clock = Clock()
    session = AreaSession("wohn", clock=clock)
    assert session.offer("near", True) == Offer.ACCEPT
    assert session.offer("far", True) == Offer.ACCEPT
    session.feed("near", tone(0.2, 8000))
    session.feed("far", tone(0.2, 1500))
    clock.t += ARBITRATION_WINDOW + 0.01
    assert session.window_elapsed()
    winner, losers = session.decide()
    assert winner == "near" and losers == ["far"]
    assert session.phase == Phase.LISTENING
    assert session.buffered_audio()
    assert session.offer("third", True) == Offer.REJECT


def test_late_board_after_window_is_rejected():
    clock = Clock()
    session = AreaSession("wohn", clock=clock)
    session.offer("a", True)
    clock.t += ARBITRATION_WINDOW + 0.1
    assert session.offer("b", True) == Offer.REJECT


def test_followup_only_for_same_board_and_window():
    clock = Clock()
    session = AreaSession("wohn", clock=clock)
    session.offer("a", True)
    session.decide()
    session.start_thinking()
    session.start_speaking()
    session.open_followup("a")
    assert session.offer("b", False) == Offer.REJECT
    assert session.offer("a", False) == Offer.FOLLOWUP
    session.start_thinking()
    session.open_followup("a")
    clock.t += 9
    assert session.offer("a", False) == Offer.REJECT
    assert session.followup_expired()


def test_wake_word_inside_followup_starts_new_run_and_keeps_history():
    clock = Clock()
    session = AreaSession("wohn", clock=clock)
    session.offer("a", True)
    session.decide()
    session.history = ["x"]
    session.open_followup("a")
    assert session.offer("b", True) == Offer.ACCEPT
    assert session.phase == Phase.COLLECTING and session.history == ["x"]


def test_two_areas_are_independent():
    a, b = AreaSession("schlaf"), AreaSession("wohn")
    assert a.offer("m1", True) == Offer.ACCEPT
    assert b.offer("m2", True) == Offer.ACCEPT


def test_endpointer_detects_end_and_timeout():
    ep = Endpointer(VadConfig())
    assert ep.feed(tone(0.3, 30)) == "continue"
    assert ep.feed(tone(0.8, 9000)) == "continue"
    assert ep.feed(tone(1.0, 20)) == "end"
    quiet = Endpointer(VadConfig(no_speech_timeout_ms=1000))
    assert quiet.feed(tone(1.2, 20)) == "timeout"


def test_confirmation_words():
    assert confirm.classify("Ja") == confirm.Answer.YES
    assert confirm.classify("Ja, bitte.") == confirm.Answer.YES
    assert confirm.classify("Mach das") == confirm.Answer.YES
    assert confirm.classify("Yes") == confirm.Answer.YES
    assert confirm.classify("Nein") == confirm.Answer.NO
    assert confirm.classify("Ja, aber nicht jetzt") == confirm.Answer.NO
    assert confirm.classify("Mach das Licht im Bad an") == confirm.Answer.UNCLEAR
    assert confirm.classify("") == confirm.Answer.UNCLEAR


def test_offline_sentences():
    assert fallback.parse("Licht an") == fallback.FallbackCommand("light", "on")
    assert fallback.parse("Jarvis, mach das Licht aus") == fallback.FallbackCommand("light", "off")
    assert fallback.parse("Rollo zu") == fallback.FallbackCommand("shade", "down")
    assert fallback.parse("Rollläden hoch") == fallback.FallbackCommand("shade", "up")
    assert fallback.parse("lights on") == fallback.FallbackCommand("light", "on")
    assert fallback.parse("Wie wird das Wetter") is None


def test_speaker_identification_is_conservative():
    rng = np.random.default_rng(1)
    base = rng.normal(size=256)
    other = rng.normal(size=256)
    norm = lambda v: v / np.linalg.norm(v)  # noqa: E731
    luca = profile_from([norm(base + rng.normal(scale=0.3, size=256)) for _ in range(3)])
    anna = profile_from([norm(other + rng.normal(scale=0.3, size=256)) for _ in range(3)])
    profiles = {"luca": luca, "anna": anna}
    assert identify(norm(base + rng.normal(scale=0.3, size=256)), profiles).person_id == "luca"
    stranger = norm(rng.normal(size=256))
    assert identify(stranger, profiles).person_id is None
