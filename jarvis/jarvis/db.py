"""SQLite storage. One file on the data volume, accessed under a lock."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

SCHEMA = [
    # version 1
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE secrets (
        key TEXT PRIMARY KEY,
        nonce BLOB NOT NULL,
        ciphertext BLOB NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        ip TEXT
    );
    CREATE TABLE bereiche (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        zone_uuid TEXT,
        main_light TEXT,
        rooms TEXT NOT NULL DEFAULT '[]',
        sort INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE mics (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        mac TEXT NOT NULL UNIQUE,
        host TEXT,
        bereich_id TEXT,
        firmware TEXT,
        last_seen REAL,
        created_at REAL NOT NULL
    );
    CREATE TABLE controls (
        uuid TEXT PRIMARY KEY,
        short_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        room TEXT,
        category TEXT,
        parent TEXT,
        allowed INTEGER NOT NULL DEFAULT 0,
        critical INTEGER NOT NULL DEFAULT 0,
        aliases TEXT NOT NULL DEFAULT '[]',
        present INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE people (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        anrede TEXT NOT NULL DEFAULT 'name',
        notes TEXT NOT NULL DEFAULT '',
        embedding BLOB,
        created_at REAL NOT NULL
    );
    CREATE TABLE timers (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL DEFAULT '',
        due_at REAL NOT NULL,
        bereich_id TEXT,
        lang TEXT NOT NULL DEFAULT 'de',
        created_at REAL NOT NULL
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX events_ts ON events(ts);
    CREATE TABLE usage (
        month TEXT PRIMARY KEY,
        requests INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        search_queries INTEGER NOT NULL DEFAULT 0
    );
    """,
]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA secure_delete=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for index, script in enumerate(SCHEMA[version:], start=version + 1):
                self._conn.executescript("BEGIN;" + script + f"PRAGMA user_version={index};COMMIT;")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, [tuple(r) for r in rows])
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default
