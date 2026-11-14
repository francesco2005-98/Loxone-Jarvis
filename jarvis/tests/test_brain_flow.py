import asyncio

import pytest
from google.genai import types

from jarvis.brain.phrases import DEFAULT, fill
from jarvis.brain.tools import Turn
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
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Kannst du hier mal das Licht an machen", "de", None))
    assert reply in [fill(t, anrede="Sir") for t in DEFAULT["de"]["light_on"]]
    assert sent == [(dimmer.uuid, "on")]
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Wer hat gestern gewonnen?", "de", None))
    assert "nicht erreichbar" in reply


def test_play_query_starts_spotify_playlist(setup):
    house, *_ = setup
    structure = parse(SAMPLE_STRUCTURE)
    zone = structure.controls["10000000-0000-0008-ffff000000000001"]
    calls = []

    class Audio:
        def ready_for(self, player):
            return True

        async def favorites(self, player):
            return []

        async def services(self, player):
            calls.append(("services", player))
            return [{"service": "spotify", "user": "luca", "name": "Spotify"}]

        async def search_service(self, player, service, user, query, *, kind="playlist", limit=8):
            calls.append(("search", player, service, user, query, kind))
            return [{"name": "Jazz Classics", "path": "spotify:playlist:abc", "playlist": True, "user": ""}]

        async def play_service(self, player, service, user, path):
            calls.append(("play", player, service, user, path))

    house.audioserver = Audio()
    title = run(house.play_query(zone, "Jazz"))
    assert title == "Jazz Classics"
    assert calls == [
        ("services", 3),
        ("search", 3, "spotify", "luca", "Jazz", "playlist"),
        ("play", 3, "spotify", "luca", "spotify:playlist:abc"),
    ]


def test_jazz_search_starts_in_the_named_area(setup):
    house, area, *_ = setup
    structure = parse(SAMPLE_STRUCTURE)
    house.miniserver.structure = structure
    zone_uuid = "10000000-0000-0008-ffff000000000001"
    area = house.areas.update(area.id, zone_uuid=zone_uuid)
    played = {}

    async def play_query(zone, query):
        played["zone"] = zone.uuid
        played["query"] = query
        return "Jazz Classics"

    house.play_query = play_query
    turn = Turn(area=area, lang="de", text="spiel etwas Jazz im Wohnzimmer")
    result = run(house.brain.runner._tool_music({"action": "search", "value": "Jazz", "areas": ["Wohnbereich"]}, turn))
    assert played == {"zone": zone_uuid, "query": "Jazz"}
    assert result["results"] == [{"area": "Wohnbereich", "ok": True, "title": "Jazz Classics"}]
    assert turn.done == ["Musik Jazz Classics Wohnbereich"]


def test_thinking_level_only_for_gemini_3(setup):
    house, *_ = setup
    assert house.brain._thinking() is not None
    house.settings.update({"gemini.model": "gemini-2.5-flash"})
    assert house.brain._thinking() is None


def test_plain_light_command_switches_without_gemini(setup):
    house, area, dimmer, gate, sent = setup
    house.brain._genai = lambda: (_ for _ in ()).throw(AssertionError("Gemini must not be asked"))
    session = AreaSession(area.id)
    reply, done = run(house.coordinator.handle_text(session, area, "Schalte das Licht im Wohnzimmer an.", "de", None))
    assert "Wohnzimmer" in reply and "Sir" in reply
    assert sent == [(dimmer.uuid, "on")]
    assert done and len(session.history) == 2
    house.settings.update({"house.default_anrede": ""})
    reply, _ = run(house.coordinator.handle_text(session, area, "Licht aus", "de", None))
    assert reply in [fill(t) for t in DEFAULT["de"]["light_off"]] and "Sir" not in reply
    assert sent[-1] == (dimmer.uuid, "off")


def test_unknown_room_goes_to_gemini(setup):
    house, area, dimmer, gate, sent = setup
    client = FakeClient([text("Ein Dachboden ist nicht eingerichtet.")])
    house.brain._genai = lambda: client
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Mach das Licht im Dachboden an", "de", None))
    assert reply == "Ein Dachboden ist nicht eingerichtet."
    assert sent == []


