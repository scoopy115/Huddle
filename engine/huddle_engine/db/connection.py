"""SQLite connection wrapper: one connection, one lock, WAL, foreign keys, migrations."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .migrations import migrate


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                     isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._lock = threading.RLock()
        self.schema_version = migrate(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Serialised transaction. Nested use (same thread) joins the outer tx."""
        with self._lock:
            outer = not self._conn.in_transaction
            if outer:
                self._conn.execute("BEGIN")
            try:
                yield self._conn
                if outer:
                    self._conn.execute("COMMIT")
            except Exception:
                if outer:
                    self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | list = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def execute(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        with self.tx() as c:
            return c.execute(sql, args)

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.tx() as c:
            c.executemany(sql, rows)

    # ---- settings -------------------------------------------------------- #
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES (?, ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))

    def all_settings(self) -> dict[str, Any]:
        return {r["key"]: json.loads(r["value"]) for r in self.query("SELECT key, value FROM settings")}


def now() -> float:
    return time.time()
