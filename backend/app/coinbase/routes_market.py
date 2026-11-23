"""Coinbase piyasa verisi rotaları (SALT OKUNUR): ürün listesi ve anlık fiyat. Emir göndermez.

COINBASE_ENABLED açıkken yüklenir (emir bayrağından bağımsız); aynı erişim şartı: oturum + OWNER + izin listesi.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Path, Query, Request

from . import activity, journal, market, service, stats
from .errors import CoinbaseError
from .routes import _error, _member, _pool, _stored_client

router = APIRouter(prefix="/api/coinbase", tags=["Coinbase manual (market data)"])
PRODUCT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,39}$"


async def _reader(request: Request, user: dict[str, Any]) -> Any:
    return await _stored_client(request, user)


@router.get("/products")
async def coinbase_products(request: Request) -> Any:
    user = _member(request)
    try:
        client = await _reader(request, user)
        return {"products": await market.fetch_products(client)}
    except CoinbaseError as exc:
        return _error(exc)


@router.get("/quote/{product_id}")
async def coinbase_quote(request: Request, product_id: str = Path(pattern=PRODUCT_PATTERN)) -> Any:
    user = _member(request)
    try:
        client = await _reader(request, user)
        return await market.fetch_quote(client, product_id)
    except CoinbaseError as exc:
        return _error(exc)


@router.get("/quotes")
async def coinbase_quotes(request: Request, ids: str = Query(min_length=3, max_length=600)) -> Any:
    """Birden çok ürünün fiyatı tek istekte (vade seçimi için). Salt okunur."""
    import re
    user = _member(request)
    product_ids = [item.strip() for item in ids.split(",") if item.strip()]
    if not product_ids or len(product_ids) > market.MAX_BATCH_QUOTES or not all(re.match(PRODUCT_PATTERN, item) for item in product_ids):
        return _error(CoinbaseError("BAD_REQUEST", "Ürün kodları geçersiz.", http_status=422))
    try:
        client = await _reader(request, user)
        return {"quotes": await market.fetch_quotes(client, product_ids)}
    except CoinbaseError as exc:
        return _error(exc)


ALERT_TEXT = {
    "CRITICAL_UNPROTECTED": ("bad", "Pozisyon korumasız kaldı: hemen kontrol et"),
    "stop_failed": ("bad", "Stop kurulamadı"),
    "bracket_failed": ("bad", "Stop ve hedef kurulamadı"),
    "closed_after_stop_failure": ("warn", "Stop kurulamadığı için pozisyon kapatıldı"),
    "entry_not_filled": ("info", "Giriş emri dolmadı; işlem açılmadı"),
    "entry_rejected": ("warn", "Giriş emri Coinbase tarafından reddedildi"),
    "rest_rejected": ("warn", "Bekleyen emir Coinbase tarafından reddedildi"),
}


@router.get("/activity")
async def coinbase_activity(request: Request) -> Any:
    """İşlem geçmişi: Coinbase dolumları (emir bazında, sade başlıkla) + son önemli uyarılar. Salt okunur."""
    user = _member(request)
    try:
        client = await _reader(request, user)
        fills = await activity.fetch_fills(client)
        try:
            sizes = activity.contract_sizes(await market.fetch_products(client))
        except CoinbaseError:
            sizes = {}
    except CoinbaseError as exc:
        return _error(exc)
    roles: dict[str, str] = {}
    alerts: list[dict[str, Any]] = []
    try:
        pool = _pool(request)
        for role in ("entry", "entry_rest", "stop", "close"):
            for order_id in await journal.our_order_ids(pool, user["id"], (role,)):
                roles[order_id] = role
        for row in await journal.recent_alerts(pool, user["id"]):
            tone, title = ALERT_TEXT.get(row["outcome"], ("info", row["outcome"]))
            alerts.append({"time": row["time"], "product_id": row["product_id"], "tone": tone, "title": title})
    except Exception:  # günlük okunamazsa akış yine Coinbase dolumlarıyla gösterilir (rolsüz sınıflama)
        roles, alerts = {}, []
    feed = activity.build_feed(activity.group_fills(fills), roles, sizes)[:40]
    return {"items": feed, "alerts": alerts}


@router.get("/positions/fees")
async def coinbase_position_fees(request: Request) -> Any:
    """Açık pozisyonlar için ödenen komisyon (Coinbase'in "komisyon dahil" getirisiyle aynı K/Z için). Salt okunur."""
    user = _member(request)
    try:
        client = await _reader(request, user)
        positions = await service.fetch_positions(client)
        fills = await activity.fetch_fills(client, limit=250) if positions else []
    except CoinbaseError as exc:
        return _error(exc)
    return {"fees": activity.position_fees(fills, positions)}


@router.get("/stats")
async def coinbase_stats(request: Request) -> Any:
    """İşlem istatistikleri: kapanan işlemlerin komisyon dahil net sonucu ve Kais önerisi / elle karşılaştırması. Salt okunur."""
    user = _member(request)
    try:
        client = await _reader(request, user)
        fills = await activity.fetch_fills(client, limit=250)
        try:
            sizes = activity.contract_sizes(await market.fetch_products(client))
        except CoinbaseError:
            sizes = {}
    except CoinbaseError as exc:
        return _error(exc)
    roles: dict[str, str] = {}
    entries: list[dict[str, Any]] = []
    try:
        pool = _pool(request)
        for role in ("entry", "entry_rest", "stop", "close"):
            for order_id in await journal.our_order_ids(pool, user["id"], (role,)):
                roles[order_id] = role
        entries = await journal.entry_plans(pool, user["id"])
    except Exception:  # günlük okunamazsa istatistik yine hesaplanır; kaynak "bilinmiyor" olur
        roles, entries = {}, []
    feed = activity.build_feed(activity.group_fills(fills), roles, sizes)
    trades, unknown = stats.build_trades(feed)
    stats.attach_sources(trades, entries)
    return stats.summarize(trades, unknown)


@router.get("/net-balance")
async def coinbase_net_balance(request: Request) -> Any:
    """Tek "Net bakiye": ana sayfa, hesap kartı ve istatistik aynı rakamı buradan gösterir. Salt okunur."""
    from datetime import datetime, timezone
    user = _member(request)
    try:
        client = await _reader(request, user)
        account = (await service.fetch_account(client)).get("balance") or {}
        fills = await activity.fetch_fills(client, limit=250)
        try:
            sizes = activity.contract_sizes(await market.fetch_products(client))
        except CoinbaseError:
            sizes = {}
    except CoinbaseError as exc:
        return _error(exc)

    def number(key: str) -> Decimal | None:
        return activity._dec((account.get(key) or {}).get("value"))

    total, futures, gross = number("total_usd_balance"), number("cfm_usd_balance"), number("daily_realized_pnl")
    wallet = max(total or Decimal(0), futures or Decimal(0))
    if total is None and futures is None:
        return {"available": False}
    feed = activity.build_feed(activity.group_fills(fills), {}, sizes)
    trades, _unknown = stats.build_trades(feed)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {"available": True, **stats.net_balance(wallet, gross or Decimal(0), trades, today)}