def test_successful_action_needs_one_gemini_round(setup):
    house, area, dimmer, gate, sent = setup
    client = FakeClient([call("control", actions=[{"device": dimmer.short_id, "action": "dim", "value": "30"}], reply="Gedimmt auf 30 Prozent.")])
    house.brain._genai = lambda: client
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Dimm das Deckenlicht auf 30 Prozent", "de", None))
    assert reply == "Gedimmt auf 30 Prozent."
    assert len(client.models.calls) == 1
    assert sent and sent[0][0] == dimmer.uuid


def test_failed_action_still_gets_a_second_round(setup):
    house, area, dimmer, gate, sent = setup
    client = FakeClient(
        [
            call("control", actions=[{"device": "L99", "action": "on"}], reply="Erledigt."),
            text("Dieses Gerät kenne ich nicht."),
        ]
    )
    house.brain._genai = lambda: client
    reply, _ = run(house.coordinator.handle_text(AreaSession(area.id), area, "Mach L99 an", "de", None))
    assert reply == "Dieses Gerät kenne ich nicht."
    assert len(client.models.calls) == 2


def test_quick_parser_takes_only_plain_sentences():
    from jarvis.brain.quick import parse

    assert parse("Jarvis, schalte das Licht im Esszimmer an").room == "esszimmer"
    assert parse("Mach in der Küche das Licht aus").prep == "in der"
    assert parse("Licht an").room is None
    assert parse("turn on the lights in the kitchen").action == "on"
    assert parse("Mach das Licht im Esszimmer an und die Rollos runter") is None
    assert parse("Dimm das Licht im Esszimmer auf 30 Prozent").value == 30
    assert parse("Fahr die Rollos im Wohnzimmer runter").action == "down"
    assert parse("Rollos hoch").room is None
    assert parse("Wie spät ist es?").kind == "time"
    assert parse("Mach das Licht im Esszimmer gemütlich") is None
    assert parse("Dimm das Licht auf 300 Prozent") is None


ZONES = {
    "rooms": {"r-ess": {"name": "Esszimmer"}, "r-bad": {"name": "Bad OG"}},
    "mediaServer": {"as": {"type": 2, "host": "192.168.1.40"}},
    "controls": {
        "z-ess": {"name": "Esszimmer", "type": "AudioZoneV2", "uuidAction": "z-ess", "room": "r-ess", "details": {"playerid": 4, "server": "as"}},
        "z-bad": {"name": "Lautsprecher Bad", "type": "AudioZoneV2", "uuidAction": "z-bad", "room": "r-bad", "details": {"playerid": 5, "server": "as"}},
    },
}


def test_loxone_audio_zone_plays_without_a_bereich(setup):
    house, area, dimmer, gate, sent = setup
    house.miniserver.structure = parse(ZONES)
    turn = Turn(area=area, lang="de", text="mach das Radio im Esszimmer an")
    result = run(house.brain.runner._tool_music({"action": "play", "areas": ["Esszimmer"]}, turn))
    assert result["results"] == [{"area": "Esszimmer", "ok": True}]
    assert sent[-1] == ("z-ess", "play")
    run(house.brain.runner._tool_music({"action": "pause", "areas": ["im Bad OG"]}, turn))
    assert sent[-1] == ("z-bad", "pause"), "the room name finds the zone too"
    failed = run(house.brain.runner.run("music", {"action": "play", "areas": ["Dachboden"]}, turn))
    assert "Esszimmer" in failed["error"] and "Lautsprecher Bad" in failed["error"]
    targets = house.brain.runner._target_areas(["alle"], turn)
    assert {t.zone_uuid for t in targets} == {"z-ess", "z-bad"}


