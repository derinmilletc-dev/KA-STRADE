"""Grid emir defteri (SQLite). Her emir ÖNCE buraya yazılır, SONRA borsaya gönderilir.

Birden fazla grid (farklı kullanıcı ve coinler) aynı defteri paylaşır; her grid kendi satırlarına dokunur.
Motorlar ayrı iş parçacıklarında koşabildiği için bağlantıya erişim tek bir kilitle sıralanır.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
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
# Sonradan eklenen sütunlar (eski defterler açılışta yükseltilir; mevcut satırlar varsayılanı alır).
MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "grid": [
        ("user_id", "TEXT NOT NULL DEFAULT ''"),     # sahibi; '' = çoklu grid öncesi (yönetici) kaydı
        ("account", "TEXT NOT NULL DEFAULT 'LIVE'"),
        ("created_at", "REAL NOT NULL DEFAULT 0"),
        ("updated_at", "REAL NOT NULL DEFAULT 0"),
        ("peak_pnl", "REAL NOT NULL DEFAULT 0"),     # iz süren kâr kilidi: görülen en yüksek gerçekleşen kâr
    ],
    "orders": [
        ("cycle", "INTEGER NOT NULL DEFAULT 0"),      # kademenin kaçıncı turu (giriş + hedefleri aynı turu taşır)
    ],
    "alerts": [
        ("at", "REAL NOT NULL DEFAULT 0"),
    ],
}
PENDING_SEND, UNKNOWN = "PENDING_SEND", "UNKNOWN"
TERMINAL = {"FILLED", "CANCELED", "REJECTED"}


class GridStore:
    def __init__(self, path: str = ":memory:", *, check_same_thread: bool = True) -> None:
        # Canlı motorlar ayrı iş parçacıklarında koşar; her çağrı kilitle sıralanır (aynı anda tek erişim).
        self.db = sqlite3.connect(path, check_same_thread=check_same_thread)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self._migrate()
            self.db.execute("CREATE INDEX IF NOT EXISTS orders_grid ON orders(grid_id, role)")
            self.db.execute("CREATE INDEX IF NOT EXISTS grid_owner ON grid(user_id, status)")
            self.db.commit()

    def _migrate(self) -> None:
        for table, columns in MIGRATIONS.items():
            have = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # ---------- grid ----------
    def create_grid(self, grid_id: str, symbol: str, side: str, spec: dict[str, Any], *, user_id: str = "",
                    account: str = "LIVE") -> bool:
        now = time.time()
        with self.lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO grid(grid_id,symbol,side,status,spec,user_id,account,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (grid_id, symbol, side, "CREATED", json.dumps(spec), str(user_id or ""), account, now, now))
            self.db.commit()
            return cur.rowcount == 1

    def grid(self, grid_id: str) -> sqlite3.Row:
        with self.lock:
            return self.db.execute("SELECT * FROM grid WHERE grid_id=?", (grid_id,)).fetchone()

    def grids(self, *, user_id: str | None = None, include_legacy: bool = False, statuses: set[str] | None = None,
              exclude_statuses: set[str] | None = None, limit: int | None = None) -> list[sqlite3.Row]:
        """En yeni önce. include_legacy: sahipsiz ('') eski kayıtlar da (yalnız yönetici için) dahil."""
        sql, args = "SELECT * FROM grid WHERE 1=1", []
        if user_id is not None:
            if include_legacy:
                sql += " AND user_id IN (?, '')"
            else:
                sql += " AND user_id=?"
            args.append(str(user_id))
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"; args.extend(sorted(statuses))
        if exclude_statuses:
            sql += f" AND status NOT IN ({','.join('?' * len(exclude_statuses))})"; args.extend(sorted(exclude_statuses))
        sql += " ORDER BY rowid DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def latest_grid(self) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute("SELECT * FROM grid ORDER BY rowid DESC LIMIT 1").fetchone()

    def set_grid(self, grid_id: str, **fields: Any) -> None:
        fields = {**fields, "updated_at": time.time()}
        keys = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE grid SET {keys} WHERE grid_id=?", (*fields.values(), grid_id))
            self.db.commit()

    # ---------- emirler ----------
    def add_order(self, row: dict[str, Any]) -> bool:
        cols = ",".join(row)
        with self.lock:
            cur = self.db.execute(f"INSERT OR IGNORE INTO orders({cols}) VALUES({','.join('?' * len(row))})", tuple(row.values()))
            self.db.commit()
            return cur.rowcount == 1

    def set_order(self, client_id: str, **fields: Any) -> None:
        keys = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.db.execute(f"UPDATE orders SET {keys} WHERE client_id=?", (*fields.values(), client_id))
            self.db.commit()

    def order(self, client_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.db.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone()

    def orders(self, grid_id: str, role: str | None = None) -> list[sqlite3.Row]:
        sql, args = "SELECT * FROM orders WHERE grid_id=?", [grid_id]
        if role:
            sql += " AND role=?"; args.append(role)
        with self.lock:
            return self.db.execute(sql + " ORDER BY level, rev", args).fetchall()

    # ---------- uyarılar ----------
    def alert(self, grid_id: str, level: str, text: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO alerts(grid_id,level,text,at) VALUES(?,?,?,?)", (grid_id, level, text, time.time()))
            self.db.commit()

    def alerts(self, grid_id: str) -> list[tuple[str, str]]:
        with self.lock:
            return [(r["level"], r["text"]) for r in self.db.execute("SELECT level,text FROM alerts WHERE grid_id=? ORDER BY id", (grid_id,))]
