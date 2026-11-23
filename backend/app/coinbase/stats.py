"""Coinbase işlem istatistikleri (SALT OKUNUR, saf hesap): kapanan işlemlerin komisyon dahil net sonucu, kazanma oranı,
coin bazında dağılım ve "Kais önerisiyle açılanlar" ile "elle açılanlar" karşılaştırması.

Tüm rakamlar Coinbase'in dolum kayıtlarından gelir; tahmin edilemeyen (K/Z'si bilinmeyen) kapanışlar sayıya katılmaz ve
`unknown` olarak ayrıca bildirilir. Hiçbir emir gönderilmez.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from . import activity

CLOSING = ("target", "stop", "manual_close", "protect")
OPENING = ("entry", "other")


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"


def build_trades(feed: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Geçmiş akışından kapanan işlemleri çıkarır. feed: build_feed çıktısı (en yeni önce). Dönüş: (işlemler eskiden yeniye, K/Z'si bilinmeyen sayısı).

    Net = (Coinbase'e göre kapanış K/Z) − (kapatılan paya düşen giriş komisyonu) − kapanış komisyonu.
    Kısmi kapanışta giriş komisyonu kapatılan oranda paylaştırılır; kalan pozisyon kalan komisyonu taşır."""
    state: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    unknown = 0
    for item in reversed(feed):
        product = item["product_id"]
        size = activity._dec(item.get("size")) or Decimal(0)
        fee = activity._dec(item.get("commission")) or Decimal(0)
        direction = "LONG" if item["side"] == "BUY" else "SHORT"
        if item["kind"] in OPENING:
            current = state.get(product)
            if current is None or current["side"] == direction:
                if current is None:
                    state[product] = {"side": direction, "size": size, "fee": fee, "since": item["time"]}
                else:
                    current["size"] += size
                    current["fee"] += fee
            continue
        if item["kind"] not in CLOSING:
            continue
        opened = state.get(product)
        pnl = activity._dec(item.get("pnl"))
        if opened is None or pnl is None or opened["size"] <= 0:
            unknown += 1
            state.pop(product, None)
            continue
        share = min(Decimal(1), size / opened["size"])
        entry_fee = opened["fee"] * share
        trades.append({
            "product_id": product, "kind": "manual_close" if item["kind"] == "manual_close" else "target" if item["kind"] == "target" else "stop",
            "time": item["time"], "opened": opened["since"], "gross": pnl, "fees": entry_fee + fee, "net": pnl - entry_fee - fee,
        })
        opened["size"] -= size
        opened["fee"] -= entry_fee
        if opened["size"] <= 0:
            state.pop(product, None)
    return trades, unknown


def attach_sources(trades: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    """Her işlemi, açılışından önceki en yakın giriş kaydına bağlar (plan kaynağı kais/manual; kayıt yoksa 'unknown')."""
    by_product: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_product.setdefault(entry["product_id"], []).append(entry)
    for trade in trades:
        pick = None
        for entry in by_product.get(trade["product_id"], []):
            if entry["time"] <= trade["opened"]:
                pick = entry
            else:
                break
        trade["source"] = (pick or {}).get("source") or "unknown"


def _bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wins = sum(1 for row in rows if row["net"] > 0)
    return {"trades": len(rows), "wins": wins, "net": _money(sum((row["net"] for row in rows), Decimal(0)))}


def summarize(trades: list[dict[str, Any]], unknown: int = 0) -> dict[str, Any]:
    count = len(trades)
    wins = [row for row in trades if row["net"] > 0]
    losses = [row for row in trades if row["net"] < 0]
    win_sum = sum((row["net"] for row in wins), Decimal(0))
    loss_sum = sum((row["net"] for row in losses), Decimal(0))
    coins: dict[str, list[dict[str, Any]]] = {}
    for row in trades:
        coins.setdefault(row["product_id"].split("-", 1)[0], []).append(row)
    by_coin = sorted(({"coin": coin, **_bucket(rows)} for coin, rows in coins.items()), key=lambda row: Decimal(row["net"]), reverse=True)
    best = max(trades, key=lambda row: row["net"]) if trades else None
    worst = min(trades, key=lambda row: row["net"]) if trades else None
    return {
        "trades": count, "wins": len(wins), "losses": len(losses), "unknown": unknown,
        "win_rate": round(len(wins) / count * 100, 1) if count else None,
        "net": _money(sum((row["net"] for row in trades), Decimal(0))),
        "gross": _money(sum((row["gross"] for row in trades), Decimal(0))),
        "fees": _money(sum((row["fees"] for row in trades), Decimal(0))),
        "avg_win": _money(win_sum / len(wins)) if wins else None,
        "avg_loss": _money(loss_sum / len(losses)) if losses else None,
        "profit_factor": round(float(win_sum / abs(loss_sum)), 2) if loss_sum < 0 else None,
        "best": {"coin": best["product_id"].split("-", 1)[0], "net": _money(best["net"])} if best else None,
        "worst": {"coin": worst["product_id"].split("-", 1)[0], "net": _money(worst["net"])} if worst else None,
        "by_coin": by_coin[:6],
        "by_exit": {kind: sum(1 for row in trades if row["kind"] == kind) for kind in ("target", "stop", "manual_close")},
        "by_source": {source: _bucket([row for row in trades if row.get("source", "unknown") == source]) for source in ("kais", "manual", "unknown")},
    }


def net_balance(balance: Decimal, daily_gross: Decimal, trades: list[dict[str, Any]], today: str) -> dict[str, Any]:
    """Tek "Net bakiye": hesaba işlenmiş bakiye + bugünkü gerçekleşen K/Z − bugünkü komisyon.

    Bugünkü komisyon, en yeni kapanıştan geriye doğru brüt K/Z'leri toplayıp Coinbase'in günlük gerçekleşen K/Z'sine
    ulaşan işlemlerin komisyonudur (gün sınırını Coinbase belirler). Tutmazsa bugünün (UTC) kapanışları kullanılır ve
    `exact` false döner. Bugün gerçekleşen K/Z yoksa komisyon düşülmez. Salt okunur, saf hesap."""
    fees = Decimal(0)
    exact = True
    if daily_gross != 0:
        picked: list[dict[str, Any]] = []
        total = Decimal(0)
        for row in sorted(trades, key=lambda item: item["time"], reverse=True):
            picked.append(row)
            total += row["gross"]
            if abs(total - daily_gross) < Decimal("0.01"):
                break
        else:
            picked = [row for row in trades if str(row["time"]).startswith(today)]
            exact = False
        fees = sum((row["fees"] for row in picked), Decimal(0))
    return {
        "balance": _money(balance), "gross": _money(daily_gross), "fees": _money(fees),
        "net_today": _money(daily_gross - fees), "net_balance": _money(balance + daily_gross - fees), "exact": exact,
    }
