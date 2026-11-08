"""SQLite trade log: every confirmed action and its exchange responses."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    env TEXT NOT NULL,
    text TEXT,
    command TEXT,
    summary TEXT,
    status TEXT NOT NULL,
    detail TEXT
)
"""


class TradeLog:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    def add(self, *, env: str, text: str | None, command: Any, summary: str,
            status: str, detail: Any = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO trade_log (ts, env, text, command, summary, status, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), env, text, json.dumps(command, default=str), summary, status,
             json.dumps(detail, default=str)),
        )
        self.conn.commit()
        return cur.lastrowid

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, ts, env, text, summary, status, detail FROM trade_log "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ["id", "ts", "env", "text", "summary", "status", "detail"]
        return [dict(zip(keys, r)) for r in rows]
