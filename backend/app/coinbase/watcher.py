"""Coinbase bildirim izleyicisi (SALT OKUNUR, emir göndermez).

Her ~45 sn'de anahtarı kayıtlı her hesap için yeni dolumlara bakar. Stop ya da hedef çalıştıysa, bekleyen emir
dolduysa veya Coinbase'de dışarıdan bir emir dolduysa telefona bildirim gönderir ve olayı günlüğe yazar.

- İlk turda yalnızca "son görülen dolum" kaydedilir; geçmiş için bildirim yağmuru olmaz.
- Bir hesapta hata olursa o hesap atlanır, diğerleri ve uygulama etkilenmez.
- Bildirim gönderilemese bile (abonelik yok, VAPID yok) olay günlüğe yazılır; ekrandaki akış onu gösterir.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from . import activity, journal, market, service, vault
from .client import CoinbaseClient
from .config import orders_enabled
from .trading_client import CoinbaseTradingClient as TradingClient
from .jwt_auth import load_private_key

logger = logging.getLogger(__name__)

WATCH_TABLE = "kaistrade_coinbase_watch"
INTERVAL_SECONDS = 45
_SCHEMA_DONE: set[int] = set()


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {WATCH_TABLE} (
          user_id TEXT PRIMARY KEY,
          last_fill_time TEXT,
          positions JSONB,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    await pool.execute(f"ALTER TABLE {WATCH_TABLE} ADD COLUMN IF NOT EXISTS alerts JSONB")
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


def _positions_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        side = activity.position_side(row.get("side"))
        if side:
            result[str(row["product_id"])] = {"side": side, "entry": row.get("avg_entry_price")}
    return result


def notification_for(event: dict[str, Any]) -> tuple[str, str, str] | None:
    """(kategori, başlık, metin). Uygulamadan senin yaptığın giriş/kapanış için bildirim yok (zaten ekrandasın)."""
    kind = event["kind"]
    detail = f"{event['product_id']} · {event['size']} sözleşme @ {event['price']}"
    pnl = f" · yaklaşık {'+' if not str(event['pnl']).startswith('-') else ''}{event['pnl']} USD (komisyon hariç)" if event.get("pnl") is not None else ""
    if kind == "target":
        return "close", f"🎯 {event['title']}", detail + pnl
    if kind == "stop":
        return "close", f"🛑 {event['title']}", detail + pnl
    if kind == "protect":
        return "close", f"⚠️ {event['title']}", detail
    if kind == "entry" and "bekleyen" in event["title"]:
        return "entry", f"✅ {event['title']}", detail
    if kind == "other":
        return "entry", f"ℹ️ {event['title']}", detail
    return None


async def check_user(pool: Any, user_id: str, client: Any, *, send: Any) -> list[dict[str, Any]]:
    """Bir hesabı kontrol eder; gönderilen olayları döner. send(pool, user_id, kategori, başlık, metin)."""
    await ensure_schema(pool)
    fills = await activity.fetch_fills(client)
    positions = await service.fetch_positions(client)
    now_map = _positions_map(positions)
    row = await pool.fetchrow(f"SELECT last_fill_time, positions FROM {WATCH_TABLE} WHERE user_id = $1", user_id)
    newest = max((fill["time"] for fill in fills), default=None)
    if row is None:
        await pool.execute(
            f"INSERT INTO {WATCH_TABLE} (user_id, last_fill_time, positions) VALUES ($1, $2, $3::jsonb) ON CONFLICT (user_id) DO NOTHING",
            user_id, newest, json.dumps(now_map, default=str))
        return []
    last = row["last_fill_time"]
    before = row["positions"]
    if isinstance(before, str):
        try:
            before = json.loads(before)
        except ValueError:
            before = {}
    before = before if isinstance(before, dict) else {}
    fresh = [fill for fill in fills if last is None or fill["time"] > last]
    sent: list[dict[str, Any]] = []
    if fresh:
        roles: dict[str, str] = {}
        for role in ("entry", "entry_rest", "stop", "close"):
            for order_id in await journal.our_order_ids(pool, user_id, (role,)):
                roles[order_id] = role
        try:
            sizes = activity.contract_sizes(await market.fetch_products(client))
        except Exception:
            sizes = {}
        state = {product: {"side": value.get("side"), "entry": activity._dec(value.get("entry"))} for product, value in before.items()}
        for order in sorted(activity.group_fills(fresh), key=lambda item: item["time"]):
            event = activity.classify(order, roles.get(order["order_id"]), state.get(order["product_id"]), sizes.get(order["product_id"]))
            if event["kind"] in ("target", "stop", "protect", "manual_close"):
                state.pop(order["product_id"], None)
            elif event["kind"] in ("entry", "other"):
                state[order["product_id"]] = {"side": "LONG" if order["side"] == "BUY" else "SHORT", "entry": order["price"]}
            await journal.record(pool, user_id, "watch", event["kind"], product_id=order["product_id"], order_id=order["order_id"],
                                 detail={"title": event["title"], "price": event["price"], "size": event["size"], "pnl": event["pnl"], "time": event["time"]})
            note = notification_for(event)
            if note is not None:
                try:
                    await send(pool, user_id, *note)
                except Exception as exc:
                    logger.warning("Coinbase bildirimi gönderilemedi (%s)", type(exc).__name__)
            sent.append(event)
    await pool.execute(
        f"UPDATE {WATCH_TABLE} SET last_fill_time = $2, positions = $3::jsonb, updated_at = NOW() WHERE user_id = $1",
        user_id, newest or last, json.dumps(now_map, default=str))
    return sent


EXPIRY_WARN_HOURS = 24
REALERT_MINUTES = 30      # korumasız kalan pozisyon için tekrar hatırlatma aralığı
CONFIRM_SIGHTINGS = 2     # emir akışı sürerken görülen anlık "stop yok" durumu alarm vermesin: iki tur üst üste görülmeli


def plan_protection_alerts(items: list[dict[str, Any]], before: Any, now: datetime) -> tuple[list[tuple[str, str, str]], dict[str, Any]]:
    """Saf karar: (gönderilecek bildirimler [(kategori, başlık, metin)], yeni alarm durumu).

    - Korumasız/uyumsuz pozisyon iki tur üst üste görülürse bildirim gider; sonra her 30 dk'da bir hatırlatılır.
    - Korumaya dönen pozisyon için tek seferlik "yeniden korumada" bildirimi gider.
    """
    before = before if isinstance(before, dict) else {}
    notes: list[tuple[str, str, str]] = []
    state: dict[str, Any] = {}
    seen_products = set()
    for item in items:
        product = str(item.get("product_id") or "")
        if not product:
            continue
        seen_products.add(product)
        previous = before.get(product) if isinstance(before.get(product), dict) else None
        if item.get("state") == "protected":
            if previous and previous.get("notified"):
                notes.append(("close", f"✅ {product} yeniden korumada", "Stop emri doğrulandı."))
            continue
        kind = str(item.get("state"))
        sightings = (int(previous.get("n", 0)) if previous and previous.get("state") == kind else 0) + 1
        entry: dict[str, Any] = {"state": kind, "n": sightings, "notified": bool(previous and previous.get("state") == kind and previous.get("notified")),
                                 "at": previous.get("at") if previous and previous.get("state") == kind else None}
        last = _parse_time(entry["at"])
        due = sightings >= CONFIRM_SIGHTINGS and (not entry["notified"] or last is None or now - last >= timedelta(minutes=REALERT_MINUTES))
        if due:
            head = "STOP YOK" if kind == "UNPROTECTED" else "STOP UYUŞMUYOR"
            notes.append(("close", f"🚨 {product} KORUMASIZ · {head}", str(item.get("message") or "Pozisyonun stopunu kontrol et.")))
            entry["notified"] = True
            entry["at"] = now.isoformat()
        state[product] = entry
    return notes, state


async def check_protection(pool: Any, user_id: str, client: Any, *, send: Any, now: datetime | None = None) -> list[str]:
    """Açık pozisyonların stopu yoksa/uyuşmuyorsa bildirir (SALT OKUNUR; emir göndermez, düzeltmez)."""
    from . import flow  # tembel: yalnız emir modülü açıkken çağrılır

    await ensure_schema(pool)
    now = now or datetime.now(timezone.utc)
    report = await flow.protection_report(client)
    row = await pool.fetchrow(f"SELECT alerts FROM {WATCH_TABLE} WHERE user_id = $1", user_id)
    before = row["alerts"] if row else None
    if isinstance(before, str):
        try:
            before = json.loads(before)
        except ValueError:
            before = {}
    notes, state = plan_protection_alerts(report["positions"], before, now)
    sent: list[str] = []
    for category, title, body in notes:
        await journal.record(pool, user_id, "watch", "unprotected" if title.startswith("🚨") else "reprotected", detail={"title": title, "body": body})
        try:
            await send(pool, user_id, category, title, body)
        except Exception as exc:
            logger.warning("Coinbase koruma bildirimi gönderilemedi (%s)", type(exc).__name__)
        sent.append(title)
    await pool.execute(f"UPDATE {WATCH_TABLE} SET alerts = $2::jsonb WHERE user_id = $1", user_id, json.dumps(state))
    return sent


def _parse_time(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def check_expiry(pool: Any, user_id: str, positions: list[dict[str, Any]], *, send: Any, now: datetime | None = None) -> list[str]:
    """Vadesine 24 saatten az kalan açık pozisyonlar için BİR KEZ bildirim (ürün + vade başına)."""
    now = now or datetime.now(timezone.utc)
    warned: list[str] = []
    for row in positions:
        expiry = _parse_time(row.get("expiration_time"))
        if expiry is None or expiry.year >= 2050:
            continue
        left = expiry - now
        if not (timedelta(0) < left <= timedelta(hours=EXPIRY_WARN_HOURS)):
            continue
        product = str(row["product_id"])
        key = expiry.isoformat()
        if await journal.has_event(pool, user_id, "expiry_warned", product_id=product, key=key, days=7):
            continue
        hours = max(1, round(left.total_seconds() / 3600))
        side = activity.position_side(row.get("side")) or ""
        await journal.record(pool, user_id, "watch", "expiry_warned", product_id=product, detail={"key": key, "hours": hours})
        try:
            await send(pool, user_id, "close", f"⏳ {product.split('-')[0]} pozisyonun {hours} saat sonra kapanıyor",
                       f"{product} {side} · vade dolunca pozisyon otomatik kapanır. İstersen önceden kapat ya da sonraki vadeye geç.")
        except Exception as exc:
            logger.warning("Coinbase vade bildirimi gönderilemedi (%s)", type(exc).__name__)
        warned.append(product)
    return warned


def week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def weekly_summary(feed: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Son 7 günün kapanışlarından özet. Kapanış yoksa None (boş rapor gönderilmez)."""
    since = now - timedelta(days=7)
    closes = [item for item in feed if item["kind"] in ("target", "stop", "manual_close") and (_parse_time(item["time"]) or now) >= since]
    if not closes:
        return None
    values = [(item, Decimal(item["pnl"])) for item in closes if item.get("pnl") is not None]
    total = sum((value for _, value in values), Decimal(0))
    wins = sum(1 for _, value in values if value > 0)
    best = max(values, key=lambda pair: pair[1]) if values else None
    worst = min(values, key=lambda pair: pair[1]) if values else None
    return {"trades": len(closes), "known": len(values), "wins": wins, "total": f"{total.quantize(Decimal('0.01')):f}",
            "best": (best[0]["product_id"].split("-")[0], f"{best[1].quantize(Decimal('0.01')):f}") if best else None,
            "worst": (worst[0]["product_id"].split("-")[0], f"{worst[1].quantize(Decimal('0.01')):f}") if worst else None}


