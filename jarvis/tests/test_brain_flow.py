import asyncio

import pytest
from google.genai import types

from jarvis.loxone.protocol import Reply
from jarvis.loxone.structure import parse
from jarvis.voice.arbitration import AreaSession

from .conftest import SAMPLE_STRUCTURE


class FakeModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        step = self.script.pop(0)
        return step(contents) if callable(step) else step


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)
        self.aio = self


def call(name, **args):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))]
    )


def text(value):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=value)]))]
    )


@pytest.fixture
def setup(house):
    house.catalog.sync(parse(SAMPLE_STRUCTURE))
    dimmer = next(e for e in house.catalog.entries() if e.name == "Deckenlicht")
    gate = next(e for e in house.catalog.entries() if e.name == "Garagentor")
    house.catalog.update(dimmer.short_id, allowed=True)
    house.catalog.update(gate.short_id, allowed=True)
    area = house.areas.create("Wohnbereich")
    area = house.areas.update(area.id, main_light=dimmer.short_id, rooms=["Wohnzimmer"])
    sent = []

    async def command(uuid, cmd):
        sent.append((uuid, cmd))
        return Reply("ok", "1", 200)

    house.miniserver.command = command
    house.secrets.set("gemini.api_key", "test")
    return house, area, dimmer, gate, sent


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_free_sentence_switches_and_critical_waits_for_yes(setup):
    house, area, dimmer, gate, sent = setup
    client = FakeClient(
        [
            call("control", actions=[{"device": dimmer.short_id, "action": "dim", "value": "40"}, {"device": gate.short_id, "action": "open"}]),
            text("Das Licht ist auf 40 Prozent. Soll ich das Garagentor öffnen?"),
        ]
    )
    house.brain._genai = lambda: client
    session = AreaSession(area.id)
    reply, done = run(house.coordinator.handle_text(session, area, "Licht auf 40 und Garage auf", "de", None))
    assert "Garagentor" in reply
    assert sent == [(dimmer.uuid, "40")]
    assert session.pending is not None and session.pending.kind == "control"
    system = client.models.calls[0]["config"].system_instruction
    assert dimmer.short_id in system and "Wohnbereich" in system
    assert gate.uuid not in system, "UUIDs never go to Gemini"

    reply, done = run(house.coordinator.handle_text(session, area, "Ja", "de", None))
    assert reply == "Erledigt."
    assert sent[-1] == (gate.uuid, "open")
    assert session.pending is None


def test_no_cancels_pending(setup):
    house, area, dimmer, gate, sent = setup
    client = FakeClient([call("control", actions=[{"device": gate.short_id, "action": "open"}]), text("Soll ich?")])
    house.brain._genai = lambda: client
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Garage auf", "de", None))
    reply, _ = run(house.coordinator.handle_text(session, area, "Nein", "de", None))
    assert reply == "Abgebrochen."
    assert sent == []


def test_unknown_device_is_refused(setup):
    house, area, dimmer, gate, sent = setup
    replies = []

    def check(contents):
        replies.append(contents[-1].parts[0].function_response.response)
        return text("Das kann ich nicht.")

    client = FakeClient([call("control", actions=[{"device": "X99", "action": "on"}]), check])
    house.brain._genai = lambda: client
    run(house.coordinator.handle_text(AreaSession(area.id), area, "Mach alles an", "de", None))
    assert sent == []
    assert "nicht freigegeben" in str(replies[0])


def test_search_answer_is_spoken_directly(setup):
    house, area, *_ = setup
    client = FakeClient([call("web_search", query="Wetter Berlin morgen"), text("Morgen wird es sonnig bei 12 Grad.")])
    house.brain._genai = lambda: client
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Wie wird das Wetter morgen?", "de", None))
    assert reply == "Morgen wird es sonnig bei 12 Grad."
    search_config = client.models.calls[1]["config"]
    assert any(t.google_search is not None for t in search_config.tools)
    assert all(t.google_search is None for t in client.models.calls[0]["config"].tools), "house commands go without search"


def test_offline_light_uses_main_light(setup):
    house, area, dimmer, gate, sent = setup
    house.secrets.delete("gemini.api_key")
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Licht an", "de", None))
    assert reply == "Licht an."
    assert sent == [(dimmer.uuid, "on")]
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Wie spät ist es?", "de", None))
    assert "nicht erreichbar" in reply


def test_thinking_level_only_for_gemini_3(setup):
    house, *_ = setup
    assert house.brain._thinking() is not None
    house.settings.update({"gemini.model": "gemini-2.5-flash"})
    assert house.brain._thinking() is None
