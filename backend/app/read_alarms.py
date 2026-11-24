"""Pozisyon yorum alarmı (SALT OKUNUR: emir göndermez, yalnız bildirir).

Kullanıcı açık pozisyonu için alarmı açar; ayrı bir döngü ~60 sn'de bir o coinin yorumunu (coin_read motoru) yeniden hesaplar.
Pozisyonun yönüne TERS yeni bir olay (ters kırılım, tuzak, RSI uyumsuzluğu, zaman dilimlerinin yön değiştirmesi) çıkınca
BİR kez bildirim gider. Aynı olay sürdüğü sürece tekrar bildirilmez; olay geçip yeniden çıkarsa yine bildirilir.
Bildirim, kullanıcı alarmı bilerek açtığı için bildirim tercihlerinden bağımsız gönderilir.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from . import coin_read, coin_read_service

logger = logging.getLogger(__name__)

TABLE = "kaistrade_read_alarm"
INTERVAL_SECONDS = 60
COOLDOWN_SECONDS = 600       # aynı alarm için iki bildirim arası en az 10 dk
LIFETIME = timedelta(days=7)  # pozisyon kapanıp alarm kalırsa kendiliğinden söner; ekran açıkken uzatılır
MAX_PER_USER = 30
MODES = ("against", "all")
MIN_TFS = ("5m", "15m", "1h")
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          id BIGSERIAL PRIMARY KEY,
          user_id TEXT NOT NULL,
          exchange TEXT NOT NULL,
          product_id TEXT NOT NULL,
          symbol TEXT NOT NULL,
          side TEXT NOT NULL,
          entry DOUBLE PRECISION,
          mode TEXT NOT NULL DEFAULT 'against',
          min_tf TEXT NOT NULL DEFAULT '15m',
          active_keys TEXT NOT NULL DEFAULT '',
          last_push_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          expires_at TIMESTAMPTZ NOT NULL,
          UNIQUE (user_id, exchange, product_id)
        )
        """
    )
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def normalize(exchange: Any, product_id: Any, symbol: Any, side: Any, entry: Any, mode: Any, min_tf: Any) -> dict[str, Any] | None:
    """Gelen alanları doğrular; geçersizse None. Sembol büyük harfe çevrilir, tanınmayan mod/süre varsayılana düşer."""
    ex = str(exchange or "").strip().lower()
    sym = coin_read_service.valid_symbol(symbol)
    pid = str(product_id or "").strip()
    sd = str(side or "").strip().upper()
    if ex not in ("binance", "coinbase") or sym is None or not (2 <= len(pid) <= 60) or sd not in ("LONG", "SHORT"):
        return None
    try:
        entry_value = float(entry) if entry not in (None, "") else None
    except (TypeError, ValueError):
        entry_value = None
    if entry_value is not None and not (entry_value > 0 and entry_value < float("inf")):
        entry_value = None
    return {"exchange": ex, "product_id": pid, "symbol": sym, "side": sd, "entry": entry_value,
            "mode": mode if mode in MODES else "against", "min_tf": min_tf if min_tf in MIN_TFS else "15m"}


def _row(row: Any) -> dict[str, Any]:
    return {"id": int(row["id"]), "exchange": str(row["exchange"]), "product_id": str(row["product_id"]), "symbol": str(row["symbol"]),
            "side": str(row["side"]), "entry": None if row["entry"] is None else float(row["entry"]), "mode": str(row["mode"]),
            "min_tf": str(row["min_tf"]), "active_keys": [k for k in str(row["active_keys"] or "").split(",") if k],
            "last_push_at": row["last_push_at"]}


async def list_alarms(pool: Any, user_id: str, exchange: str) -> list[dict[str, Any]]:
    await ensure_schema(pool)
    rows = await pool.fetch(
        f"SELECT id, exchange, product_id, symbol, side, entry, mode, min_tf, active_keys, last_push_at FROM {TABLE} "
        "WHERE user_id = $1 AND exchange = $2 AND expires_at > NOW() ORDER BY id", user_id, exchange)
    return [_row(row) for row in rows]