async def check_weekly(pool: Any, user_id: str, client: Any, *, send: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """Pazartesi 06:00 UTC'den sonra haftada BİR KEZ özet bildirimi."""
    now = now or datetime.now(timezone.utc)
    if now.weekday() != 0 or now.hour < 6:
        return None
    key = week_key(now)
    if await journal.has_event(pool, user_id, "weekly_sent", key=key, days=8):
        return None
    roles: dict[str, str] = {}
    for role in ("entry", "entry_rest", "stop", "close"):
        for order_id in await journal.our_order_ids(pool, user_id, (role,)):
            roles[order_id] = role
    try:
        sizes = activity.contract_sizes(await market.fetch_products(client))
    except Exception:
        sizes = {}
    feed = activity.build_feed(activity.group_fills(await activity.fetch_fills(client)), roles, sizes)
    summary = weekly_summary(feed, now)
    await journal.record(pool, user_id, "watch", "weekly_sent", detail={"key": key, "summary": summary})
    if summary is None:
        return None
    total = Decimal(summary["total"])
    lines = [f"{summary['trades']} işlem kapandı · {summary['wins']}/{summary['known']} kârlı · toplam {'+' if total > 0 else ''}{summary['total']} USD"]
    if summary["best"] and summary["worst"] and summary["known"] > 1:
        lines.append(f"En iyi {summary['best'][0]} {summary['best'][1]} · en kötü {summary['worst'][0]} {summary['worst'][1]}")
    try:
        await send(pool, user_id, "close", "📊 Coinbase haftalık özet", " · ".join(lines) + " (komisyon hariç, yaklaşık)")
    except Exception as exc:
        logger.warning("Coinbase haftalık özet gönderilemedi (%s)", type(exc).__name__)
    return summary


async def _push(pool: Any, user_id: str, category: str, title: str, body: str) -> None:
    from ..web_push import configured, send_to_user
    if configured():
        await send_to_user(pool, user_id, category, title, body, url="/master-trade")


async def _users(pool: Any) -> list[str]:
    try:
        rows = await pool.fetch(f"SELECT user_id FROM {vault.TABLE}")
    except Exception:  # tablo henüz yoksa (hiç anahtar kaydedilmemiş) izlenecek hesap yok
        return []
    return [str(row["user_id"]) for row in rows]


async def run_once(app: Any) -> int:
    pool = getattr(app.state, "db_pool", None)
    if pool is None:
        return 0
    count = 0
    for user_id in await _users(pool):
        try:
            stored = await vault.load_key(pool, user_id)
            if stored is None:
                continue
            key_name, pem = stored
            client = CoinbaseClient(key_name, load_private_key(pem))
            count += len(await check_user(pool, user_id, client, send=_push))
            await check_expiry(pool, user_id, await service.fetch_positions(client), send=_push)
            if orders_enabled():
                try:
                    await check_protection(pool, user_id, TradingClient(key_name, load_private_key(pem)), send=_push)
                except Exception as exc:  # koruma kontrolü hatası diğer kontrolleri durdurmaz
                    logger.warning("Coinbase koruma kontrolü atlandı (%s)", type(exc).__name__)
            await check_weekly(pool, user_id, client, send=_push)
        except Exception as exc:
            logger.warning("Coinbase izleyici bu hesabı atladı (%s)", type(exc).__name__)
    return count


async def watcher_loop(app: Any) -> None:
    await asyncio.sleep(20)
    while True:
        try:
            await run_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Coinbase izleyici turu başarısız")
        await asyncio.sleep(INTERVAL_SECONDS)
