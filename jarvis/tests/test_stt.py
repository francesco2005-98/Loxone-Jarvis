import numpy as np

from jarvis.voice.stt import Recognizer, Transcript, guess_language, prepare_audio


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
    rec._kroko = lambda audio, language: used.append("kroko") or Transcript("x", "de", 0.0, 0.0)
    pcm = (np.ones(16000, dtype="<i2") * 1000).tobytes()
    rec.transcribe(pcm, model="kroko", language="auto")
    assert used == ["small"], "no Kroko files in the models folder"
    (tmp_path / "kroko").mkdir()
    for name in ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt"):
        (tmp_path / "kroko" / name).write_bytes(b"")
    rec.transcribe(pcm, model="kroko", language="auto")
    rec.transcribe(pcm, model="kroko", language="en")
    assert used == ["small", "kroko", "small"], "English-only speech keeps Whisper"
