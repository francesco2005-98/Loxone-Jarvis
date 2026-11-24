import numpy as np

from jarvis.voice.stt import Recognizer, Transcript, guess_language, hotwords_from_prompt, polish, prepare_audio
from jarvis.voice.vad import high_pass


def test_quiet_speech_is_lifted_and_silence_stays_quiet():
    quiet = np.full(160, 0.1, dtype=np.float32)
    assert prepare_audio(quiet)[0] == np.float32(0.5)
    noise = np.full(160, 0.005, dtype=np.float32)
    assert prepare_audio(noise)[0] == np.float32(0.005)
    loud = np.full(160, 0.8, dtype=np.float32)
    assert prepare_audio(loud)[0] == np.float32(0.8)


def test_language_is_german_unless_clearly_english():
    assert guess_language("Mach das Licht im Esszimmer an") == "de"
    assert guess_language("Ja, gerne.") == "de"
    assert guess_language("Turn on the lights in the living room") == "en"
    assert guess_language("What time is it") == "en"
    assert guess_language("Spiel etwas Jazz") == "de"


def test_kroko_falls_back_to_whisper_small_without_its_model(tmp_path):
    rec = Recognizer(tmp_path)
    used = []
    rec._whisper = lambda audio, model, language, prompt: used.append(model) or Transcript("x", "de", 0.0, 0.0)
    rec._kroko = lambda audio, language, prompt="": used.append("kroko") or Transcript("x", "de", 0.0, 0.0)
    pcm = (np.ones(16000, dtype="<i2") * 1000).tobytes()
    rec.transcribe(pcm, model="kroko", language="auto")
    assert used == ["small"], "no Kroko files in the models folder"
    (tmp_path / "kroko").mkdir()
    for name in ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"):
        (tmp_path / "kroko" / name).write_bytes(b"")
    rec.transcribe(pcm, model="kroko", language="auto")
    rec.transcribe(pcm, model="kroko", language="en")
    assert used == ["small", "kroko", "small"], "English-only speech keeps Whisper"


def test_a_click_does_not_keep_the_sentence_quiet():
    audio = np.full(16000, 0.05, dtype=np.float32)
    audio[10] = 0.95
    lifted = prepare_audio(audio)
    assert lifted[1000] > 0.3
    assert lifted[10] <= 1.0


def test_quiet_sentence_is_not_gated_away():
    pcm = np.full(16000, 900, dtype="<i2").tobytes()
    out = np.frombuffer(high_pass(pcm, 2), dtype="<i2")
    assert np.max(np.abs(out)) > 200


def test_known_mishears_become_the_command_word():
    assert polish("mach das lich an") == "mach das Licht an"
    assert polish("Rolllos auf 30 pro zent") == "Rolllos auf 30 Prozent"
    assert polish("Licht im Wohnzimmer an") == "Licht im Wohnzimmer an"
    assert hotwords_from_prompt("Jarvis, Küche, Küche, Licht") == "Jarvis/Küche/Licht"
