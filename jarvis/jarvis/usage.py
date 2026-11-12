"""Monthly Gemini usage. Shown in the overview, never used to stop anything."""

from __future__ import annotations

from datetime import datetime

from .db import Database
from .settings import Settings

FREE_SEARCHES = 5000


class Usage:
    def __init__(self, db: Database, settings: Settings):
        self._db = db
        self._settings = settings

    def _month(self) -> str:
        return datetime.now(self._settings.tz).strftime("%Y-%m")

    def record(self, *, input_tokens: int = 0, output_tokens: int = 0, searches: int = 0) -> None:
        self._db.execute(
            "INSERT INTO usage(month, requests, input_tokens, output_tokens, search_queries) "
            "VALUES(?, 1, ?, ?, ?) ON CONFLICT(month) DO UPDATE SET "
            "requests = requests + 1, input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens, "
            "search_queries = search_queries + excluded.search_queries",
            (self._month(), int(input_tokens), int(output_tokens), int(searches)),
        )

    def current(self) -> dict:
        row = self._db.one("SELECT * FROM usage WHERE month = ?", (self._month(),))
        data = row or {"month": self._month(), "requests": 0, "input_tokens": 0, "output_tokens": 0, "search_queries": 0}
        data["free_searches"] = FREE_SEARCHES
        return data
