"""Small SQLite repository for durable control-plane artifacts."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class SQLiteStore:
    """Persist the eight latest experiment results."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS experiment_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                """)

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

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=2.0)
        try:
            with connection:
                yield connection
        finally:
            connection.close()
