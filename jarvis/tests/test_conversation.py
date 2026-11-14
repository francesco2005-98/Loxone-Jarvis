import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from jarvis.brain import quick
from jarvis.brain.phrases import DEFAULT, fill
from jarvis.brain.tools import Pending, Turn
from jarvis.loxone.catalog import detect_light_kind
from jarvis.loxone.protocol import Reply
from jarvis.loxone.structure import LoxControl, parse
from jarvis.voice.arbitration import AreaSession

from .conftest import SAMPLE_STRUCTURE
from .test_brain_flow import FakeClient, call

H = {"X-Jarvis": "1"}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def home(house):
    structure = parse(SAMPLE_STRUCTURE)
    house.miniserver.structure = structure
    house.catalog.sync(structure)
    for entry in house.catalog.entries():
        if entry.group in ("light", "shading", "gate"):
            house.catalog.update(entry.short_id, allowed=True)
    area = house.areas.create("Wohnbereich")
    area = house.areas.update(area.id, rooms=["Wohnzimmer"])
    sent = []

    async def command(uuid, cmd):
        sent.append((uuid, cmd))
        return Reply("ok", "1", 200)

    house.miniserver.command = command
    house.secrets.set("gemini.api_key", "test")
    entries = {e.name: e for e in house.catalog.entries()}
    return house, area, entries, sent


# ------------------------------------------------------------------ light kind
def test_light_kind_from_loxone_names_and_override(home):
    house, area, entries, _ = home
    assert entries["Indirekte Beleuchtung TV"].kind == "indirect"
    assert entries["Deckenlicht"].kind == "direct"
    assert entries["Rollo Terrasse"].kind == "unknown", "only lights have a light kind"
    control = LoxControl("u", "Kreis 3", "Dimmer", None, None, None, None, None, details={"lightType": "indirect"})
    assert detect_light_kind(control, "Kreis 3", []) == "indirect"
    house.catalog.update(entries["Deckenlicht"].short_id, light_kind="indirect")
    assert house.catalog.get(entries["Deckenlicht"].short_id).kind == "indirect"


def test_prompt_groups_circuits_under_their_light_block(home):
    house, area, entries, _ = home
    system = house.brain.system_for(Turn(area=area, lang="de", text=""), False)
    block = entries["Licht Wohnzimmer"]
    line = next(l for l in system.splitlines() if l.startswith(block.short_id + " |"))
    assert "Kreise:" in line and "[indirekt]" in line and "[direkt]" in line
    tv = entries["Indirekte Beleuchtung TV"]
    assert f"Lichtkreis von {block.short_id}" in next(l for l in system.splitlines() if l.startswith(tv.short_id + " |"))


# ------------------------------------------------------------- instant path
def test_named_circuit_switches_instantly(home):
    house, area, entries, sent = home
    house.brain._genai = lambda: (_ for _ in ()).throw(AssertionError("no Gemini"))
    house.settings.update({"brain.suggestions": "off"})
    session = AreaSession(area.id)
    reply, done = run(
        house.coordinator.handle_text(session, area, "Mach die indirekte Beleuchtung im Wohnzimmer am Fernseher an", "de", None)
    )
    assert sent == [(entries["Indirekte Beleuchtung TV"].uuid, "on")]
    assert "Wohnzimmer" in reply
    run(house.coordinator.handle_text(session, area, "Mach das indirekte Licht im Wohnzimmer aus", "de", None))
    assert sent[-1] == (entries["Indirekte Beleuchtung TV"].uuid, "off")


def test_unclear_device_goes_to_gemini(home):
    house, area, entries, _ = home
    for text in ("Mach den Fernseher an", "Mach die Lampe im Wohnzimmer am Fenster an"):
        command = quick.parse(text)
        assert command is not None and quick.targets(house, area, command) == ([], None), text


def test_mood_by_name(home):
    house, area, entries, sent = home
    block = entries["Licht Wohnzimmer"]
    house.miniserver.texts["s-0002"] = json.dumps([{"id": 1, "name": "Fernsehen"}, {"id": 778, "name": "Aus"}])
    command = quick.parse("Stimmung Fernsehen im Wohnzimmer")
    found, room = quick.targets(house, area, command)
    assert [e.uuid for e in found] == [block.uuid] and command.scene == "Fernsehen"


# ------------------------------------------------------------------ offers
def _offer_call(entries, question="Soll ich außerdem die Beschattung im Wohnzimmer herunterfahren?"):
    return call(
        "control",
        actions=[{"device": entries["Indirekte Beleuchtung TV"].short_id, "action": "on"}],
        reply="Wie Sie wünschen, Sir.",
        offer={"question": question, "actions": [{"device": entries["Rollo Terrasse"].short_id, "action": "down"}]},
    )


def test_offer_then_plain_yes_runs_locally(home):
    house, area, entries, sent = home
    client = FakeClient([_offer_call(entries)])
    house.brain._genai = lambda: client
    session = AreaSession(area.id)
    reply, _ = run(house.coordinator.handle_text(session, area, "Mach das Fernsehlicht an, es wird gemütlich", "de", None))
    assert reply == "Wie Sie wünschen, Sir. Soll ich außerdem die Beschattung im Wohnzimmer herunterfahren?"
    assert isinstance(session.pending, Pending) and session.pending.kind == "offer"
    reply, done = run(house.coordinator.handle_text(session, area, "Ja", "de", None))
    assert sent[-1] == (entries["Rollo Terrasse"].uuid, "FullDown")
    assert reply in [fill(t, anrede="Sir") for t in DEFAULT["de"]["offer_done"]]
    assert len(client.models.calls) == 1, "the yes never reached Gemini"


