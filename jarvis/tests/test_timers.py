import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jarvis.brain import quick
from jarvis.brain.tools import Turn
from jarvis.brain.when import human_duration, parse_clock, parse_duration
from jarvis.voice.arbitration import AreaSession


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("1 stunde", 3600),
        ("eine halbe stunde", 1800),
        ("anderthalb stunden", 5400),
        ("eine stunde und eine halbe", 5400),
        ("1 stunde und 30 minuten", 5400),
        ("1 stunde 30", 5400),
        ("zehn minuten", 600),
        ("fünfundzwanzig minuten", 1500),
        ("eine viertelstunde", 900),
        ("2,5 stunden", 9000),
        ("30 sekunden", 30),
        ("morgen früh", None),
        ("die nudeln 10 minuten", None),
    ],
)
def test_durations(text, seconds):
    assert parse_duration(text) == seconds


def test_clock_times_pick_the_next_moment():
    now = datetime(2026, 11, 13, 11, 50, tzinfo=ZoneInfo("Europe/Berlin"))
    assert parse_clock("18 uhr", now).hour == 18
    assert (parse_clock("halb sieben", now).hour, parse_clock("halb sieben", now).minute) == (18, 30)
    assert parse_clock("7", now).hour == 19, "7 o'clock in the evening, the morning has passed"
    assert parse_clock("10:15", now).day == 14, "already past today, so tomorrow"
    assert human_duration(5400) == "anderthalb Stunden" and human_duration(3600, "en") == "1 hour"


@pytest.fixture
def area(house):
    house.settings.update({"house.default_anrede": ""})
    return house.areas.create("Küche")


def test_the_dinner_timer_runs_without_gemini(house, area):
    house.brain._genai = lambda: (_ for _ in ()).throw(AssertionError("no Gemini"))
    session = AreaSession(area.id)
    before = time.time()
    reply, done = run(
        house.coordinator.handle_text(session, area, "Jarvis stell mir einen Timer in 1 Stunde, da ist das Essen fertig", "de", None)
    )
    timers = house.timers.list(area.id)
    assert len(timers) == 1
    timer = timers[0]
    assert timer["kind"] == "timer" and timer["label"] == "Essen" and timer["text"] == "da ist das Essen fertig"
    assert before + 3590 < timer["due_at"] < time.time() + 3610
    assert "eine Stunde" in reply and done == ["timer Essen"]


def test_reminder_with_clock_and_reason(house, area):
    reply, _ = run(
        house.coordinator.handle_text(AreaSession(area.id), area, "Erinnere mich um 18 Uhr daran, dass ich den Müll rausbringe", "de", None)
    )
    timer = house.timers.list(area.id)[0]
    assert timer["kind"] == "reminder" and timer["text"] == "dass ich den Müll rausbringe"
    assert "um 18 Uhr" in reply


def test_remaining_time_and_cancel(house, area):
    session = AreaSession(area.id)
    run(house.coordinator.handle_text(session, area, "Stell einen Timer für 20 Minuten für die Nudeln", "de", None))
    reply, _ = run(house.coordinator.handle_text(session, area, "Wie lange läuft der Timer noch?", "de", None))
    assert "Minuten für Nudeln" in reply
    reply, _ = run(house.coordinator.handle_text(session, area, "Brich den Timer ab", "de", None))
    assert house.timers.list(area.id) == []
    reply, _ = run(house.coordinator.handle_text(session, area, "Wie lange läuft der Timer noch?", "de", None))
    assert "kein Timer" in reply


def test_due_message_in_the_personality(house, area):
    timer = {"kind": "timer", "label": "Essen", "text": "da ist das Essen fertig", "lang": "de"}
    house.secrets.delete("gemini.api_key")
    message = run(house.due_message(timer))
    assert "Essen" in message
    house.secrets.set("gemini.api_key", "test")
    seen = {}

    async def due_sentence(t, anrede):
        seen["timer"] = t
        return "Verzeihung, das Essen dürfte jetzt fertig sein."

    house.brain.due_sentence = due_sentence
    assert run(house.due_message(timer)) == "Verzeihung, das Essen dürfte jetzt fertig sein."
    assert seen["timer"]["text"] == "da ist das Essen fertig"

    async def broken(t, anrede):
        raise RuntimeError("offline")

    house.brain.due_sentence = broken
    assert "Essen" in run(house.due_message(timer)), "falls back to the phrase book"


def test_unclear_timer_goes_to_gemini():
    assert quick.parse("Mach einen Timer morgen früh") is None
    assert quick.parse("Stell den Timer auf die Nudeln") is None
