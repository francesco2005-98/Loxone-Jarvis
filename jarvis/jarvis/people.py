"""People in the household: name, Anrede, notes and an optional voice profile."""

from __future__ import annotations

import time
import uuid

import numpy as np

from .db import Database
from .voice.speaker import from_blob, to_blob

ANREDE = ("name", "sir", "mam")

ENROLL_SENTENCES = (
    "Jarvis, mach bitte das Licht im Wohnzimmer an und stell die Musik etwas leiser.",
    "Wie wird das Wetter morgen früh, und brauche ich einen Schirm?",
    "Erinnere mich heute Abend um acht daran, die Garage zu schließen.",
)


def _clean(person: dict) -> dict:
    return {
        "id": person["id"],
        "name": person["name"],
        "anrede": person["anrede"],
        "notes": person["notes"],
        "has_voice": person.get("embedding") is not None,
    }


class People:
    def __init__(self, db: Database):
        self._db = db

    def list(self) -> list[dict]:
        return [_clean(p) for p in self._db.all("SELECT * FROM people ORDER BY created_at")]

    def get(self, person_id: str) -> dict | None:
        row = self._db.one("SELECT * FROM people WHERE id = ?", (person_id,))
        return _clean(row) if row else None

    def create(self, name: str, anrede: str, notes: str) -> dict:
        name, anrede, notes = self._validate(name, anrede, notes)
        person_id = uuid.uuid4().hex[:10]
        self._db.execute(
            "INSERT INTO people(id, name, anrede, notes, created_at) VALUES(?,?,?,?,?)",
            (person_id, name, anrede, notes, time.time()),
        )
        return self.get(person_id) or {}

    def update(self, person_id: str, name: str, anrede: str, notes: str) -> dict | None:
        name, anrede, notes = self._validate(name, anrede, notes)
        self._db.execute(
            "UPDATE people SET name = ?, anrede = ?, notes = ? WHERE id = ?",
            (name, anrede, notes, person_id),
        )
        return self.get(person_id)

    def append_note(self, person_id: str, note: str) -> None:
        person = self._db.one("SELECT notes FROM people WHERE id = ?", (person_id,))
        if person is None:
            return
        notes = (person["notes"] + "\n" + note.strip()).strip()[-4000:]
        self._db.execute("UPDATE people SET notes = ? WHERE id = ?", (notes, person_id))

    def delete(self, person_id: str) -> None:
        self._db.execute("DELETE FROM people WHERE id = ?", (person_id,))

    def set_voice(self, person_id: str, profile: np.ndarray | None) -> None:
        self._db.execute(
            "UPDATE people SET embedding = ? WHERE id = ?",
            (to_blob(profile) if profile is not None else None, person_id),
        )

    def profiles(self) -> dict[str, np.ndarray]:
        rows = self._db.all("SELECT id, embedding FROM people WHERE embedding IS NOT NULL")
        return {row["id"]: from_blob(row["embedding"]) for row in rows}

    def address(self, person_id: str | None) -> tuple[str | None, str, str]:
        """(Anrede to use, person name, person notes)."""
        if not person_id:
            return None, "", ""
        row = self._db.one("SELECT name, anrede, notes FROM people WHERE id = ?", (person_id,))
        if not row:
            return None, "", ""
        anrede = {"sir": "Sir", "mam": "Ma'am"}.get(row["anrede"], row["name"])
        return anrede, row["name"], row["notes"]

    @staticmethod
    def _validate(name: str, anrede: str, notes: str) -> tuple[str, str, str]:
        name = (name or "").strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("Name mit 1 bis 60 Zeichen")
        if anrede not in ANREDE:
            raise ValueError("Anrede ist Name, Sir oder Mam")
        notes = (notes or "").strip()
        if len(notes) > 4000:
            raise ValueError("Notizen höchstens 4000 Zeichen")
        return name, anrede, notes