def test_bereich_with_zone_wins_and_gemini_sees_all_zones(setup):
    house, area, dimmer, gate, sent = setup
    house.miniserver.structure = parse(ZONES)
    dining = house.areas.create("Essbereich")
    dining = house.areas.update(dining.id, zone_uuid="z-ess")
    targets = house.brain.runner._target_areas(["Esszimmer", "Essbereich"], Turn(area=area, lang="de", text=""))
    assert [t.id for t in targets] == [dining.id], "same zone only once, as the Bereich"
    system = house.brain.system_for(Turn(area=area, lang="de", text=""), False)
    assert "AUDIO-ZONEN IN LOXONE" in system and "Esszimmer" in system and "Lautsprecher Bad (Raum Bad OG)" in system


def test_butler_persona_and_default_anrede(setup):
    house, area, *_ = setup
    system = house.brain.system_for(Turn(area=area, lang="de", text=""), False)
    assert "J.A.R.V.I.S." in system and "britischen Butlers" in system
    assert 'Sprich die Person mit "Sir" an' in system
    house.settings.update({"house.default_anrede": ""})
    assert "keine Anrede wie Sir" in house.brain.system_for(Turn(area=area, lang="de", text=""), False)


def test_old_default_persona_follows_the_new_one(house):
    from jarvis.settings import DEFAULT_PERSONA, LEGACY_PERSONAS, Settings

    house.settings.update({"gemini.system_prompt": LEGACY_PERSONAS[0]})
    assert Settings(house.db).get("gemini.system_prompt") == DEFAULT_PERSONA
    house.settings.update({"gemini.system_prompt": "Eigener Text."})
    assert Settings(house.db).get("gemini.system_prompt") == "Eigener Text.", "own prompts stay untouched"


def test_blinds_dimming_and_time_without_gemini(setup):
    house, area, dimmer, gate, sent = setup
    house.brain._genai = lambda: (_ for _ in ()).throw(AssertionError("Gemini must not be asked"))
    shade = next(e for e in house.catalog.entries() if e.name == "Rollo Terrasse")
    house.catalog.update(shade.short_id, allowed=True)
    house.settings.update({"house.default_anrede": ""})
    session = AreaSession(area.id)
    reply, _ = run(house.coordinator.handle_text(session, area, "Fahr die Rollos im Wohnzimmer runter", "de", None))
    assert reply in [fill(t, raum="im Wohnzimmer") for t in DEFAULT["de"]["shade_down"]]
    assert sent[-1][0] == shade.uuid
    reply, _ = run(house.coordinator.handle_text(session, area, "Dimm das Licht im Wohnzimmer auf 30 Prozent", "de", None))
    assert "30 Prozent" in reply and "Wohnzimmer" in reply
    assert sent[-1][0] == dimmer.uuid
    reply, _ = run(house.coordinator.handle_text(session, area, "Wie spät ist es?", "de", None))
    assert "Uhr" in reply


def test_old_lead_silence_follows_the_new_default(house):
    from jarvis.settings import Settings

    house.settings.update({"announce.silence_ms": 600})
    assert Settings(house.db).get("announce.silence_ms") == 300
    house.settings.update({"announce.silence_ms": 800})
    assert Settings(house.db).get("announce.silence_ms") == 800


def test_earlier_recognition_defaults_move_to_kroko_once(house):
    from jarvis.db import dumps
    from jarvis.settings import Settings

    def stored(model, upgraded):
        house.db.execute("DELETE FROM settings WHERE key IN ('stt.model', 'stt.upgraded')")
        house.db.execute("INSERT INTO settings(key, value) VALUES(?, ?)", ("stt.model", dumps(model)))
        if upgraded is not None:
            house.db.execute("INSERT INTO settings(key, value) VALUES(?, ?)", ("stt.upgraded", dumps(upgraded)))
        return Settings(house.db).get("stt.model")

    assert stored("small", None) == "kroko", "the old default"
    assert stored("german", True) == "kroko", "the slow German Whisper is gone"
    assert stored("small", True) == "small", "small chosen after the first upgrade stays"
    assert stored("base", None) == "base"
    upgraded = Settings(house.db)
    upgraded.update({"stt.model": "small"})
    assert Settings(house.db).get("stt.model") == "small", "a choice after this upgrade stays"
