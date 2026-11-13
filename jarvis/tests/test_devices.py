import asyncio

import pytest

from jarvis.brain import quick
from jarvis.brain.tools import Turn
from jarvis.loxone.client import NotConnected
from jarvis.loxone.protocol import Reply
from jarvis.loxone.structure import parse

STRUCTURE = {
    "rooms": {"r-wohn": {"name": "Wohnzimmer"}, "r-bad": {"name": "Bad"}, "r-out": {"name": "Außen"}},
    "controls": {
        "jal": {"name": "Rollo Süd", "type": "Jalousie", "uuidAction": "jal", "room": "r-wohn", "states": {"position": "s-jp", "shadePosition": "s-js"}},
        "win": {"name": "Dachfenster", "type": "Window", "uuidAction": "win", "room": "r-bad", "states": {"position": "s-wp"}},
        "gate": {"name": "Hoftor", "type": "Gate", "uuidAction": "gate", "states": {"position": "s-gp"}},
        "dim": {"name": "Spots", "type": "Dimmer", "uuidAction": "dim", "room": "r-wohn", "cat": "c-l", "states": {"position": "s-dp"}},
        "sw": {"name": "Steckdose Lampe", "type": "Switch", "uuidAction": "sw", "room": "r-wohn", "states": {"active": "s-sa"}},
        "sld": {"name": "Lüftung Stufe", "type": "Slider", "uuidAction": "sld", "details": {"min": 0, "max": 4, "format": "%.0f Stufe"}, "states": {"value": "s-lv"}},
        "irc": {"name": "Raumregler Bad", "type": "IRoomControllerV2", "uuidAction": "irc", "room": "r-bad", "states": {"tempActual": "s-ta", "tempTarget": "s-tt", "operatingMode": "s-om"}},
        "tin": {"name": "Temperatur Wohnzimmer", "type": "InfoOnlyAnalog", "uuidAction": "tin", "room": "r-wohn", "details": {"format": "%.1f°C"}, "states": {"value": "s-tv"}},
        "tout": {"name": "Außentemperatur", "type": "InfoOnlyAnalog", "uuidAction": "tout", "room": "r-out", "details": {"format": "%.1f°C"}, "states": {"value": "s-to"}},
        "dig": {"name": "Regen", "type": "InfoOnlyDigital", "uuidAction": "dig", "details": {"text": {"on": "Es regnet", "off": "trocken"}}, "states": {"active": "s-rain"}},
        "met": {"name": "Hausverbrauch", "type": "Meter", "uuidAction": "met", "details": {"actualFormat": "%.2fkW", "totalFormat": "%.0fkWh"}, "states": {"actual": "s-ma", "total": "s-mt"}},
        "wm": {"name": "Fensterüberwachung", "type": "WindowMonitor", "uuidAction": "wm", "states": {"numOpen": "s-no", "numTilted": "s-nt", "numClosed": "s-nc"}},
        "pd": {"name": "Präsenz Bad", "type": "PresenceDetector", "uuidAction": "pd", "room": "r-bad", "states": {"active": "s-pa"}},
        "alarm": {"name": "Alarmanlage", "type": "Alarm", "uuidAction": "alarm", "states": {"armed": "s-ar"}},
    },
    "cats": {"c-l": {"name": "Beleuchtung", "type": "lights"}},
}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def home(house):
    structure = parse(STRUCTURE)
    house.miniserver.structure = structure
    house.catalog.sync(structure)
    for entry in house.catalog.entries():
        if entry.group != "sensor":
            house.catalog.update(entry.short_id, allowed=True)
    house.miniserver.values.update(
        {
            "s-jp": 0.4, "s-js": 0.5, "s-wp": 0.3, "s-gp": 1.0, "s-dp": 0, "s-sa": 1, "s-lv": 2,
            "s-ta": 22.34, "s-tt": 21.5, "s-om": 0, "s-tv": 21.47, "s-to": 7.2, "s-rain": 1,
            "s-ma": 1.237, "s-mt": 4321.4, "s-no": 2, "s-nt": 1, "s-nc": 9, "s-pa": 0, "s-ar": 0,
        }
    )
    area = house.areas.create("Wohnbereich")
    area = house.areas.update(area.id, rooms=["Wohnzimmer"])
    entries = {e.name: e for e in house.catalog.entries()}
    return house, area, entries


def cmds(house, entry, action, value=None, value2=None):
    return house.catalog.commands(entry, action, value, value2, house.miniserver.state)


def test_action_words_are_normalized_per_block(home):
    house, _, e = home
    assert cmds(house, e["Rollo Süd"], "open") == [("jal", "FullUp")]
    assert cmds(house, e["Rollo Süd"], "close") == [("jal", "FullDown")]
    assert cmds(house, e["Rollo Süd"], "set", "40") == [("jal", "manualPosition/40")]
    assert cmds(house, e["Rollo Süd"], "lamellen", 30) == [("jal", "manualLamelle/30")]
    assert cmds(house, e["Hoftor"], "auf") == [("gate", "open")]
    assert cmds(house, e["Spots"], "set", "40") == [("dim", "40")]
    assert cmds(house, e["Spots"], "dim", "0") == [("dim", "off")]
    assert cmds(house, e["Spots"], "on", "55") == [("dim", "55")]
    assert cmds(house, e["Lüftung Stufe"], "dim", 9) == [("sld", "4")], "clamped to the slider range"
    assert cmds(house, e["Raumregler Bad"], "set", "21") == [("irc", "setComfortTemperature/21.0")]
    assert cmds(house, e["Alarmanlage"], "on") == [("alarm", "on/1")]
    assert cmds(house, e["Steckdose Lampe"], "toggle") == [("sw", "off")], "toggle reads the current state"


