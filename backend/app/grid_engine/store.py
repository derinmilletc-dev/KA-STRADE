"""Grid emir defteri (SQLite). Her emir ÖNCE buraya yazılır, SONRA borsaya gönderilir."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS grid(
  grid_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, status TEXT NOT NULL,
  spec TEXT NOT NULL, baseline REAL NOT NULL DEFAULT 0, halt_reason TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS orders(
  client_id TEXT PRIMARY KEY, grid_id TEXT NOT NULL, role TEXT NOT NULL, level INTEGER NOT NULL, rev INTEGER NOT NULL,
  side TEXT NOT NULL, type TEXT NOT NULL, price REAL NOT NULL, stop_price REAL NOT NULL, qty REAL NOT NULL,
  filled REAL NOT NULL DEFAULT 0, status TEXT NOT NULL, reduce_only INTEGER NOT NULL, misses INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, grid_id TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL);
"""
PENDING_SEND, UNKNOWN = "PENDING_SEND", "UNKNOWN"
TERMINAL = {"FILLED", "CANCELED", "REJECTED"}


class GridStore:
    def __init__(self, path: str = ":memory:", *, check_same_thread: bool = True) -> None:
        # Canlı grid motoru ayrı iş parçacığında koşar; orada çağrılar tek tek (sıralı) yapılır, eşzamanlı değil.
        self.db = sqlite3.connect(path, check_same_thread=check_same_thread)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def create_grid(self, grid_id: str, symbol: str, side: str, spec: dict[str, Any]) -> bool:
        cur = self.db.execute("INSERT OR IGNORE INTO grid(grid_id,symbol,side,status,spec) VALUES(?,?,?,?,?)",
                              (grid_id, symbol, side, "CREATED", json.dumps(spec)))
        self.db.commit()
        return cur.rowcount == 1

    def grid(self, grid_id: str) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM grid WHERE grid_id=?", (grid_id,)).fetchone()

    def set_grid(self, grid_id: str, **fields: Any) -> None:
        keys = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE grid SET {keys} WHERE grid_id=?", (*fields.values(), grid_id))
        self.db.commit()

    def add_order(self, row: dict[str, Any]) -> bool:
        cols = ",".join(row)
        cur = self.db.execute(f"INSERT OR IGNORE INTO orders({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
        self.db.commit()
        return cur.rowcount == 1

    def set_order(self, client_id: str, **fields: Any) -> None:
        keys = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE orders SET {keys} WHERE client_id=?", (*fields.values(), client_id))
        self.db.commit()

    def orders(self, grid_id: str, role: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM orders WHERE grid_id=?", [grid_id]
        if role:
            sql += " AND role=?"; args.append(role)
        return self.db.execute(sql + " ORDER BY level, rev", args).fetchall()

    def alert(self, grid_id: str, level: str, text: str) -> None:
        self.db.execute("INSERT INTO alerts(grid_id,level,text) VALUES(?,?,?)", (grid_id, level, text))
        self.db.commit()

    def alerts(self, grid_id: str) -> list[tuple[str, str]]:
        return [(r["level"], r["text"]) for r in self.db.execute("SELECT level,text FROM alerts WHERE grid_id=? ORDER BY id", (grid_id,))]
