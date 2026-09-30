"""Durable single-owner draft. Each save is one SQLite transaction."""

import json
import sqlite3
from pathlib import Path

from .models import Draft


class DraftStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS draft (slot INTEGER PRIMARY KEY CHECK(slot = 1), data TEXT NOT NULL)")
        self._db.commit()

    def load(self) -> Draft | None:
        row = self._db.execute("SELECT data FROM draft WHERE slot = 1").fetchone()
        return Draft.from_dict(json.loads(row[0])) if row else None

    def save(self, draft: Draft) -> None:
        payload = json.dumps(draft.to_dict(), ensure_ascii=False)
        with self._db:
            self._db.execute("INSERT INTO draft(slot, data) VALUES(1, ?) ON CONFLICT(slot) DO UPDATE SET data = excluded.data", (payload,))

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM draft WHERE slot = 1")

    def close(self) -> None:
        self._db.close()