def test_window_block(home):
    house, _, e = home
    window = e["Dachfenster"]
    assert window.group == "window" and window.short_id.startswith("F")
    assert cmds(house, window, "open") == [("win", "fullopen")]
    assert cmds(house, window, "zu") == [("win", "fullclose")]
    assert cmds(house, window, "position", 25) == [("win", "moveToPosition/25")]
    assert house.catalog.describe_state(window, house.miniserver.state) == "30 % offen"


def test_states_read_like_a_person_would_say_them(home):
    house, _, e = home
    describe = lambda name: house.catalog.describe_state(e[name], house.miniserver.state)  # noqa: E731
    assert describe("Rollo Süd") == "40 % geschlossen, Lamellen 50 %"
    assert describe("Temperatur Wohnzimmer") == "21.5°C"
    assert describe("Regen") == "Es regnet"
    assert describe("Hausverbrauch") == "aktuell 1.24kW, gesamt 4321kWh"
    assert describe("Fensterüberwachung") == "2 offen, 1 gekippt, 9 zu"
    assert describe("Präsenz Bad") == "niemand erkannt"
    assert describe("Raumregler Bad") == "ist 22.3 °C, Soll 21.5 °C, Automatik"
    assert describe("Lüftung Stufe") == "2 Stufe"


def test_sensors_are_readable_but_never_switch(home):
    house, area, e = home
    sensor = e["Außentemperatur"]
    assert sensor.group == "sensor" and sensor.allowed, "reading is harmless, so sensors start enabled"
    with pytest.raises(Exception, match="nur zum Ablesen"):
        cmds(house, sensor, "on")
    system = house.brain.system_for(Turn(area=area, lang="de", text=""), False)
    assert f"{sensor.short_id}: 7.2°C" in system
    assert "Sensor/InfoOnlyAnalog" in system and "nur zum Ablesen" in system
    rollo_line = next(l for l in system.splitlines() if l.startswith(e["Rollo Süd"].short_id + " |"))
    assert "slat = Lamellen" in rollo_line


def test_temperature_questions_are_answered_locally(home):
    house, area, e = home
    house.settings.update({"house.default_anrede": ""})
    turn = Turn(area=area, lang="de", text="Wie warm ist es im Bad?")
    reply = run(house.coordinator._quick(turn))
    assert "22,3 Grad" in reply and "Bad" in reply
    reply = run(house.coordinator._quick(Turn(area=area, lang="de", text="Wie warm ist es draußen?")))
    assert "7,2 Grad" in reply and "draußen" in reply
    reply = run(house.coordinator._quick(Turn(area=area, lang="de", text="Wie warm ist es?")))
    assert "21,5 Grad" in reply, "without a room: the sensor of the Bereich"
    assert quick.parse("Wie warm wird es morgen?") is None


def test_command_is_retried_after_a_short_reconnect(home):
    house, area, e = home
    calls = []

    async def command(uuid, cmd):
        calls.append(cmd)
        if len(calls) == 1:
            house.miniserver.connected_flag = True
            raise NotConnected("weg")
        return Reply("ok", "1", 200)

    house.miniserver.command = command
    cls = type(house.miniserver)
    original = cls.__dict__["connected"]
    cls.connected = property(lambda self: getattr(self, "connected_flag", False))
    try:
        result = run(house.brain.runner.execute(e["Spots"], "on", None, None, None))
    finally:
        cls.connected = original
    assert result["ok"] and calls == ["on", "on"]


def test_offer_accepts_natural_action_words(home):
    house, area, e = home
    turn = Turn(area=area, lang="de", text="")
    offer = house.brain.runner.offer({"question": "Rollos zu?", "actions": [{"device": e["Rollo Süd"].short_id, "action": "close"}]}, turn)
    assert offer is not None and offer.actions[0][1] == "down"


def test_blinds_up_and_down_are_not_confused():
    from jarvis.brain import quick

    assert quick.parse("Fahr die Rollos im Wohnzimmer auf").action == "up"
    assert quick.parse("Fahr die Rollos im Wohnzimmer ganz auf").action == "up"
    assert quick.parse("Fahr die Rollos im Wohnzimmer ganz auf").room == "wohnzimmer"
    assert quick.parse("Mach die Rollos im Wohnzimmer zu").action == "down"
    assert quick.parse("Fahr die Rollos im Wohnzimmer ganz zu").room == "wohnzimmer"


def test_rgb_scene_state_is_shown(home):
    house, _, e = home
    rgb = next(iter(e.values()))
    house.catalog.structure.controls["rgb"] = type(house.catalog.structure.controls["jal"])(
        uuid="rgb", name="Farblicht", type="LightsceneRGB", room="Wohnzimmer", room_uuid="r-wohn",
        category=None, cat_type=None, parent=None, states={"activeScene": "s-rgb"}, details={"sceneList": {"1": "Abend"}},
    )
    house.catalog.sync(house.catalog.structure)
    house.miniserver.values["s-rgb"] = 1
    entry = next(x for x in house.catalog.entries() if x.name == "Farblicht")
    assert house.catalog.describe_state(entry, house.miniserver.state) == "Szene Abend"


def test_a_critical_question_survives_an_unclear_answer(home):
    house, area, e = home
    from jarvis.brain.tools import Pending
    from jarvis.voice.arbitration import AreaSession

    session = AreaSession(area.id)
    gate = e["Hoftor"]
    session.pending = Pending("control", "Tor öffnen?", [(gate, "open", None, None)])
    reply, _ = run(house.coordinator.handle_text(session, area, "Wie meinst du das?", "de", None))
    assert session.pending is not None and session.pending.kind == "control", "the question stays open"
    run(house.coordinator.handle_text(session, area, "Ja", "de", None))
    assert session.pending is None, "the yes was taken and the question is closed"