def test_no_is_remembered_and_suppresses_the_same_offer(home):
    house, area, entries, sent = home
    house.brain._genai = lambda: FakeClient([_offer_call(entries)])
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Fernsehlicht an bitte, mach es gemütlich", "de", None))
    reply, _ = run(house.coordinator.handle_text(session, area, "Nein danke", "de", None))
    assert reply in [fill(t, anrede="Sir") for t in DEFAULT["de"]["offer_declined"]]
    assert all(uuid != entries["Rollo Terrasse"].uuid for uuid, _ in sent)
    assert house.coordinator.declined(area.id, entries["Rollo Terrasse"].uuid, "down")
    assert "Rollo Terrasse: down" in house.brain.system_for(Turn(area=area, lang="de", text=""), False)
    house.brain._genai = lambda: FakeClient([_offer_call(entries)])
    reply, _ = run(house.coordinator.handle_text(session, area, "Fernsehlicht nochmal an, mach es gemütlich", "de", None))
    assert "Beschattung" not in reply and session.pending is None


def test_offer_with_critical_device_is_dropped(home):
    house, area, entries, _ = home
    gate = entries["Garagentor"]
    turn = Turn(area=area, lang="de", text="")
    assert house.brain.runner.offer({"question": "Tor auf?", "actions": [{"device": gate.short_id, "action": "open"}]}, turn) is None
    house.settings.update({"brain.suggestions": "off"})
    rollo = entries["Rollo Terrasse"]
    assert house.brain.runner.offer({"question": "Rollos?", "actions": [{"device": rollo.short_id, "action": "down"}]}, turn) is None


def test_friendly_yes_words_count_for_offers(home):
    house, area, entries, sent = home
    session = AreaSession(area.id)
    rollo = entries["Rollo Terrasse"]
    session.pending = Pending("offer", "Rollos runter?", [(rollo, "down", None, None)])
    run(house.coordinator.handle_text(session, area, "Gerne", "de", None))
    assert sent[-1] == (rollo.uuid, "FullDown")


# ------------------------------------------------------------------ hybrid
def test_instant_command_gets_a_background_suggestion(home):
    house, area, entries, sent = home
    rollo = entries["Rollo Terrasse"]
    asked = []

    async def suggest(turn):
        asked.append(turn.done)
        return Pending("offer", "Soll ich die Rollos herunterfahren?", [(rollo, "down", None, None)])

    house.brain.suggest = suggest
    session = AreaSession(area.id)

    async def scenario():
        reply, _ = await house.coordinator.handle_text(session, area, "Mach das Deckenlicht an", "de", None)
        offer = await house.coordinator.take_suggestion(session)
        return reply, offer

    reply, offer = run(scenario())
    assert asked and offer is not None and session.pending is offer
    session.pending = None
    run(house.coordinator.handle_text(session, area, "Mach das Deckenlicht aus", "de", None))
    assert session.suggestion is None, "cooldown: no second background question right away"


def test_slow_suggestion_is_skipped(home):
    house, area, entries, _ = home

    async def suggest(turn):
        await asyncio.sleep(5)

    house.brain.suggest = suggest
    session = AreaSession(area.id)

    async def scenario():
        await house.coordinator.handle_text(session, area, "Mach das Deckenlicht an", "de", None)
        return await house.coordinator.take_suggestion(session, wait=0.05)

    assert run(scenario()) is None and session.pending is None


def test_live_speaks_the_suggestion_after_the_reply(house):
    from jarvis.voice.live import LiveSession

    import numpy as np

    from jarvis.audio import wav

    sent = []

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "reply":
            asyncio.get_running_loop().call_soon(session._played.set)

    session = LiveSession(house, send)

    async def handle_text(s, area, text, lang, person_id, origin="", followup=False):
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        future.set_result(Pending("offer", "Soll ich die Rollos herunterfahren?", []))
        s.suggestion = future
        return "Sehr wohl, Sir.", ["Licht an"]

    async def render(text, lang, **kw):
        return wav.Pcm(np.zeros(2400, dtype="<i2"), 24000)

    house.coordinator.handle_text = handle_text
    house.coordinator._identify = lambda pcm: None
    house.speech.render = render
    run(session._handle("Licht an", "de", b"\0" * 3200))
    replies = [m["text"] for m in sent if m["type"] == "reply"]
    assert replies == ["Sehr wohl, Sir.", "Soll ich die Rollos herunterfahren?"]
    assert session.phase == "followup"


# --------------------------------------------------------------------- api
def test_structure_download_and_light_kind_api(home):
    house, area, entries, _ = home
    from jarvis.main import create_app

    client = TestClient(create_app(house), client=("192.168.1.5", 50000))
    client.post("/api/setup", json={"password": "langes-passwort"}, headers=H)
    assert client.get("/api/loxone/structure").status_code == 404
    house.miniserver.raw_structure = {"msInfo": {"msName": "Haus", "currentUser": {"name": "jarvis"}, "serialNr": "504F"}, "controls": {}}
    data = client.get("/api/loxone/structure").json()
    assert data["msInfo"] == {"msName": "Haus"}
    short = entries["Deckenlicht"].short_id
    res = client.put(f"/api/loxone/controls/{short}", json={"light_kind": "indirect"}, headers=H).json()
    assert res["light_kind"] == "indirect" and res["kind"] == "indirect"
    assert client.put(f"/api/loxone/controls/{short}", json={"light_kind": "bunt"}, headers=H).status_code == 400
