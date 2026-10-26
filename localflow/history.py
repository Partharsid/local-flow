"""Local transcript history (SQLite). Never synced anywhere."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("""CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                app TEXT, title TEXT,
                raw TEXT, final TEXT,
                audio_seconds REAL, total_ms INTEGER, stt_ms INTEGER, llm_ms INTEGER,
                method TEXT)""")
            self._db.commit()

    def add(self, **row) -> None:
        cols = ["ts", "app", "title", "raw", "final", "audio_seconds", "total_ms", "stt_ms", "llm_ms", "method"]
        row.setdefault("ts", time.time())
        with self._lock:
            self._db.execute(f"INSERT INTO entries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [row.get(c) for c in cols])
            self._db.commit()

    def recent(self, limit: int = 200) -> list[dict]:
        with self._lock:
            cur = self._db.execute("SELECT id, ts, app, raw, final, audio_seconds, total_ms FROM entries "
                                   "ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def last_final(self) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT final FROM entries ORDER BY id DESC LIMIT 1").fetchone()
        return r[0] if r else None

    def stats(self) -> dict:
        with self._lock:
            r = self._db.execute("SELECT COUNT(*), COALESCE(SUM(audio_seconds),0), "
                                 "COALESCE(SUM(LENGTH(final) - LENGTH(REPLACE(final, ' ', '')) + 1),0) "
                                 "FROM entries").fetchone()
        return {"count": r[0], "audio_seconds": r[1], "words": r[2]}

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM entries")
            self._db.commit()
            self._db.execute("VACUUM")
