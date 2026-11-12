import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis.audio import wav
from jarvis.voice.live import LiveSession, Segmenter, split_wake
from jarvis.voice.stt import Transcript


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_wake_word_and_rest_are_split():
    assert split_wake("Jarvis, mach das Licht an.") == (True, "mach das Licht an")
    assert split_wake("Hey Jarvis!") == (True, "")
    assert split_wake("Dschawis wie spät ist es?") == (True, "wie spät ist es")
    assert split_wake("Mach das Licht an") == (False, "")
    assert split_wake("Ich habe gestern mit dem Nachbarn über Jarvis gesprochen") == (False, "")


def _tone(seconds, amplitude):
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype("<i2").tobytes()


def test_segmenter_cuts_one_sentence_with_preroll():
    seg = Segmenter(2)
    quiet = _tone(1.0, 20)
    speech = _tone(1.2, 8000)
    out = seg.feed(quiet) + seg.feed(speech) + seg.feed(_tone(1.0, 20))
    assert len(out) == 1
    assert len(speech) <= len(out[0]) <= len(speech) + 16000 * 2


class _Fake:
    def __init__(self, house, texts):
        self.texts = list(texts)
        self.handled = []
        house.stt.transcribe = lambda pcm, **kw: Transcript(self.texts.pop(0), "de", -0.2, 0.01)

        async def handle_text(session, area, text, lang, person_id, origin=""):
            self.handled.append((text, origin))
            return "Erledigt.", ["Licht an"]

        async def render(text, lang):
            return wav.Pcm(np.zeros(2400, dtype="<i2"), 24000)

        house.coordinator.handle_text = handle_text
        house.coordinator._identify = lambda pcm: None
        house.speech.render = render


def _session(house):
    sent = []
    holder = {}

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "reply":
            asyncio.get_running_loop().call_soon(holder["session"]._played.set)

    session = LiveSession(house, send)
    holder["session"] = session
    return session, sent


def test_wake_word_then_command_answers_in_the_browser(house):
    fake = _Fake(house, ["Jarvis.", "Mach das Licht an", "Fernsehen läuft"])
    session, sent = _session(house)
    announced = []
    house.speech.say = lambda *a, **k: announced.append(a)

    async def scenario():
        await session._segment(b"\0" * 32000)
        assert session.phase == "listening"
        await session._segment(b"\0" * 32000)
        assert session.phase == "followup"
        session.phase = "idle"
        await session._segment(b"\0" * 32000)

    run(scenario())
    assert fake.handled == [("Mach das Licht an", "live")]
    reply = next(m for m in sent if m["type"] == "reply")
    assert reply["text"] == "Erledigt." and reply["audio"]
    assert announced == [], "Live answers never go to the Audioserver"


def test_command_in_the_same_breath(house):
    fake = _Fake(house, ["Jarvis, wie spät ist es?"])
    session, sent = _session(house)

    run(session._segment(b"\0" * 32000))
    assert fake.handled == [("wie spät ist es", "live")]


def test_sentences_without_wake_word_are_ignored(house):
    fake = _Fake(house, ["Mach das Licht an"])
    session, sent = _session(house)
    run(session._segment(b"\0" * 32000))
    assert fake.handled == [] and session.phase == "idle"


def test_live_timer_rings_on_the_open_browser(house):
    session, sent = _session(house)
    house.live_sessions.add(session)
    spoken = []

    async def say(*args):
        spoken.append(args)

    async def render(text, lang):
        return wav.Pcm(np.zeros(2400, dtype="<i2"), 24000)

    house.speech.say = say
    house.speech.render = render
    timer = house.timers.add(kind="timer", due_at=time.time() + 60, label="Pizza", text="", bereich_id="live", lang="de", origin="live")
    run(house._timer_due(timer))
    assert spoken == []
    assert sent[-1]["type"] == "announce" and "Pizza" in sent[-1]["text"]
    house.live_sessions.clear()
    run(house._timer_due(timer))
    assert len(spoken) == 1, "without an open Live page the reminder falls back to the Audioserver"


@pytest.fixture
def client(house):
    from jarvis.main import create_app

    return TestClient(create_app(house), client=("192.168.1.5", 50000))


def test_live_voice_needs_a_session_and_reports_https_port(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/live/voice") as ws:
            ws.receive_json()
    assert client.get("/api/session").json()["https_port"] == 8501
    client.post("/api/setup", json={"password": "langes-passwort"}, headers={"X-Jarvis": "1"})
    with client.websocket_connect("/api/live/voice") as ws:
        assert ws.receive_json() == {"type": "state", "phase": "idle"}
        ws.send_json({"type": "hello", "area": ""})
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["area"]["id"] == "live"


def test_microphone_allowed_for_own_origin(client):
    assert "microphone=(self)" in client.get("/").headers["permissions-policy"]


def test_self_signed_certificate_is_created_once(tmp_path):
    from jarvis.tls import ensure_certificate

    cert, key = ensure_certificate(tmp_path)
    assert cert.read_bytes().startswith(b"-----BEGIN CERTIFICATE")
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    first = cert.read_bytes()
    ensure_certificate(tmp_path)
    assert cert.read_bytes() == first
