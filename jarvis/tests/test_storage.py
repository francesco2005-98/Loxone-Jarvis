import time

import pytest

from jarvis.crypto import SecretStore
from jarvis.db import Database
from jarvis.settings import Settings
from jarvis.timers import TimerService


def test_secret_roundtrip_and_wrong_key(tmp_path):
    db = Database(tmp_path / "db.sqlite3")
    store = SecretStore(db, "x" * 32, tmp_path)
    store.set("gemini.api_key", "AIza123")
    assert store.get("gemini.api_key") == "AIza123"
    raw = db.one("SELECT ciphertext FROM secrets WHERE key='gemini.api_key'")["ciphertext"]
    assert b"AIza123" not in raw
    store.set("gemini.api_key", "")
    assert not store.has("gemini.api_key")
    with pytest.raises(SystemExit):
        SecretStore(db, "y" * 32, tmp_path)


def test_timers(tmp_path):
    db = Database(":memory:")
    settings = Settings(db)
    timers = TimerService(db, settings)
    t = timers.add(kind="timer", due_at=time.time() + 600, label="Nudeln", text="", bereich_id="a1", lang="de")
    assert timers.list("a1")[0]["label"] == "Nudeln"
    assert timers.cancel(bereich_id="a1", label="nudeln") == 1
    with pytest.raises(ValueError):
        timers.add(kind="timer", due_at=time.time() - 5, label="x", text="", bereich_id=None, lang="de")
    due = timers.parse_at("2030-01-01T08:00")
    assert abs(due - 1893481200) < 3600 * 2
    assert t["id"]
