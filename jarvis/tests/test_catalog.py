import json

import pytest

from jarvis.db import Database
from jarvis.loxone.catalog import ActionError, Catalog
from jarvis.loxone.structure import parse

from .conftest import SAMPLE_STRUCTURE


@pytest.fixture
def catalog():
    db = Database(":memory:")
    cat = Catalog(db)
    cat.sync(parse(SAMPLE_STRUCTURE))
    return cat


def states(values):
    return lambda uuid, name: values.get((uuid, name))


def test_structure_flattens_and_blocks_door_openers(catalog):
    structure = catalog.structure
    assert structure.audioserver_host() == "192.168.1.40"
    assert structure.audioservers() == [{"uuid": "ms-audio", "name": "192.168.1.40", "host": "192.168.1.40"}]
    names = {e.name for e in catalog.entries()}
    assert "Deckenlicht" in names and "Rollo Terrasse" in names
    assert "Tür" not in names, "Intercom subtree must stay out"
    assert "Türöffner Haustür" not in names, "door openers stay out"
    assert "Master" not in names, "masterValue is internal"
    assert "Wohnzimmer Audio" not in names, "audio zones go through the music tool"


def test_short_ids_are_stable_across_sync(catalog):
    before = {e.uuid: e.short_id for e in catalog.entries()}
    catalog.sync(parse(SAMPLE_STRUCTURE))
    after = {e.uuid: e.short_id for e in catalog.entries()}
    assert before == after
    assert all(sid[0] in "LKBTAX" for sid in after.values())


def test_defaults_nothing_allowed_and_gate_alarm_critical(catalog):
    entries = {e.name: e for e in catalog.entries()}
    assert not any(e.allowed for e in entries.values())
    assert entries["Garagentor"].critical
    assert entries["Alarmanlage"].critical and entries["Alarmanlage"].secured
    assert not entries["Deckenlicht"].critical


def test_secured_cannot_be_allowed_and_gate_stays_critical(catalog):
    alarm = next(e for e in catalog.entries() if e.name == "Alarmanlage")
    with pytest.raises(ActionError):
        catalog.update(alarm.short_id, allowed=True)
    gate = next(e for e in catalog.entries() if e.name == "Garagentor")
    with pytest.raises(ActionError):
        catalog.update(gate.short_id, critical=False)


def test_aliases_are_deduplicated(catalog):
    entry = next(e for e in catalog.entries() if e.name == "Indirekte Beleuchtung TV")
    catalog.update(entry.short_id, aliases=["Fernsehlicht", "fernsehlicht", " ", "Licht am TV"])
    assert catalog.get(entry.short_id).aliases == ["Fernsehlicht", "Licht am TV"]


def test_commands(catalog):
    e = {x.name: x for x in catalog.entries()}
    moods = json.dumps([{"name": "Hell", "id": 777}, {"name": "Abend", "id": 3}, {"name": "Aus", "id": 778}])
    st = states({(e["Licht Wohnzimmer"].uuid, "moodList"): moods})
    lc = e["Licht Wohnzimmer"]
    assert catalog.commands(lc, "on", None, None, st) == [(lc.uuid, "changeTo/777")]
    assert catalog.commands(lc, "off", None, None, st) == [(lc.uuid, "changeTo/778")]
    assert catalog.commands(lc, "scene", "abend", None, st) == [(lc.uuid, "changeTo/3")]
    assert catalog.commands(lc, "dim", "40", None, st) == [(lc.uuid + "/masterValue", "40")]
    with pytest.raises(ActionError):
        catalog.commands(lc, "scene", "Kino", None, st)
    dimmer = e["Deckenlicht"]
    assert catalog.commands(dimmer, "dim", 150, None, st) == [(dimmer.uuid, "100")]
    jal = e["Rollo Terrasse"]
    assert catalog.commands(jal, "down", None, None, st) == [(jal.uuid, "FullDown")]
    assert catalog.commands(jal, "position", "30", None, st) == [(jal.uuid, "manualPosition/30")]
    irc = e["Raumregler Wohnzimmer"]
    assert catalog.commands(irc, "temperature", "21.5", None, st) == [(irc.uuid, "setComfortTemperature/21.5")]
    with pytest.raises(ActionError):
        catalog.commands(jal, "temperature", 20, None, st)
    with pytest.raises(ActionError):
        catalog.commands(dimmer, "dim", None, None, st)


def test_describe_state(catalog):
    e = {x.name: x for x in catalog.entries()}
    st = states(
        {
            (e["Deckenlicht"].uuid, "position"): 60.0,
            (e["Rollo Terrasse"].uuid, "position"): 0.25,
            (e["Raumregler Wohnzimmer"].uuid, "tempActual"): 21.4,
            (e["Raumregler Wohnzimmer"].uuid, "tempTarget"): 22.0,
            (e["Garagentor"].uuid, "position"): 0.0,
        }
    )
    assert catalog.describe_state(e["Deckenlicht"], st) == "60 %"
    assert catalog.describe_state(e["Rollo Terrasse"], st) == "25 % geschlossen"
    assert catalog.describe_state(e["Raumregler Wohnzimmer"], st).startswith("ist 21.4")
    assert catalog.describe_state(e["Garagentor"], st) == "geschlossen"


def test_counts(catalog):
    moods = json.dumps([{"name": "Hell", "id": 777}, {"name": "Aus", "id": 778}])
    lc = next(x for x in catalog.entries() if x.name == "Licht Wohnzimmer")
    counts = catalog.counts(states({(lc.uuid, "moodList"): moods}))
    assert counts["light"] == 3 and counts["shading"] == 1 and counts["climate"] == 1
    assert counts["scenes"] == 1
