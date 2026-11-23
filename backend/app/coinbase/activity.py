"""Coinbase işlem geçmişi (SALT OKUNUR): dolumları okur, emir bazında toplar ve sade bir başlıkla sınıflar.

Sınıflama yalnızca bildiğimiz bilgiye dayanır:
- Günlükte (journal) rolü kayıtlı emirler: giriş, bekleyen giriş, koruma (stop/hedef), elle kapatma.
- Bilinmeyen emir: önceki pozisyonun ters yönündeyse "kapanış", fiyat girişe göre kârdaysa hedef, değilse stop sayılır.
  Pozisyon bilinmiyorsa uydurma yapılmaz: "Coinbase'de emir doldu" denir.
Hiçbir emir gönderilmez; tüm sayılar Coinbase'in verdiği metinden hesaplanır.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

FILLS_PATH = "/api/v3/brokerage/orders/historical/fills"
FUTURES_ID = re.compile(r"^[A-Z0-9]{2,12}-[A-Z0-9]{3,12}-CDE$")


def _dec(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def _iso(value: Any) -> str | None:
    """Zamanı tek biçime çevirir (karşılaştırma metinle yapılabilsin): 2026-11-21T05:31:22.123000+00:00."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def normalize_fills(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("fills") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_id = str(row.get("product_id") or "")
        price, size = _dec(row.get("price")), _dec(row.get("size"))
        order_id, time = str(row.get("order_id") or ""), _iso(row.get("trade_time") or row.get("sequence_timestamp"))
        side = str(row.get("side") or "").upper()
        if not FUTURES_ID.match(product_id) or price is None or size is None or size <= 0 or not order_id or not time or side not in ("BUY", "SELL"):
            continue
        result.append({"order_id": order_id, "product_id": product_id, "side": side, "price": price, "size": size,
                       "commission": _dec(row.get("commission")) or Decimal(0), "time": time})
    return result


async def fetch_fills(client: Any, limit: int = 100) -> list[dict[str, Any]]:
    return normalize_fills(await client.get(FILLS_PATH, params={"limit": limit}))


