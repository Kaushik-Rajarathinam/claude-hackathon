# hatenna_lite/memory.py — the bot's on-disk state.
#
# The full HatennaBot splits this across MongoDB and Redis. The lite version
# keeps one SQLite file: whatever is written here survives a kill -9.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      TEXT PRIMARY KEY,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sightings (
    spawn_id   TEXT PRIMARY KEY,
    dex        INTEGER,
    image      TEXT,
    name       TEXT NOT NULL,
    confidence REAL NOT NULL,
    ts         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS hunters (
    user    TEXT NOT NULL,
    pokemon TEXT NOT NULL,
    PRIMARY KEY (user, pokemon)
);
CREATE TABLE IF NOT EXISTS incidents (
    ts     REAL NOT NULL,
    helper TEXT NOT NULL,
    error  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    task    TEXT PRIMARY KEY,
    tokens  INTEGER NOT NULL DEFAULT 0,
    calls   INTEGER NOT NULL DEFAULT 0,
    refused INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS facts (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)  # autocommit
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _one(self, sql: str, *args: Any):
        return self.db.execute(sql, args).fetchone()

    # --- facts: bookmark, boot count, heartbeat ---
    def get_fact(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM facts WHERE key=?", key)
        return json.loads(row["value"]) if row else default

    def set_fact(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO facts(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # --- events: every input, keyed by id so a replay is detected ---
    def record_event(self, event: dict) -> bool:
        """False if this id was handled before (duplicate)."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events(id, ts, kind, payload) VALUES(?,?,?,?)",
            (event["id"], event["ts"], event["kind"], json.dumps(event.get("payload", {}))),
        )
        return cur.rowcount == 1

    def has_event(self, event_id: str) -> bool:
        return self._one("SELECT 1 FROM events WHERE id=?", event_id) is not None

    def event_count(self) -> int:
        return self._one("SELECT COUNT(*) n FROM events")["n"]

    # --- what the helpers produce ---
    def record_sighting(self, spawn_id: str, dex: Optional[int], image: Optional[str],
                        name: str, confidence: float, ts: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO sightings VALUES(?,?,?,?,?,?)",
                        (spawn_id, dex, image, name, confidence, ts))

    def recent_sightings(self, n: int = 8) -> list[dict]:
        rows = self.db.execute("SELECT * FROM sightings ORDER BY ts DESC LIMIT ?", (n,))
        return [dict(r) for r in rows]

    def top_sightings(self, n: int = 3) -> list[tuple[str, int]]:
        rows = self.db.execute(
            "SELECT name, COUNT(*) c FROM sightings GROUP BY name ORDER BY c DESC, name LIMIT ?", (n,))
        return [(r["name"], r["c"]) for r in rows]

    def add_hunter(self, user: str, pokemon: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO hunters VALUES(?,?)", (user, pokemon.lower()))

    def hunters_for(self, pokemon: str) -> list[str]:
        rows = self.db.execute("SELECT user FROM hunters WHERE pokemon=? ORDER BY user",
                               (pokemon.lower(),))
        return [r["user"] for r in rows]

    def hunter_count(self) -> int:
        return self._one("SELECT COUNT(*) n FROM hunters")["n"]

    def record_incident(self, helper: str, error: str, ts: float) -> None:
        self.db.execute("INSERT INTO incidents VALUES(?,?,?)", (ts, helper, error))

    def incident_count(self) -> int:
        return self._one("SELECT COUNT(*) n FROM incidents")["n"]

    # --- spend: token use per task, kept across restarts ---
    def spend(self, task: str) -> dict:
        row = self._one("SELECT tokens, calls, refused FROM spend WHERE task=?", task)
        return dict(row) if row else {"tokens": 0, "calls": 0, "refused": 0}

    def add_spend(self, task: str, tokens: int = 0, calls: int = 0, refused: int = 0) -> None:
        self.db.execute(
            "INSERT INTO spend(task, tokens, calls, refused) VALUES(?,?,?,?) "
            "ON CONFLICT(task) DO UPDATE SET tokens=tokens+excluded.tokens, "
            "calls=calls+excluded.calls, refused=refused+excluded.refused",
            (task, tokens, calls, refused),
        )
