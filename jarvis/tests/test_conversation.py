"""Conversation holds: the server decides the action, Gemini only the words."""

import asyncio
import json

from jarvis.brain.affair import reaction
from jarvis.brain.gemini import BrainUnavailable
from jarvis.loxone.protocol import Reply
from jarvis.loxone.structure import parse
from jarvis.voice.arbitration import AreaSession

STRUCTURE = {
    "lastModified": "2026-11-01 10:00:00",
    "msInfo": {"serialNr": "504F00000001", "msName": "Zuhause"},
    "rooms": {"r-wohn": {"name": "Wohnzimmer"}},
    "cats": {"c-licht": {"name": "Beleuchtung", "type": "lights"}},
    "controls": {
        "lc": {
            "name": "Licht Wohnzimmer",
            "type": "LightControllerV2",
            "uuidAction": "10000000-0000-0001-ffff000000000001",
            "room": "r-wohn",
            "cat": "c-licht",
            "states": {"activeMoods": "s-active", "moodList": "s-moods"},
        },
        "door": {
            "name": "Terrassentür",
            "type": "Window",
            "uuidAction": "10000000-0000-0002-ffff000000000001",
            "room": "r-wohn",
            "states": {"position": "s-door"},
        },
    },
}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def prepare(house, door_open=True):
    structure = parse(STRUCTURE)
    house.miniserver.structure = structure
    house.catalog.sync(structure)
    light = next(e for e in house.catalog.entries() if e.type == "LightControllerV2")
    door = next(e for e in house.catalog.entries() if e.name == "Terrassentür")
    house.catalog.update(light.short_id, allowed=True)
    house.catalog.update(door.short_id, allowed=True)
    light = house.catalog.get(light.short_id)
    house.miniserver.texts["s-moods"] = json.dumps([{"id": 0, "name": "Aus"}, {"id": 7, "name": "Gute Nacht"}])
    house.miniserver.values["s-door"] = 1.0 if door_open else 0.0
    area = house.areas.create("Wohnen")
    area = house.areas.update(area.id, rooms=["Wohnzimmer"])
    person = house.people.create("Luca", "sir", "")
    sent = []

    async def command(uuid, cmd):
        sent.append((uuid, cmd))
        return Reply("ok", "1", 200)

    house.miniserver.command = command
    house.miniserver.status = "verbunden"
    return area, person["id"], sent, light


def scripted(house, lines):
    spoken = []

    async def speak(turn, history, instruction):
        spoken.append((turn.text, instruction))
        for needle, line in lines:
            if needle in turn.text:
                return line
        return lines[-1][1]

    async def answer(turn, history):
        spoken.append((turn.text, "answer"))
        return "Bis neun, Sir.", history

    house.brain.speak = speak
    house.brain.answer = answer
    return spoken


def test_reaction_reads_the_hint_without_a_phrase_book():
    assert reaction("Ich weiß") == "ack"
    assert reaction("Die bleibt heute offen") == "ack"
    assert reaction("Ich weiß, wir sitzen noch draußen") == "ack"
    assert reaction("Welche Tür?") == "question"
    assert reaction("Regnet es denn heute Nacht?") == "question"
    assert reaction("Warte, ich mache sie noch zu") == "pause"
    assert reaction("Lass es für heute") == "cancel"
    assert reaction("Was würde ich nur ohne dich tun") == "unclear"


def test_good_night_waits_for_the_open_door_and_runs_once(house):
    area, person, sent, light = prepare(house)
    spoken = scripted(house, [("Gute Nacht", "Darf ich anmerken, dass die Terrassentür noch offen ist, Sir?"), ("Ich weiß", "Wie Sie wünschen. Gute Nacht.")])
    session = AreaSession(area.id)
    reply, done = run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    assert sent == []
    assert session.affair is not None and not session.affair.executed
    assert "Terrassentür" in reply
    reply, done = run(house.coordinator.handle_text(session, area, "Ich weiß", "de", person))
    assert done and sent == [(light.uuid, "changeTo/7")]
    assert reply == "Wie Sie wünschen. Gute Nacht."
    assert "aktiviert" not in reply.lower()
    again, _ = run(house.coordinator.handle_text(session, area, "Ich weiß", "de", person))
    assert again == ""
    assert sent == [(light.uuid, "changeTo/7")]
    assert spoken[1][0] == "Ich weiß"