def group_fills(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı emrin parça dolumlarını tek satıra toplar (ağırlıklı ortalama fiyat). En yeni önce."""
    groups: dict[str, dict[str, Any]] = {}
    for fill in fills:
        item = groups.get(fill["order_id"])
        if item is None:
            groups[fill["order_id"]] = {**fill, "notional": fill["price"] * fill["size"]}
            continue
        item["size"] += fill["size"]
        item["notional"] += fill["price"] * fill["size"]
        item["commission"] += fill["commission"]
        item["time"] = max(item["time"], fill["time"])
    result = []
    for item in groups.values():
        item["price"] = (item.pop("notional") / item["size"]) if item["size"] > 0 else item["price"]
        result.append(item)
    result.sort(key=lambda row: row["time"], reverse=True)
    return result


def _root(product_id: str) -> str:
    return product_id.split("-", 1)[0]


def _fmt(value: Decimal, places: int = 8) -> str:
    text = f"{value.quantize(Decimal(1).scaleb(-places)).normalize():f}"
    return text if text not in ("-0", "") else "0"


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"


def position_side(raw: str | None) -> str | None:
    text = str(raw or "").upper()
    return "LONG" if text.endswith("LONG") else "SHORT" if text.endswith("SHORT") else None


def classify(order: dict[str, Any], role: str | None, prev: dict[str, Any] | None, contract_size: Decimal | None) -> dict[str, Any]:
    """Tek bir (toplanmış) emir dolumunu sade bir olaya çevirir. prev: dolumdan ÖNCEKİ pozisyon {side, entry}."""
    side = order["side"]
    product = order["product_id"]
    base = {"time": order["time"], "product_id": product, "side": side, "size": _fmt(order["size"]), "price": _fmt(order["price"]),
            "commission": _fmt(order["commission"], 4), "pnl": None}
    closing_dir = prev and ((prev["side"] == "LONG" and side == "SELL") or (prev["side"] == "SHORT" and side == "BUY"))
    pnl: Decimal | None = None
    if closing_dir and prev.get("entry") is not None and contract_size:
        sign = Decimal(1) if prev["side"] == "LONG" else Decimal(-1)
        pnl = (order["price"] - prev["entry"]) * order["size"] * contract_size * sign
    if role in ("entry", "entry_rest"):
        title = f"{_root(product)} {'LONG' if side == 'BUY' else 'SHORT'} girişi doldu" if role == "entry" else f"{_root(product)} bekleyen emrin doldu"
        return {**base, "kind": "entry", "tone": "info", "title": title}
    if role == "close":
        return {**base, "kind": "manual_close", "tone": "info", "title": f"{_root(product)} pozisyonunu kapattın",
                "pnl": _money(pnl) if pnl is not None else None}
    if role == "stop" or closing_dir:
        if pnl is None:
            return {**base, "kind": "protect", "tone": "warn", "title": f"{_root(product)} stop veya hedef çalıştı"}
        target = pnl >= 0
        return {**base, "kind": "target" if target else "stop", "tone": "good" if target else "bad",
                "title": f"{_root(product)} {'hedef' if target else 'stop'} çalıştı", "pnl": _money(pnl)}
    return {**base, "kind": "other", "tone": "info", "title": f"{_root(product)} {'alış' if side == 'BUY' else 'satış'} emri doldu (Coinbase)"}


def contract_sizes(products: list[dict[str, Any]]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for row in products:
        size = _dec(row.get("contract_size"))
        if size and size > 0:
            result[str(row.get("product_id"))] = size
    return result


def build_feed(orders: list[dict[str, Any]], roles: dict[str, str], sizes: dict[str, Decimal]) -> list[dict[str, Any]]:
    """Geçmiş akışı: eskiden yeniye yürüyerek her ürünün pozisyonunu tahmin eder; sonra en yeni önce döner.

    Pencereden önce açılmış pozisyon bilinmez; o yüzden ilk görülen emir yalnızca günlükteki rolüyle sınıflanır.
    """
    state: dict[str, dict[str, Any]] = {}
    feed: list[dict[str, Any]] = []
    for order in sorted(orders, key=lambda row: row["time"]):
        product = order["product_id"]
        role = roles.get(order["order_id"])
        prev = state.get(product)
        item = classify(order, role, prev, sizes.get(product))
        feed.append(item)
        direction = "LONG" if order["side"] == "BUY" else "SHORT"
        if item["kind"] in ("entry", "other") and (prev is None or prev["side"] == direction):
            # Aynı yöne ekleme: giriş fiyatı ağırlıklı ortalama olur, boyut toplanır.
            before = prev["size"] if prev else Decimal(0)
            total = before + order["size"]
            entry = order["price"] if prev is None or before <= 0 else (prev["entry"] * before + order["price"] * order["size"]) / total
            state[product] = {"side": direction, "entry": entry, "size": total}
        elif item["kind"] in ("target", "stop", "protect", "manual_close"):
            # Kısmi kapanışta pozisyon kalan boyutla devam eder (kalanın sonraki kapanışı da K/Z'sini bilir).
            remain = (prev["size"] if prev and prev.get("size") else Decimal(0)) - order["size"]
            if prev is not None and remain > 0:
                state[product] = {**prev, "size": remain}
            else:
                state.pop(product, None)
    feed.reverse()
    return feed


def position_fees(fills: list[dict[str, Any]], positions: list[dict[str, Any]]) -> dict[str, str | None]:
    """Açık pozisyon için ŞİMDİYE KADAR ÖDENEN komisyon (Coinbase dolumlarından). En yeni dolumdan geriye yürür,
    her dolumu geri alır; pozisyon sıfıra inince açılış bulunmuş olur. Yön değiştiren dolumun yalnız açan payı sayılır.
    Dolum geçmişi yetmezse None (uydurma yok)."""
    result: dict[str, str | None] = {}
    for position in positions:
        product = str(position.get("product_id") or "")
        side = position_side(position.get("side"))
        contracts = _dec(position.get("number_of_contracts"))
        if not product or side is None or contracts is None or contracts <= 0:
            continue
        left = contracts if side == "LONG" else -contracts
        fee: Decimal | None = Decimal(0)
        rows = sorted((row for row in fills if row["product_id"] == product), key=lambda row: row["time"], reverse=True)
        for row in rows:
            signed = row["size"] if row["side"] == "BUY" else -row["size"]
            after = left - signed
            if after == 0 or (after > 0) != (left > 0):
                share = abs(left) / row["size"] if after != 0 else Decimal(1)
                fee += row["commission"] * share
                left = Decimal(0)
                break
            fee += row["commission"]
            left = after
        result[product] = _fmt(fee, 4) if left == 0 else None
    return result
