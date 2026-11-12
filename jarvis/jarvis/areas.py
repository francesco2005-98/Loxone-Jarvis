"""Bereiche: listening and answer groups. One Audioserver zone, any number of mics."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from .db import Database, dumps, loads


@dataclass
class Area:
    id: str
    name: str
    zone_uuid: str | None
    main_light: str | None
    rooms: list[str] = field(default_factory=list)
    sort: int = 0

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "zone_uuid": self.zone_uuid,
            "main_light": self.main_light,
            "rooms": self.rooms,
        }


def _row(row: dict) -> Area:
    return Area(row["id"], row["name"], row["zone_uuid"], row["main_light"], loads(row["rooms"], []), row["sort"])


class Areas:
    def __init__(self, db: Database):
        self._db = db

    def list(self) -> list[Area]:
        return [_row(r) for r in self._db.all("SELECT * FROM bereiche ORDER BY sort, name")]

    def get(self, area_id: str | None) -> Area | None:
        if not area_id:
            return None
        row = self._db.one("SELECT * FROM bereiche WHERE id = ?", (area_id,))
        return _row(row) if row else None

    def find(self, name: str) -> Area | None:
        wanted = _norm(name)
        areas = self.list()
        for area in areas:
            if _norm(area.name) == wanted:
                return area
        for area in areas:
            if wanted and (wanted in _norm(area.name) or _norm(area.name) in wanted):
                return area
        for area in areas:
            if any(_norm(room) == wanted for room in area.rooms):
                return area
        return None

    def create(self, name: str) -> Area:
        name = _validate_name(name)
        area_id = uuid.uuid4().hex[:10]
        sort = (self._db.one("SELECT COALESCE(MAX(sort), 0) + 1 AS n FROM bereiche") or {"n": 1})["n"]
        self._db.execute(
            "INSERT INTO bereiche(id, name, zone_uuid, main_light, rooms, sort) VALUES(?,?,?,?,?,?)",
            (area_id, name, None, None, "[]", sort),
        )
        return self.get(area_id)  # type: ignore[return-value]

    def update(self, area_id: str, **fields) -> Area:
        area = self.get(area_id)
        if area is None:
            raise KeyError(area_id)
        if "name" in fields:
            area.name = _validate_name(fields["name"])
        if "zone_uuid" in fields:
            area.zone_uuid = fields["zone_uuid"] or None
        if "main_light" in fields:
            area.main_light = (fields["main_light"] or None) and str(fields["main_light"]).upper()
        if "rooms" in fields:
            area.rooms = [str(r) for r in (fields["rooms"] or [])][:20]
        self._db.execute(
            "UPDATE bereiche SET name=?, zone_uuid=?, main_light=?, rooms=? WHERE id=?",
            (area.name, area.zone_uuid, area.main_light, dumps(area.rooms), area_id),
        )
        return area

    def delete(self, area_id: str) -> None:
        self._db.execute("UPDATE mics SET bereich_id = NULL WHERE bereich_id = ?", (area_id,))
        self._db.execute("DELETE FROM bereiche WHERE id = ?", (area_id,))


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "", (text or "").lower())


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not 1 <= len(name) <= 40:
        raise ValueError("Name mit 1 bis 40 Zeichen")
    return name
