"""KAIS FARMER kalıcı durum (C2). Motor satırları `GridStore` tablolarını (grid/orders/alerts) kullanır; burada yalnız `farmer_*` tabloları eklenir.

Hesap başına AYRI dosya (farmer_live.sqlite3 / farmer_demo.sqlite3 — DCA ile aynı kalıp); mevcut tablolara sütun EKLENMEZ.
Geri alma: `DROP TABLE farmer_*` (başka hiçbir tabloya dokunmaz)."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from ..grid_engine.store import GridStore

FARMER_SCHEMA = """
CREATE TABLE IF NOT EXISTS farmer_bot(
  account_key TEXT PRIMARY KEY, state TEXT NOT NULL, settings TEXT NOT NULL, pause_reason TEXT NOT NULL DEFAULT '',
  updated_at REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS farmer_entry_attempt(
  account_key TEXT NOT NULL, symbol TEXT NOT NULL, interval TEXT NOT NULL, candle_open_ms INTEGER NOT NULL,
  grid_id TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'SENT', at REAL NOT NULL,
  PRIMARY KEY(account_key, symbol, interval, candle_open_ms));
CREATE TABLE IF NOT EXISTS farmer_margin_hold(
  grid_id TEXT PRIMARY KEY, account_key TEXT NOT NULL, margin_usdt REAL NOT NULL, state TEXT NOT NULL,
  at REAL NOT NULL, released_at REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS farmer_hold_acct ON farmer_margin_hold(account_key, state);
CREATE TABLE IF NOT EXISTS farmer_candidate(
  account_key TEXT NOT NULL, symbol TEXT NOT NULL, candle_open_ms INTEGER NOT NULL, direction TEXT NOT NULL, score REAL NOT NULL,
  snapshot TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'NEW', reject_reason TEXT NOT NULL DEFAULT '', at REAL NOT NULL,
  PRIMARY KEY(account_key, symbol, candle_open_ms));
CREATE TABLE IF NOT EXISTS farmer_result(
  grid_id TEXT PRIMARY KEY, account_key TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, opened_at REAL NOT NULL,
  closed_at REAL NOT NULL DEFAULT 0, target_usdt REAL NOT NULL, max_loss_usdt REAL NOT NULL, est_cost_usdt REAL NOT NULL,
  close_reason TEXT NOT NULL DEFAULT 'OPEN', pnl_state TEXT NOT NULL DEFAULT 'PENDING',
  gross_pnl REAL, commission REAL, funding REAL, verified_net_pnl REAL,
  verify_attempts INTEGER NOT NULL DEFAULT 0, verify_error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS farmer_dead_watch(
  grid_id TEXT PRIMARY KEY, last_candle_ms INTEGER NOT NULL DEFAULT 0, consecutive INTEGER NOT NULL DEFAULT 0);
"""

HOLD_ACTIVE = ("HELD", "OPEN", "CLOSING")      # CLOSING: kapanış başladı, flat doğrulanana kadar slot+marjin tutulur (C6)
CLAIMED, DUPLICATE, OVER_CAP, NO_SLOT = "CLAIMED", "DUPLICATE", "OVER_CAP", "NO_SLOT"
CLOSE_REASONS = {"OPEN", "TARGET", "STOP", "MARKET_CLOSE", "DEAD_CLOSE", "EMERGENCY"}
PNL_STATES = {"PENDING", "VERIFIED", "UNVERIFIED"}


class FarmerStore(GridStore):
    def __init__(self, path: str = ":memory:", *, check_same_thread: bool = True) -> None:
        super().__init__(path, check_same_thread=check_same_thread)
        with self.lock:
            self.db.executescript(FARMER_SCHEMA)
            self.db.commit()

    # ---------- orkestratör durumu ----------
    def save_bot(self, account_key: str, state: str, settings: dict[str, Any], *, pause_reason: str = "") -> None:
        now = time.time()
        with self.lock:
            self.db.execute(
                "INSERT INTO farmer_bot(account_key,state,settings,pause_reason,updated_at,created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(account_key) DO UPDATE SET state=excluded.state, settings=excluded.settings, "
                "pause_reason=excluded.pause_reason, updated_at=excluded.updated_at",
                (account_key, state, json.dumps(settings, sort_keys=True), pause_reason, now, now))
            self.db.commit()

    def bot(self, account_key: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM farmer_bot WHERE account_key=?", (account_key,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["settings"] = json.loads(data["settings"])
        return data

    # ---------- giriş kilidi + marjin rezervasyonu (TEK işlem) ----------
    def claim_entry(self, account_key: str, symbol: str, interval: str, candle_open_ms: int, grid_id: str, margin_usdt: float,
                    *, cap_usdt: float, max_slots: int | None = None) -> str:
        """Emir göndermeden ÖNCE çağrılır. CLAIMED dönmezse emir GÖNDERİLMEZ.
        DUPLICATE: aynı hesap+sembol+aralık+mum için daha önce deneme yazılmış (geçici ret dahil; satır silinmez).
        OVER_CAP: mevcut aktif rezervasyon + bu marjin > tavan (satır YAZILMAZ).
        NO_SLOT: `max_slots` verilmişse aktif (HELD+OPEN+CLOSING) slot sayısı doldu (satır YAZILMAZ)."""
        if margin_usdt <= 0 or cap_usdt <= 0:
            raise ValueError("marjin ve tavan pozitif olmalı")
        with self.lock:
            self.db.commit()
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if max_slots is not None and self._active_count(account_key) >= int(max_slots):
                    self.db.rollback()
                    return NO_SLOT
                held = self._held(account_key)
                if held + margin_usdt > cap_usdt + 1e-9:
                    self.db.rollback()
                    return OVER_CAP
                now = time.time()
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO farmer_entry_attempt(account_key,symbol,interval,candle_open_ms,grid_id,outcome,at) "
                    "VALUES(?,?,?,?,?,'SENT',?)", (account_key, symbol, interval, int(candle_open_ms), grid_id, now))
                if cur.rowcount != 1:
                    self.db.rollback()
                    return DUPLICATE
                self.db.execute("INSERT INTO farmer_margin_hold(grid_id,account_key,margin_usdt,state,at) VALUES(?,?,?,'HELD',?)",
                                (grid_id, account_key, float(margin_usdt), now))
                self.db.commit()
                return CLAIMED
            except sqlite3.Error:
                self.db.rollback()
                raise

    def _held(self, account_key: str) -> float:
        row = self.db.execute("SELECT COALESCE(SUM(margin_usdt),0) AS m FROM farmer_margin_hold WHERE account_key=? AND state IN ('HELD','OPEN','CLOSING')",
                              (account_key,)).fetchone()
        return float(row["m"])

    def _active_count(self, account_key: str) -> int:
        return int(self.db.execute("SELECT COUNT(*) AS n FROM farmer_margin_hold WHERE account_key=? AND state IN ('HELD','OPEN','CLOSING')",
                                   (account_key,)).fetchone()["n"])

    def slot_counts(self, account_key: str) -> dict[str, int]:
        with self.lock:
            rows = self.db.execute("SELECT state, COUNT(*) AS n FROM farmer_margin_hold WHERE account_key=? AND state IN ('HELD','OPEN','CLOSING') GROUP BY state",
                                   (account_key,)).fetchall()
        counts = {"HELD": 0, "OPEN": 0, "CLOSING": 0}
        counts.update({row["state"]: int(row["n"]) for row in rows})
        return counts

    def held_margin(self, account_key: str) -> float:
        with self.lock:
            return self._held(account_key)

    def attempt(self, account_key: str, symbol: str, interval: str, candle_open_ms: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM farmer_entry_attempt WHERE account_key=? AND symbol=? AND interval=? AND candle_open_ms=?",
                                  (account_key, symbol, interval, int(candle_open_ms))).fetchone()
        return dict(row) if row else None

    def set_attempt_outcome(self, grid_id: str, outcome: str) -> None:
        with self.lock:
            self.db.execute("UPDATE farmer_entry_attempt SET outcome=? WHERE grid_id=?", (outcome, grid_id))
            self.db.commit()

    def mark_hold_open(self, grid_id: str) -> None:
        with self.lock:
            self.db.execute("UPDATE farmer_margin_hold SET state='OPEN' WHERE grid_id=? AND state='HELD'", (grid_id,))
            self.db.commit()

    def mark_hold_closing(self, grid_id: str) -> bool:
        """OPEN/HELD -> CLOSING (slot ve marjin flat doğrulanana kadar tutulur)."""
        with self.lock:
            cur = self.db.execute("UPDATE farmer_margin_hold SET state='CLOSING' WHERE grid_id=? AND state IN ('HELD','OPEN')", (grid_id,))
            self.db.commit()
            return cur.rowcount == 1

    def release_hold(self, grid_id: str) -> bool:
        """Yalnızca aktif rezervasyonu bırakır; tekrar çağrı zararsız (False)."""
        with self.lock:
            cur = self.db.execute("UPDATE farmer_margin_hold SET state='RELEASED', released_at=? WHERE grid_id=? AND state IN ('HELD','OPEN','CLOSING')",
                                  (time.time(), grid_id))
            self.db.commit()
            return cur.rowcount == 1

    def holds(self, account_key: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM farmer_margin_hold WHERE account_key=?" + (" AND state IN ('HELD','OPEN','CLOSING')" if active_only else "") + " ORDER BY at"
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, (account_key,))]

    # ---------- aday önbelleği ----------
    def save_candidate(self, account_key: str, symbol: str, candle_open_ms: int, direction: str, score: float, snapshot: dict[str, Any],
                       *, status: str = "NEW", reject_reason: str = "") -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO farmer_candidate(account_key,symbol,candle_open_ms,direction,score,snapshot,status,reject_reason,at) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_key,symbol,candle_open_ms) DO UPDATE SET "
                "direction=excluded.direction, score=excluded.score, snapshot=excluded.snapshot, status=excluded.status, "
                "reject_reason=excluded.reject_reason, at=excluded.at",
                (account_key, symbol, int(candle_open_ms), direction, float(score), json.dumps(snapshot, sort_keys=True, default=str),
                 status, reject_reason, time.time()))
            self.db.commit()

    def set_candidate_status(self, account_key: str, symbol: str, candle_open_ms: int, status: str, reject_reason: str = "") -> None:
        with self.lock:
            self.db.execute("UPDATE farmer_candidate SET status=?, reject_reason=?, at=? WHERE account_key=? AND symbol=? AND candle_open_ms=?",
                            (status, reject_reason, time.time(), account_key, symbol, int(candle_open_ms)))
            self.db.commit()

    def candidates(self, account_key: str, *, status: str | None = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM farmer_candidate WHERE account_key=?", [account_key]
        if status:
            sql, args = sql + " AND status=?", args + [status]
        with self.lock:
            rows = [dict(row) for row in self.db.execute(sql + " ORDER BY score DESC, symbol", args)]
        for row in rows:
            row["snapshot"] = json.loads(row["snapshot"])
        return rows

    # ---------- sonuç (doğrulanmış PnL ayrı sütunlar) ----------
    def open_result(self, grid_id: str, account_key: str, symbol: str, side: str, *, target_usdt: float, max_loss_usdt: float,
                    est_cost_usdt: float) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR IGNORE INTO farmer_result(grid_id,account_key,symbol,side,opened_at,target_usdt,max_loss_usdt,est_cost_usdt) "
                "VALUES(?,?,?,?,?,?,?,?)", (grid_id, account_key, symbol, side, time.time(), target_usdt, max_loss_usdt, est_cost_usdt))
            self.db.commit()

    def close_result(self, grid_id: str, close_reason: str) -> None:
        if close_reason not in CLOSE_REASONS - {"OPEN"}:
            raise ValueError(f"bilinmeyen kapanış nedeni: {close_reason}")
        with self.lock:
            self.db.execute("UPDATE farmer_result SET close_reason=?, closed_at=? WHERE grid_id=? AND close_reason='OPEN'",
                            (close_reason, time.time(), grid_id))
            self.db.commit()

    def set_pnl(self, grid_id: str, *, state: str, gross: float | None = None, commission: float | None = None,
                funding: float | None = None, net: float | None = None, error: str = "") -> None:
        """VERIFIED yalnız net+gross+commission+funding dördü de verilmişse yazılır; aksi halde UNVERIFIED/PENDING (varsayım YOK)."""
        if state not in PNL_STATES:
            raise ValueError(f"bilinmeyen pnl durumu: {state}")
        if state == "VERIFIED" and None in (gross, commission, funding, net):
            raise ValueError("VERIFIED için gross/commission/funding/net zorunlu")
        with self.lock:
            self.db.execute(
                "UPDATE farmer_result SET pnl_state=?, gross_pnl=?, commission=?, funding=?, verified_net_pnl=?, verify_error=?, "
                "verify_attempts=verify_attempts+1 WHERE grid_id=?", (state, gross, commission, funding, net, error, grid_id))
            self.db.commit()

    def result(self, grid_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM farmer_result WHERE grid_id=?", (grid_id,)).fetchone()
        return dict(row) if row else None

    def unresolved_pnl(self, account_key: str) -> list[dict[str, Any]]:
        """Kapanmış (closed_at>0) ama VERIFIED olmayan sonuçlar."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM farmer_result WHERE account_key=? AND closed_at>0 AND pnl_state<>'VERIFIED' ORDER BY closed_at", (account_key,))]

    def results(self, account_key: str) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM farmer_result WHERE account_key=? ORDER BY opened_at", (account_key,))]

    # ---------- ölü pozisyon sayacı ----------
    def dead_count(self, grid_id: str, candle_open_ms: int, is_dead: bool) -> int:
        """Her KAPANMIŞ mum için en fazla bir kez sayar (aynı mum tekrar gelirse sayaç değişmez)."""
        with self.lock:
            row = self.db.execute("SELECT last_candle_ms, consecutive FROM farmer_dead_watch WHERE grid_id=?", (grid_id,)).fetchone()
            last, count = (int(row["last_candle_ms"]), int(row["consecutive"])) if row else (0, 0)
            if int(candle_open_ms) <= last:
                return count
            count = count + 1 if is_dead else 0
            self.db.execute("INSERT INTO farmer_dead_watch(grid_id,last_candle_ms,consecutive) VALUES(?,?,?) "
                            "ON CONFLICT(grid_id) DO UPDATE SET last_candle_ms=excluded.last_candle_ms, consecutive=excluded.consecutive",
                            (grid_id, int(candle_open_ms), count))
            self.db.commit()
            return count