def test_the_same_words_do_not_close_a_door_that_was_explicitly_offered(house):
    area, person, sent, _light = prepare(house)
    scripted(house, [("Gute Nacht", "Soll ich die Tür schließen, Sir?")])
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    reply, done = run(house.coordinator.handle_text(session, area, "Ich weiß", "de", person))
    assert sent == [] and done == []
    assert session.affair is not None and not session.affair.executed and session.affair.asks_to_close
    assert reply


def test_a_personal_detail_changes_the_closing_not_the_action(house):
    area, person, sent, light = prepare(house)
    spoken = scripted(
        house,
        [
            ("Gute Nacht", "Darf ich anmerken, dass die Terrassentür noch offen ist, Sir?"),
            ("draußen", "Dann wünsche ich Ihnen noch einen schönen Abend, Sir."),
        ],
    )
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    reply, done = run(house.coordinator.handle_text(session, area, "Ich weiß, wir sitzen noch draußen", "de", person))
    assert sent == [(light.uuid, "changeTo/7")]
    assert "Abend" in reply
    assert "draußen" in spoken[-1][0]


def test_a_question_keeps_the_scene_waiting(house):
    area, person, sent, _light = prepare(house)
    scripted(house, [("Gute Nacht", "Die Terrassentür ist noch offen, Sir.")])
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    reply, done = run(house.coordinator.handle_text(session, area, "Welche Tür?", "de", person))
    assert sent == [] and done == []
    assert session.affair is not None and not session.affair.executed
    assert reply
    reply, done = run(house.coordinator.handle_text(session, area, "Regnet es denn heute Nacht?", "de", person))
    assert sent == [] and session.affair is not None


def test_cancel_and_another_person_do_not_run_the_scene(house):
    area, person, sent, _light = prepare(house)
    other = house.people.create("Gast", "mam", "")["id"]
    scripted(house, [("Gute Nacht", "Die Terrassentür ist noch offen."), ("heute", "Dann lasse ich es für heute.")])
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    run(house.coordinator.handle_text(session, area, "Ich weiß", "de", other))
    assert sent == [] and session.affair is not None
    reply, done = run(house.coordinator.handle_text(session, area, "Lass es für heute", "de", person))
    assert sent == [] and done == []
    assert session.affair is None
    assert reply


def test_a_closed_door_starts_the_scene_immediately(house):
    area, person, sent, light = prepare(house, door_open=False)
    spoken = scripted(house, [("x", "sollte nicht drankommen")])
    session = AreaSession(area.id)
    reply, _done = run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    assert sent == [(light.uuid, "changeTo/7")]
    assert session.affair is None
    assert reply
    assert spoken == []


def test_gemini_failing_after_the_scene_stays_quiet(house):
    area, person, sent, light = prepare(house)

    async def speak(turn, history, instruction):
        if "genau einmal" in instruction:
            raise BrainUnavailable("timeout")
        return "Die Terrassentür ist noch offen, Sir."

    house.brain.speak = speak
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Gute Nacht", "de", person))
    reply, done = run(house.coordinator.handle_text(session, area, "Ich weiß", "de", person))
    assert sent == [(light.uuid, "changeTo/7")]
    assert done
    assert reply == ""
    assert "Gemini" not in reply


def test_a_new_situation_is_not_scripted_in_the_prompt(house):
    area, person, _sent, _light = prepare(house, door_open=False)
    captured = {}

    async def answer(turn, history):
        captured["system"] = house.brain.system_for(turn, False)
        captured["user"] = turn.conversation + "\n" + turn.text
        return "Vermutlich selbst nachsehen, Sir.", history

    house.brain.answer = answer
    session = AreaSession(area.id)
    session.last_spoken = "Nur noch im Flur."
    session.last_playback = "gehört"
    reply, done = run(house.coordinator.handle_text(session, area, "Was würde ich nur ohne dich tun, Jarvis?", "de", person))
    assert reply.startswith("Vermutlich")
    assert done == []
    assert "Was würde ich nur ohne dich" not in captured["system"]
    assert "Formuliere aus diesem Gespräch" in captured["system"]
    assert "Nur noch im Flur" in captured["user"]
