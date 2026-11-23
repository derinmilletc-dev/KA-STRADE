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

from . import activity, journal, market, profit_target, service, user_limits, vault
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


async def check_protection(pool: Any, user_id: str, client: Any, *, send: Any, now: datetime | None = None, report: dict[str, Any] | None = None) -> list[str]:
    """Açık pozisyonların stopu yoksa/uyuşmuyorsa bildirir (SALT OKUNUR; emir göndermez, düzeltmez). report verilirse yeniden çekilmez."""
    from . import flow  # tembel: yalnız emir modülü açıkken çağrılır

    await ensure_schema(pool)
    now = now or datetime.now(timezone.utc)
    report = report if report is not None else await flow.protection_report(client)
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


PROFIT_ALERT_AT = Decimal("0.7")   # hedefe giden yolun %70'inde "kârı koru" bildirimi
DAILY_WARN_AT = Decimal("0.8")     # günlük zarar limitinin %80'inde uyarı


def profit_alert(position: dict[str, Any], item: dict[str, Any]) -> tuple[str, str, str] | None:
    """Saf karar: fiyat hedefe giden yolun %70'ini geçtiyse (kâr yönünde, hedefe ulaşmadan) (anahtar, başlık, metin); yoksa None.
    Yalnız bildirir; emir vermez. Bildirim, kapatma ya da stop taşıma için ekrandaki onaylı düğmeleri gösterir."""
    side = activity.position_side(position.get("side"))
    entry, current = activity._dec(position.get("avg_entry_price")), activity._dec(position.get("current_price"))
    target, stop = activity._dec(item.get("take_profit_price")), activity._dec(item.get("stop_price"))
    contracts = activity._dec(position.get("number_of_contracts")) or Decimal(0)
    if side is None or entry is None or current is None or target is None or entry <= 0 or current <= 0:
        return None
    sign = Decimal(1) if side == "LONG" else Decimal(-1)
    full = (target - entry) * sign
    if full <= 0:
        return None
    progress = (current - entry) * sign / full
    if progress < PROFIT_ALERT_AT or progress >= 1:
        return None
    product = str(position["product_id"])
    pct = int((progress * 100).to_integral_value())
    if contracts >= 2:
        advice = "Yarısını kapatıp kârı kilitlemeyi düşün; kalanı hedefe bırakırsın."
    elif stop is None or (stop - entry) * sign < 0:
        advice = "Stopu girişe çekersen bu işlem zarara dönmez."
    else:
        return None   # tek sözleşme ve stop zaten kârda: önerilecek bir şey yok
    return f"{product}:{entry.normalize():f}:{target.normalize():f}", f"💰 {product.split('-')[0]} hedefin %{pct}'inde", advice


async def check_profit_alerts(pool: Any, user_id: str, positions: list[dict[str, Any]], report: dict[str, Any], *, send: Any) -> list[str]:
    """Hedefe yaklaşan pozisyonlar için BİR KEZ "kârı koru" bildirimi (ürün + giriş + hedef başına)."""
    sent: list[str] = []
    rows = {str(row["product_id"]): row for row in positions}
    for item in report.get("positions", []):
        position = rows.get(str(item.get("product_id")))
        alert = profit_alert(position, item) if position is not None else None
        if alert is None:
            continue
        key, title, body = alert
        product = str(item["product_id"])
        if await journal.has_event(pool, user_id, "profit_hint", product_id=product, key=key, days=7):
            continue
        await journal.record(pool, user_id, "watch", "profit_hint", product_id=product, detail={"key": key, "title": title})
        try:
            await send(pool, user_id, "close", title, body)
        except Exception as exc:
            logger.warning("Coinbase kâr bildirimi gönderilemedi (%s)", type(exc).__name__)
        sent.append(title)
    return sent


