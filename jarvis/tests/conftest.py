import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_SECRET_KEY", "test-secret-key-0123456789abcdef")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JARVIS_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("JARVIS_INSECURE_COOKIES", "1")
    return tmp_path


@pytest.fixture
def house(env):
    from jarvis.config import Config
    from jarvis.house import House

    instance = House(Config.from_env())
    yield instance
    instance.db.close()


SAMPLE_STRUCTURE = {
    "lastModified": "2026-11-01 10:00:00",
    "msInfo": {"serialNr": "504F00000001", "msName": "Zuhause"},
    "rooms": {
        "r-wohn": {"name": "Wohnzimmer"},
        "r-schlaf": {"name": "Schlafzimmer"},
        "r-garage": {"name": "Garage"},
    },
    "cats": {
        "c-licht": {"name": "Beleuchtung", "type": "lights"},
        "c-schatten": {"name": "Beschattung", "type": "shading"},
    },
    "mediaServer": {"ms-audio": {"type": 2, "host": "192.168.1.40:7091"}},
    "controls": {
        "lc-wohn": {
            "name": "Licht Wohnzimmer",
            "type": "LightControllerV2",
            "uuidAction": "10000000-0000-0001-ffff000000000001",
            "room": "r-wohn",
            "cat": "c-licht",
            "states": {"activeMoods": "s-0001", "moodList": "s-0002"},
            "subControls": {
                "sub1": {
                    "name": "Deckenlicht",
                    "type": "Dimmer",
                    "uuidAction": "10000000-0000-0001-ffff000000000002",
                    "states": {"position": "s-0003"},
                },
                "sub2": {
                    "name": "Indirekte Beleuchtung TV",
                    "type": "Switch",
                    "uuidAction": "10000000-0000-0001-ffff000000000003",
                    "states": {"active": "s-0004"},
                },
                "master": {
                    "name": "Master",
                    "type": "Dimmer",
                    "uuidAction": "10000000-0000-0001-ffff000000000001/masterValue",
                    "states": {"position": "s-0005"},
                },
            },
        },
        "jal-wohn": {
            "name": "Rollo Terrasse",
            "type": "Jalousie",
            "uuidAction": "10000000-0000-0002-ffff000000000001",
            "room": "r-wohn",
            "cat": "c-schatten",
            "states": {"position": "s-0010"},
        },
        "irc-wohn": {
            "name": "Raumregler Wohnzimmer",
            "type": "IRoomControllerV2",
            "uuidAction": "10000000-0000-0003-ffff000000000001",
            "room": "r-wohn",
            "states": {"tempActual": "s-0020", "tempTarget": "s-0021"},
        },
        "gate": {
            "name": "Garagentor",
            "type": "Gate",
            "uuidAction": "10000000-0000-0004-ffff000000000001",
            "room": "r-garage",
            "states": {"position": "s-0030"},
        },
        "alarm": {
            "name": "Alarmanlage",
            "type": "Alarm",
            "uuidAction": "10000000-0000-0005-ffff000000000001",
            "isSecured": True,
            "states": {"armed": "s-0040"},
        },
        "intercom": {
            "name": "Türklingel",
            "type": "IntercomV2",
            "uuidAction": "10000000-0000-0006-ffff000000000001",
            "subControls": {
                "door": {"name": "Tür", "type": "Pushbutton", "uuidAction": "10000000-0000-0006-ffff000000000002"}
            },
        },
        "opener": {
            "name": "Türöffner Haustür",
            "type": "Pushbutton",
            "uuidAction": "10000000-0000-0007-ffff000000000001",
        },
        "zone": {
            "name": "Wohnzimmer Audio",
            "type": "AudioZoneV2",
            "uuidAction": "10000000-0000-0008-ffff000000000001",
            "room": "r-wohn",
            "details": {"playerid": 3, "server": "ms-audio"},
            "states": {"volume": "s-0050"},
        },
    },
}
