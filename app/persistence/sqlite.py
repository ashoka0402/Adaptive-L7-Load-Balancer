"""Small SQLite repository for durable control-plane artifacts."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


class SQLiteStore:
    """Persist bounded policy history and named experiment results."""

    def __init__(self, path: str | Path, *, snapshot_limit: int = 500) -> None:
        self.path = Path(path)
        self.snapshot_limit = snapshot_limit
        self.run_id = uuid.uuid4().hex

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS policy_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(run_id, version)
                );
                CREATE TABLE IF NOT EXISTS experiment_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                """)

    def save_policy(self, version: int, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO policy_snapshots(run_id, version, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    version,
                    time.time(),
                    json.dumps(payload, separators=(",", ":")),
                ),
            )
            connection.execute(
                """
                DELETE FROM policy_snapshots
                WHERE id NOT IN (
                    SELECT id FROM policy_snapshots
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (self.snapshot_limit,),
            )

    def save_experiment(self, name: str, payload: dict[str, Any]) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO experiment_results(name, created_at, payload) VALUES (?, ?, ?)",
                (name, time.time(), json.dumps(payload, separators=(",", ":"))),
            )
            connection.execute("DELETE FROM experiment_results WHERE id NOT IN (SELECT id FROM experiment_results ORDER BY id DESC LIMIT 8)")
            return int(cursor.lastrowid or 0)

    def get_experiment(self, name: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM experiment_results WHERE name=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def recent_experiments(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT name, created_at FROM experiment_results ORDER BY id DESC LIMIT 8").fetchall()
        return [{"id": row[0], "created_at": row[1]} for row in rows]

    def recent_policies(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM policy_snapshots ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, self.snapshot_limit)),),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=2.0)