def daily_alert(loss: Decimal, limit: Decimal) -> tuple[str, str, str] | None:
    """Saf karar: bugünkü zarar günlük limitin %80'ine ya da tamamına ulaştıysa (seviye, başlık, metin). Limit yoksa (varsayılan tavan) None."""
    if limit <= 0 or limit >= Decimal(1_000_000) or loss <= 0:
        return None
    ratio = loss / limit
    fmt = lambda value: f"{value.quantize(Decimal('0.01')):f}"  # noqa: E731
    if ratio >= 1:
        return "daily_hit", "🛑 Günlük zarar limitin doldu", f"Bugün {fmt(loss)} USD zarar var (limit {fmt(limit)} USD). Yeni emirler limit sıfırlanana kadar verilmez."
    if ratio >= DAILY_WARN_AT:
        return "daily_warn", "⚠️ Günlük zarar limitine yaklaştın", f"Bugün {fmt(loss)} USD zarar var; limitin {fmt(limit)} USD (kalan {fmt(limit - loss)} USD)."
    return None


async def check_daily_loss(pool: Any, user_id: str, client: Any, *, send: Any, now: datetime | None = None) -> str | None:
    """Günlük zarar limitine (kullanıcının kendi limiti) yaklaşınca/ulaşınca günde BİR KEZ bildirir (seviye başına)."""
    from . import flow  # tembel: yalnız emir modülü açıkken çağrılır

    now = now or datetime.now(timezone.utc)
    limits = await user_limits.effective(pool, user_id)
    loss = flow.daily_loss_from_account(await service.fetch_account(client))
    alert = daily_alert(loss, limits.daily_loss_limit_usd)
    if alert is None:
        return None
    level, title, body = alert
    key = now.strftime("%Y-%m-%d")
    if await journal.has_event(pool, user_id, level, key=key, days=2):
        return None
    await journal.record(pool, user_id, "watch", level, detail={"key": key, "title": title, "loss": str(loss)})
    try:
        await send(pool, user_id, "close", title, body)
    except Exception as exc:
        logger.warning("Coinbase günlük zarar bildirimi gönderilemedi (%s)", type(exc).__name__)
    return level


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
        await send_to_user(pool, user_id, category, title, body, url="/master-trade?tab=pozisyonlar&ex=coinbase")


def combine(items: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """Aynı turda biriken bildirimleri tek özete çevirir. Tek bildirim aynen gider; 2+ ise başlık sayıyı verir,
    metin her bildirimin başlığını (ve kısa metnini) alt alta taşır. Kategori: herhangi biri 'close' ise 'close'."""
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    category = "close" if any(c == "close" for c, _, _ in items) else items[0][0]
    body = "\n".join(f"{title}: {text}" if text else title for _, title, text in items[:5])
    if len(items) > 5:
        body += f"\n… ve {len(items) - 5} bildirim daha"
    return category, f"Coinbase: {len(items)} yeni gelişme", body


class _Batch:
    """`send` yerine geçer: bildirimleri toplar, tur sonunda tek özet olarak gönderir (telefon dolmasın)."""

    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    async def __call__(self, pool: Any, user_id: str, category: str, title: str, body: str) -> None:
        self.items.append((category, title, body))

    async def flush(self, pool: Any, user_id: str) -> None:
        merged = combine(self.items)
        self.items = []
        if merged is not None:
            try:
                await _push(pool, user_id, *merged)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Coinbase özet bildirimi gönderilemedi (%s)", type(exc).__name__)


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
            batch = _Batch()
            try:
                client = CoinbaseClient(key_name, load_private_key(pem))
                count += len(await check_user(pool, user_id, client, send=batch))
                positions = await service.fetch_positions(client)
                await check_expiry(pool, user_id, positions, send=batch)
                if orders_enabled():
                    try:
                        from . import flow
                        trading = TradingClient(key_name, load_private_key(pem))
                        report = await flow.protection_report(trading)
                        await check_protection(pool, user_id, trading, send=batch, report=report)
                        await check_profit_alerts(pool, user_id, positions, report, send=batch)
                        await profit_target.check_targets(pool, user_id, trading, positions, send=batch)
                    except Exception as exc:  # koruma kontrolü hatası diğer kontrolleri durdurmaz
                        logger.warning("Coinbase koruma kontrolü atlandı (%s)", type(exc).__name__)
                    try:
                        await check_daily_loss(pool, user_id, client, send=batch)
                    except Exception as exc:  # günlük zarar kontrolü hatası diğer kontrolleri durdurmaz
                        logger.warning("Coinbase günlük zarar kontrolü atlandı (%s)", type(exc).__name__)
                await check_weekly(pool, user_id, client, send=batch)
            finally:
                await batch.flush(pool, user_id)
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