async def upsert(pool: Any, user_id: str, data: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    await ensure_schema(pool)
    existing = await pool.fetchval(f"SELECT COUNT(*) FROM {TABLE} WHERE user_id = $1 AND expires_at > NOW() AND NOT (exchange = $2 AND product_id = $3)",
                                   user_id, data["exchange"], data["product_id"])
    if int(existing or 0) >= MAX_PER_USER:
        raise ValueError(f"En fazla {MAX_PER_USER} pozisyon alarmı olabilir.")
    expires = (now or datetime.now(timezone.utc)) + LIFETIME
    row = await pool.fetchrow(
        f"""
        INSERT INTO {TABLE} (user_id, exchange, product_id, symbol, side, entry, mode, min_tf, expires_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (user_id, exchange, product_id) DO UPDATE SET
          symbol = EXCLUDED.symbol, side = EXCLUDED.side, entry = EXCLUDED.entry, mode = EXCLUDED.mode, min_tf = EXCLUDED.min_tf,
          expires_at = EXCLUDED.expires_at,
          active_keys = CASE WHEN {TABLE}.side = EXCLUDED.side THEN {TABLE}.active_keys ELSE '' END
        RETURNING id, exchange, product_id, symbol, side, entry, mode, min_tf, active_keys, last_push_at
        """,
        user_id, data["exchange"], data["product_id"], data["symbol"], data["side"], data["entry"], data["mode"], data["min_tf"], expires)
    return _row(row)


async def delete(pool: Any, user_id: str, exchange: str, product_id: str) -> bool:
    await ensure_schema(pool)
    result = await pool.execute(f"DELETE FROM {TABLE} WHERE user_id = $1 AND exchange = $2 AND product_id = $3", user_id, exchange, product_id)
    return str(result).endswith(" 1")


async def keep_alive(pool: Any, user_id: str, exchange: str, product_ids: list[str], now: datetime | None = None) -> None:
    """Pozisyonlar ekranı açıkken hâlâ açık olan pozisyonların alarmını uzatır; kapananlar silinir (istemci bildirir)."""
    await ensure_schema(pool)
    if product_ids:
        await pool.execute(f"UPDATE {TABLE} SET expires_at = $4 WHERE user_id = $1 AND exchange = $2 AND product_id = ANY($3::text[])",
                           user_id, exchange, product_ids, (now or datetime.now(timezone.utc)) + LIFETIME)


def plan(active: list[str], events: dict[str, dict[str, str]], last_push_at: datetime | None, now: datetime) -> tuple[list[str], list[str]]:
    """(şimdi bildirilecek anahtarlar, yeni aktif anahtar listesi). Soğuma süresi dolmadıkça yeni olay "aktif" yazılmaz;
    böylece süre dolunca hâlâ sürüyorsa bildirilir."""
    current = set(events)
    new = [key for key in events if key not in active]
    cooled = last_push_at is None or (now - last_push_at).total_seconds() >= COOLDOWN_SECONDS
    send = new if new and cooled else []
    return send, sorted((set(active) & current) | set(send))


def message(display: str, side: str, events: dict[str, dict[str, str]], keys: list[str]) -> tuple[str, str]:
    order = {"danger": 0, "warn": 1, "good": 2}
    picked = sorted(keys, key=lambda k: (order.get(events[k]["tone"], 3), k))
    worst = events[picked[0]]["tone"]
    mark = "⚠" if worst in ("danger", "warn") else "✅"
    title = f"{mark} {display} {'LONG' if side == 'LONG' else 'SHORT'} pozisyonun için yorum uyarısı"
    body = " ".join(events[k]["text"] for k in picked[:2])
    if len(picked) > 2:
        body += f" (+{len(picked) - 2} uyarı daha)"
    return title, body


async def _push(pool: Any, user_id: str, title: str, body: str, url: str) -> None:
    from .web_push import configured, send_to_user
    if not configured():
        logger.warning("Yorum alarmı çaldı ama telefon bildirimi kapalı (VAPID ayarı yok)")
        return
    if not await send_to_user(pool, user_id, "read", title, body, force=True, url=url):
        logger.warning("Yorum alarmı bildirimi hiçbir cihaza gitmedi (telefon aboneliği yok ya da gönderim başarısız)")


async def _open_coinbase_products(pool: Any, user_id: str) -> set[str] | None:
    """Kullanıcının Coinbase'de açık pozisyonu olan ürünleri; okunamazsa None (alarm silinmez)."""
    try:
        from .coinbase import service, vault
        from .coinbase.client import CoinbaseClient
        from .coinbase.jwt_auth import load_private_key
        stored = await vault.load_key(pool, user_id)
        if stored is None:
            return None
        positions = await service.fetch_positions(CoinbaseClient(stored[0], load_private_key(stored[1])))
        return {str(p["product_id"]) for p in positions if p.get("product_id")}
    except Exception as exc:  # noqa: BLE001
        logger.info("Yorum alarmı Coinbase pozisyonlarını okuyamadı (%s)", type(exc).__name__)
        return None


async def run_once(app: Any, now: datetime | None = None) -> int:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return 0
    await ensure_schema(pool)
    now = now or datetime.now(timezone.utc)
    await pool.execute(f"DELETE FROM {TABLE} WHERE expires_at <= $1", now)
    rows = await pool.fetch(
        f"SELECT id, user_id, exchange, product_id, symbol, side, entry, mode, min_tf, active_keys, last_push_at FROM {TABLE}")
    alarms = [{**_row(row), "user_id": str(row["user_id"])} for row in rows]
    if not alarms:
        return 0
    open_by_user: dict[str, set[str] | None] = {}
    for user_id in {a["user_id"] for a in alarms if a["exchange"] == "coinbase"}:
        open_by_user[user_id] = await _open_coinbase_products(pool, user_id)
    cache: dict[str, dict[str, Any] | None] = {}
    sent = 0
    for alarm in alarms:
        if alarm["exchange"] == "coinbase":
            opened = open_by_user.get(alarm["user_id"])
            if opened is not None and alarm["product_id"] not in opened:
                await delete(pool, alarm["user_id"], "coinbase", alarm["product_id"])   # pozisyon kapanmış
                continue
        symbol = alarm["symbol"]
        if symbol not in cache:
            try:
                cache[symbol] = await coin_read_service.read(app, symbol, with_futures=False, tfs=coin_read_service.ALARM_TFS)
            except Exception as exc:  # noqa: BLE001 - bir coin diğerlerini durdurmaz
                logger.info("Yorum alarmı %s için veri alamadı (%s)", symbol, type(exc).__name__)
                cache[symbol] = None
        data = cache[symbol]
        if data is None:
            continue
        reads = {r["tf"]: r for r in data["timeframes"]}
        events = coin_read.position_events(reads, data["summary"], alarm["side"], min_tf=alarm["min_tf"], mode=alarm["mode"])
        send, next_keys = plan(alarm["active_keys"], events, alarm["last_push_at"], now)
        if send:
            title, body = message(alarm["product_id"].split("-")[0] if alarm["exchange"] == "coinbase" else alarm["symbol"], alarm["side"], events, send)
            try:
                await _push(pool, alarm["user_id"], title, body, f"/master-trade?tab=pozisyonlar&ex={alarm['exchange']}&read={alarm['product_id']}")
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Yorum alarmı bildirimi gönderilemedi (%s)", type(exc).__name__)
                continue   # gönderilemediyse aktif yazma: bir sonraki turda yeniden denenir
            await pool.execute(f"UPDATE {TABLE} SET active_keys = $2, last_push_at = $3 WHERE id = $1", alarm["id"], ",".join(next_keys), now)
        elif sorted(alarm["active_keys"]) != next_keys:
            await pool.execute(f"UPDATE {TABLE} SET active_keys = $2 WHERE id = $1", alarm["id"], ",".join(next_keys))
    return sent


async def alarm_loop(app: Any) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            await run_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Pozisyon yorum alarmı turu başarısız")
        await asyncio.sleep(INTERVAL_SECONDS)
