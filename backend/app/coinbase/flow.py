"""Coinbase emir akışları: aç (stop ile), kapat, azalt, stop koy/taşı, koruma raporu, yetim stop temizliği.

Temel ilke: stop'suz pozisyon KALMAZ.
- Stop kurulamaz / doğrulanamazsa pozisyon hemen kapatılır; kapanmazsa ACİL (critical) hata verilir.
- Emirler 'kais-' ile başlayan kendi kimliğimizle atılır; temizlik yalnız bu emirlere dokunur.
- Aynı anda tek akış (kilit). Sonucu belirsiz her adım açık hata olarak görünür; hiçbiri yutulmaz.
- Reduce-only yok: bu yüzden pozisyon azaltırken/kapatırken eski stop'un yanlış yönde pozisyon açmaması için
  sıra dikkatle seçilmiştir (aşağıda).
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any, Awaitable, Callable

from . import journal, order_api, service
from .errors import CoinbaseError
from .limits import Limits
from .money import ROUND_DOWN, ROUND_UP, fmt, round_to_increment, to_decimal
from .preflight import run_preflight
from .trading_client import CoinbaseTradingClient

ORDER_POLLS = 8
POLL_SECONDS = 0.5
CLOSE_POLLS = 8
FILLED_FINAL = ("FILLED", "CANCELLED", "EXPIRED", "FAILED")
OK_STOP_STATUSES = ("OPEN", "PENDING", "QUEUED")
PREFIX = "kais-"
STOP_PREFIX = "kais-s-"

Sleep = Callable[[float], Awaitable[None]]


def _cid(kind: str) -> str:
    return f"{PREFIX}{kind}-{uuid.uuid4().hex[:24]}"


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY"


def _critical(code: str, message: str, **extra: Any) -> CoinbaseError:
    return CoinbaseError(code, message, http_status=409, extra={"critical": True, **extra})


def position_side(position: dict[str, Any]) -> str:
    raw = str(position.get("side") or "").upper()
    if raw.endswith("LONG"):
        return "BUY"
    if raw.endswith("SHORT"):
        return "SELL"
    raise CoinbaseError("BAD_RESPONSE", "Coinbase pozisyon yönü okunamadı (LONG/SHORT yok).")


def position_contracts(position: dict[str, Any]) -> int:
    value = to_decimal(position.get("number_of_contracts"), "sözleşme sayısı")
    if value != value.to_integral_value() or value < 0:
        raise CoinbaseError("BAD_RESPONSE", "Coinbase sözleşme sayısı tam sayı değil.")
    return int(value)


def _find_position(positions: list[dict[str, Any]], product_id: str) -> dict[str, Any] | None:
    for item in positions:
        if item.get("product_id") == product_id and position_contracts(item) > 0:
            return item
    return None


def daily_loss_from_account(account: dict[str, Any]) -> Decimal:
    """Bugünkü gerçekleşen ZARAR (pozitif sayı). Bilgi yoksa hata: tahminle devam edilmez."""
    block = (account.get("balance") or {}).get("daily_realized_pnl")
    if not isinstance(block, dict) or block.get("value") in (None, ""):
        raise CoinbaseError("BAD_RESPONSE", "Günlük kâr/zarar bilgisi Coinbase'den alınamadı; güvenlik için emir verilmez.")
    pnl = to_decimal(block["value"], "günlük kâr/zarar")
    return -pnl if pnl < 0 else Decimal(0)


# ---------------------------------------------------------------- ön kontrol

async def gather_state(client: CoinbaseTradingClient, product_id: str) -> dict[str, Any]:
    product = await order_api.get_product(client, product_id)
    bid, ask = await order_api.best_bid_ask(client, product_id)
    account = await service.fetch_account(client)
    positions = await service.fetch_positions(client)
    open_orders = await order_api.list_active_orders(client)
    return {"product": product, "bid": bid, "ask": ask, "account": account, "positions": positions, "open_orders": open_orders}


async def preflight(client: CoinbaseTradingClient, limits: Limits, request: dict[str, Any]) -> dict[str, Any]:
    product_id = str(request.get("product_id") or "")
    if not product_id:
        raise CoinbaseError("BAD_REQUEST", "Ürün seçilmedi.", http_status=422)
    state = await gather_state(client, product_id)
    result = run_preflight(
        request=request, product=state["product"], bid=state["bid"], ask=state["ask"], account=state["account"],
        positions=state["positions"], open_orders=state["open_orders"],
        daily_loss_usd=daily_loss_from_account(state["account"]), limits=limits,
    )
    result["state"] = {
        "bid": fmt(state["bid"]), "ask": fmt(state["ask"]), "open_positions": len(state["positions"]),
        "open_orders": len(state["open_orders"]),
    }
    return result


# ---------------------------------------------------------------- yardımcılar

async def _wait_order(client: CoinbaseTradingClient, order_id: str, sleep: Sleep, finals: tuple[str, ...] = FILLED_FINAL) -> dict[str, Any]:
    order: dict[str, Any] = {}
    for _ in range(ORDER_POLLS):
        order = await order_api.get_order(client, order_id)
        if order["status"] in finals:
            return order
        await sleep(POLL_SECONDS)
    return order


async def _cancel_verified(client: CoinbaseTradingClient, order_ids: list[str]) -> list[str]:
    """İptal et ve DOĞRULA. İptal edilemeyen (hâlâ aktif) emir kimliklerini döner."""
    if not order_ids:
        return []
    outcome = await order_api.cancel_orders(client, order_ids)
    stuck: list[str] = []
    for order_id in order_ids:
        ok, _reason = outcome.get(order_id, (False, ""))
        if ok:
            continue
        current = await order_api.get_order(client, order_id)
        if current["status"] not in ("CANCELLED", "EXPIRED", "FAILED"):
            stuck.append(order_id)
    return stuck


async def our_stops(client: CoinbaseTradingClient, product_id: str | None = None) -> list[dict[str, Any]]:
    orders = await order_api.list_active_orders(client, product_id)
    return [o for o in orders if str(o.get("client_order_id") or "").startswith(STOP_PREFIX)]


async def _wait_flat(client: CoinbaseTradingClient, product_id: str, sleep: Sleep) -> bool:
    for _ in range(CLOSE_POLLS):
        if _find_position(await service.fetch_positions(client), product_id) is None:
            return True
        await sleep(POLL_SECONDS)
    return False


async def _close_all(client: CoinbaseTradingClient, product_id: str, contracts: int, sleep: Sleep) -> bool:
    """Pozisyonu piyasa fiyatından kapat ve kapandığını DOĞRULA."""
    await order_api.close_position(client, product_id, Decimal(contracts), _cid("c"))
    return await _wait_flat(client, product_id, sleep)


async def _place_stop(client: CoinbaseTradingClient, *, product_id: str, side: str, contracts: int, stop: Decimal, limit: Decimal) -> str:
    """Pozisyon yönüne göre stop-limit koy (long için SELL/STOP_DOWN, short için BUY/STOP_UP)."""
    body = {
        "client_order_id": _cid("s"),
        "product_id": product_id,
        "side": _opposite(side),
        "order_configuration": {
            "stop_limit_stop_limit_gtc": {
                "base_size": str(contracts),
                "limit_price": fmt(limit),
                "stop_price": fmt(stop),
                "stop_direction": "STOP_DOWN" if side == "BUY" else "STOP_UP",
            }
        },
    }
    return await order_api.create_order(client, body, "Stop")


async def _verify_stop(client: CoinbaseTradingClient, order_id: str, *, side: str, contracts: int, stop: Decimal) -> str | None:
    """Stop gerçekten Coinbase'de, doğru yön/büyüklük/fiyatta mı? Sorun varsa sebep metni, yoksa None."""
    order = await order_api.get_order(client, order_id)
    if order["status"] not in OK_STOP_STATUSES:
        return f"Stop emri aktif değil (durum: {order['status']})."
    if order["order_type"] and not order["order_type"].startswith("STOP"):
        return f"Stop emri beklenen türde değil ({order['order_type']})."
    if order["side"] != _opposite(side):
        return "Stop emri ters yönde değil."
    if order["base_size"] is None or order["base_size"] != Decimal(contracts):
        return "Stop emrinin büyüklüğü pozisyonla aynı değil."
    if order["stop_price"] is None or order["stop_price"] != stop:
        return "Stop emrinin fiyatı beklenenden farklı."
    return None


def _stop_prices(plan: dict[str, Any]) -> tuple[Decimal, Decimal]:
    return Decimal(plan["stop_price"]), Decimal(plan["stop_limit_price"])


async def _protect_or_close(
    client: CoinbaseTradingClient, pool: Any, user_id: str, *, product_id: str, side: str, contracts: int,
    stop: Decimal, limit: Decimal, sleep: Sleep, action: str,
) -> str:
    """Stop koy + doğrula. Olmazsa pozisyonu kapat. Kapanmazsa ACİL hata. Başarıda stop emir kimliğini döner."""
    problem: str | None = None
    stop_id: str | None = None
    try:
        stop_id = await _place_stop(client, product_id=product_id, side=side, contracts=contracts, stop=stop, limit=limit)
        await journal.record(pool, user_id, action, "stop_placed", product_id=product_id, order_id=stop_id, role="stop")
        problem = await _verify_stop(client, stop_id, side=side, contracts=contracts, stop=stop)
    except CoinbaseError as exc:
        problem = exc.message
    if problem is None and stop_id is not None:
        await journal.record(pool, user_id, action, "stop_verified", product_id=product_id, order_id=stop_id, role="stop")
        return stop_id

    await journal.record(pool, user_id, action, "stop_failed", product_id=product_id, order_id=stop_id, detail={"problem": problem})
    # Yarım kalmış stop varsa önce onu kaldır (pozisyon kapanınca ters yönde pozisyon açmasın)
    leftover_note = ""
    if stop_id is not None:
        try:
            stuck = await _cancel_verified(client, [stop_id])
            if stuck:
                leftover_note = f" Stop emri ({stop_id}) iptal edilemedi; Coinbase'den elle kaldır."
        except CoinbaseError as exc:
            leftover_note = f" Stop emri ({stop_id}) iptal doğrulanamadı: {exc.message}"
    try:
        closed = await _close_all(client, product_id, contracts, sleep)
    except CoinbaseError as exc:
        closed = False
        problem = f"{problem} Kapatma isteği de hata verdi: {exc.message}"
    if closed:
        await journal.record(pool, user_id, action, "closed_after_stop_failure", product_id=product_id, role="close")
        raise CoinbaseError(
            "STOP_FAILED_POSITION_CLOSED",
            f"Stop doğrulanamadı, bu yüzden pozisyon hemen kapatıldı. Sebep: {problem}{leftover_note}",
            http_status=409, extra={"closed": True},
        )
    await journal.record(pool, user_id, action, "CRITICAL_UNPROTECTED", product_id=product_id, detail={"problem": problem})
    raise _critical(
        "CRITICAL_UNPROTECTED",
        f"ACİL: {product_id} pozisyonu STOP'SUZ ve kapatılamadı! Hemen Coinbase'den elle kapat. Sebep: {problem}{leftover_note}",
        product_id=product_id,
    )


# ---------------------------------------------------------------- aç

async def open_position(
    client: CoinbaseTradingClient, pool: Any, user_id: str, limits: Limits, request: dict[str, Any], *, sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    check = await preflight(client, limits, request)
    if not check["ok"]:
        await journal.record(pool, user_id, "open", "preflight_failed", product_id=request.get("product_id"), detail={"reasons": check["reasons"]})
        raise CoinbaseError(
            "PREFLIGHT_FAILED", "Ön kontrol geçmedi, emir verilmedi: " + " ".join(check["reasons"]),
            http_status=422, extra={"reasons": check["reasons"], "plan": check["plan"]},
        )
    plan = check["plan"]
    product_id, side, planned = plan["product_id"], plan["side"], int(plan["contracts"])
    stop, limit = _stop_prices(plan)

    body = {
        "client_order_id": _cid("e"),
        "product_id": product_id,
        "side": side,
        "order_configuration": {"market_market_ioc": {"base_size": str(planned)}},
        "leverage": fmt(Decimal(plan["leverage"]).quantize(Decimal("1"), rounding=ROUND_DOWN)) if Decimal(plan["leverage"]) >= 1 else "1",
    }
    await journal.record(pool, user_id, "open", "entry_sending", product_id=product_id, role="entry", detail=plan)
    entry_id: str | None = None
    try:
        entry_id = await order_api.create_order(client, body, "Giriş")
    except order_api.OrderRejected:
        await journal.record(pool, user_id, "open", "entry_rejected", product_id=product_id)
        raise
    except CoinbaseError as exc:
        if exc.code != "ORDER_OUTCOME_UNKNOWN":
            raise
        # Sonuç belirsiz: gerçek durum pozisyondan okunur (tek akış + üstüne ekleme yasağı sayesinde pozisyon bizimdir)
        await journal.record(pool, user_id, "open", "entry_unknown", product_id=product_id)
    await journal.record(pool, user_id, "open", "entry_sent", product_id=product_id, order_id=entry_id, role="entry")

    if entry_id is not None:
        order = await _wait_order(client, entry_id, sleep)
        if order["status"] not in FILLED_FINAL:
            stuck = await _cancel_verified(client, [entry_id])
            if stuck:
                raise _critical("ENTRY_STILL_OPEN", f"Giriş emri ({entry_id}) tamamlanmadı ve iptal edilemedi. Coinbase'de kontrol et.", product_id=product_id)

    position = _find_position(await service.fetch_positions(client), product_id)
    if position is None:
        await journal.record(pool, user_id, "open", "entry_not_filled", product_id=product_id, order_id=entry_id)
        if entry_id is None:
            raise _critical("ORDER_OUTCOME_UNKNOWN", "Giriş emrinin sonucu belirsiz ve pozisyon görünmüyor. Coinbase'de kontrol et.", product_id=product_id)
        raise CoinbaseError("ENTRY_NOT_FILLED", "Giriş emri dolmadı, hiçbir pozisyon açılmadı.", http_status=409)

    actual_side, contracts = position_side(position), position_contracts(position)
    if actual_side != side:
        await journal.record(pool, user_id, "open", "unexpected_side", product_id=product_id)
        raise _critical("UNEXPECTED_POSITION", f"{product_id} pozisyonu beklenen yönde değil. Coinbase'de kontrol et.", product_id=product_id)

    stop_id = await _protect_or_close(
        client, pool, user_id, product_id=product_id, side=side, contracts=contracts, stop=stop, limit=limit, sleep=sleep, action="open",
    )
    return {
        "status": "opened", "plan": plan, "contracts": contracts, "entry_order_id": entry_id, "stop_order_id": stop_id,
        "partial_fill": contracts != planned,
        "message": f"Pozisyon açıldı ve stop doğrulandı ({contracts} sözleşme).",
    }


# ---------------------------------------------------------------- kapat

async def close_flow(
    client: CoinbaseTradingClient, pool: Any, user_id: str, product_id: str, *, sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    position = _find_position(await service.fetch_positions(client), product_id)
    if position is None:
        raise CoinbaseError("NO_POSITION", "Bu üründe açık pozisyon yok.", http_status=409)
    contracts = position_contracts(position)
    stops = await our_stops(client, product_id)
    await journal.record(pool, user_id, "close", "closing", product_id=product_id)
    # Önce pozisyon kapanır (korumasız süre yok), SONRA stop kaldırılır; ters pozisyon riski için temizlik doğrulanır.
    try:
        closed = await _close_all(client, product_id, contracts, sleep)
    except CoinbaseError as exc:
        await journal.record(pool, user_id, "close", "close_failed", product_id=product_id, detail={"error": exc.message})
        raise
    if not closed:
        await journal.record(pool, user_id, "close", "close_unconfirmed", product_id=product_id)
        raise _critical("CLOSE_UNCONFIRMED", f"{product_id} kapatma isteği gitti ama pozisyon hâlâ görünüyor. Coinbase'de kontrol et (stop yerinde kaldı).", product_id=product_id)
    note = ""
    try:
        stuck = await _cancel_verified(client, [s["order_id"] for s in stops])
        if stuck:
            note = f" UYARI: stop emri iptal edilemedi ({', '.join(stuck)}); ters pozisyon açabilir, Coinbase'den elle kaldır."
    except CoinbaseError as exc:
        stuck = ["?"]
        note = f" UYARI: stop emirleri iptal doğrulanamadı: {exc.message}"
    await journal.record(pool, user_id, "close", "closed", product_id=product_id, detail={"stops_left": stuck})
    if note:
        raise _critical("ORPHAN_STOP", f"Pozisyon kapandı.{note}", product_id=product_id, closed=True)
    return {"status": "closed", "product_id": product_id, "message": "Pozisyon kapatıldı ve stop emirleri temizlendi."}


# ---------------------------------------------------------------- stop koy / taşı

async def set_stop(
    client: CoinbaseTradingClient, pool: Any, user_id: str, limits: Limits, product_id: str, new_stop_price: Any, *, sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    """Stop'u yeni fiyata koy/taşı. Sadece SIKILAŞTIRILIR (kayıp artmaz). Korumasız pozisyona ilk stop'u da koyar."""
    state = await gather_state(client, product_id)
    position = _find_position(state["positions"], product_id)
    if position is None:
        raise CoinbaseError("NO_POSITION", "Bu üründe açık pozisyon yok.", http_status=409)
    side, contracts = position_side(position), position_contracts(position)
    product = state["product"]
    try:
        stop_raw = to_decimal(new_stop_price, "stop fiyatı")
    except CoinbaseError:
        raise CoinbaseError("BAD_REQUEST", "Stop fiyatı sayı olmalı.", http_status=422) from None
    if side == "BUY":
        stop = round_to_increment(stop_raw, product["price_increment"], ROUND_UP)
        if stop >= state["bid"]:
            raise CoinbaseError("BAD_REQUEST", "Long pozisyonda stop fiyatı mevcut fiyatın altında olmalı.", http_status=422)
        limit = round_to_increment(stop * (1 - limits.stop_limit_gap_pct), product["price_increment"], ROUND_DOWN)
    else:
        stop = round_to_increment(stop_raw, product["price_increment"], ROUND_DOWN)
        if stop <= state["ask"]:
            raise CoinbaseError("BAD_REQUEST", "Short pozisyonda stop fiyatı mevcut fiyatın üstünde olmalı.", http_status=422)
        limit = round_to_increment(stop * (1 + limits.stop_limit_gap_pct), product["price_increment"], ROUND_UP)

    old = await our_stops(client, product_id)
    for order in old:
        previous = order["stop_price"]
        if previous is None:
            raise CoinbaseError("BAD_RESPONSE", "Mevcut stop fiyatı okunamadı; taşıma yapılmaz.")
        loosening = stop < previous if side == "BUY" else stop > previous
        if loosening:
            raise CoinbaseError("STOP_LOOSENING", "Stop yalnızca pozisyona yaklaştırılabilir (kayıp artırılamaz).", http_status=422)
    await journal.record(pool, user_id, "stop_move", "moving", product_id=product_id, detail={"to": fmt(stop), "from": [fmt(o["stop_price"]) for o in old]})

    # Önce eski stop kalkar (iki stop birlikte tetiklenip ters pozisyon açmasın), hemen yenisi konur; olmazsa eskisi geri / pozisyon kapanır.
    if old:
        stuck = await _cancel_verified(client, [o["order_id"] for o in old])
        if stuck:
            raise _critical("STOP_CANCEL_FAILED", "Eski stop iptal edilemedi; stop taşınmadı. Mevcut durumu Coinbase'de kontrol et.", product_id=product_id)
    try:
        stop_id = await _protect_or_close(
            client, pool, user_id, product_id=product_id, side=side, contracts=contracts, stop=stop, limit=limit, sleep=sleep, action="stop_move",
        )
    except CoinbaseError:
        raise
    return {"status": "stop_set", "product_id": product_id, "stop_order_id": stop_id, "stop_price": fmt(stop), "message": "Stop yerleştirildi ve doğrulandı."}


# ---------------------------------------------------------------- azalt

async def reduce_flow(
    client: CoinbaseTradingClient, pool: Any, user_id: str, limits: Limits, product_id: str, reduce_by: Any, *, sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
    state = await gather_state(client, product_id)
    position = _find_position(state["positions"], product_id)
    if position is None:
        raise CoinbaseError("NO_POSITION", "Bu üründe açık pozisyon yok.", http_status=409)
    side, contracts = position_side(position), position_contracts(position)
    try:
        amount = int(str(reduce_by))
    except ValueError:
        raise CoinbaseError("BAD_REQUEST", "Azaltılacak sözleşme sayısı tam sayı olmalı.", http_status=422) from None
    if amount < 1 or amount >= contracts:
        raise CoinbaseError("BAD_REQUEST", f"Azaltma 1 ile {contracts - 1} arasında olmalı (tamamını kapatmak için 'kapat' kullan).", http_status=422)
    old = await our_stops(client, product_id)
    if len(old) != 1 or old[0]["stop_price"] is None:
        raise CoinbaseError("NO_KNOWN_STOP", "Bu pozisyonda tek bir bilinen stop emri yok; önce stop'u düzelt, sonra azalt.", http_status=409)
    stop = old[0]["stop_price"]
    if side == "BUY":
        limit = round_to_increment(stop * (1 - limits.stop_limit_gap_pct), state["product"]["price_increment"], ROUND_DOWN)
    else:
        limit = round_to_increment(stop * (1 + limits.stop_limit_gap_pct), state["product"]["price_increment"], ROUND_UP)
    remaining = contracts - amount
    await journal.record(pool, user_id, "reduce", "reducing", product_id=product_id, detail={"amount": amount, "remaining": remaining})

    # Sıra: eski (büyük) stop kalkar -> azalt -> küçük stop konur. Büyük stop açık kalsaydı tetiklenince ters pozisyon açardı.
    stuck = await _cancel_verified(client, [old[0]["order_id"]])
    if stuck:
        raise _critical("STOP_CANCEL_FAILED", "Eski stop iptal edilemedi; azaltma yapılmadı. Coinbase'de kontrol et.", product_id=product_id)
    try:
        await order_api.close_position(client, product_id, Decimal(amount), _cid("c"))
        await sleep(POLL_SECONDS)
        position = _find_position(await service.fetch_positions(client), product_id)
        current = position_contracts(position) if position else 0
    except CoinbaseError as exc:
        # Azaltma doğrulanamadı: durum belirsiz, korumasız bırakma -> kalan neyse tam pozisyonu koru/kapat
        await journal.record(pool, user_id, "reduce", "reduce_failed", product_id=product_id, detail={"error": exc.message})
        position = _find_position(await service.fetch_positions(client), product_id)
        current = position_contracts(position) if position else 0
        if current == 0:
            raise
        await _protect_or_close(client, pool, user_id, product_id=product_id, side=side, contracts=current, stop=stop, limit=limit, sleep=sleep, action="reduce")
        raise CoinbaseError("REDUCE_FAILED", f"Azaltma başarısız: {exc.message}. Pozisyon ({current} sözleşme) stop ile korundu.", http_status=409) from None
    if current == 0:
        raise _critical("UNEXPECTED_FLAT", "Azaltma sonrası pozisyon tamamen kapanmış görünüyor. Coinbase'de kontrol et.", product_id=product_id, closed=True)
    stop_id = await _protect_or_close(client, pool, user_id, product_id=product_id, side=side, contracts=current, stop=stop, limit=limit, sleep=sleep, action="reduce")
    await journal.record(pool, user_id, "reduce", "reduced", product_id=product_id, order_id=stop_id, detail={"remaining": current})
    return {"status": "reduced", "product_id": product_id, "remaining_contracts": current, "stop_order_id": stop_id, "message": f"{amount} sözleşme azaltıldı; kalan {current} sözleşme stop ile korunuyor."}


# ---------------------------------------------------------------- koruma raporu / yetim stop

async def protection_report(client: CoinbaseTradingClient) -> dict[str, Any]:
    positions = await service.fetch_positions(client)
    stops = await our_stops(client)
    open_orders = await order_api.list_active_orders(client)
    items: list[dict[str, Any]] = []
    for position in positions:
        if position_contracts(position) <= 0:
            continue
        product_id = str(position["product_id"])
        side, contracts = position_side(position), position_contracts(position)
        mine = [s for s in stops if s["product_id"] == product_id]
        matching = [s for s in mine if s["side"] == _opposite(side) and s["base_size"] == Decimal(contracts)]
        if len(matching) == 1 and len(mine) == 1:
            state, text = "protected", "Korunuyor."
        elif not mine:
            state, text = "UNPROTECTED", "STOP YOK! Hemen stop koy veya pozisyonu kapat."
        else:
            state, text = "MISMATCH", "Stop emri pozisyonla uyuşmuyor (büyüklük/yön/adet). Düzelt."
        items.append({
            "product_id": product_id, "side": side, "contracts": contracts, "state": state, "message": text,
            "stop_price": fmt(matching[0]["stop_price"]) if matching and matching[0]["stop_price"] is not None else None,
        })
    flat = {str(p["product_id"]) for p in positions if position_contracts(p) > 0}
    orphans = [
        {"order_id": s["order_id"], "product_id": s["product_id"]} for s in stops if s["product_id"] not in flat
    ]
    return {
        "positions": items, "orphan_stops": orphans, "open_order_count": len(open_orders),
        "all_protected": all(i["state"] == "protected" for i in items),
        "has_problem": any(i["state"] != "protected" for i in items) or bool(orphans),
    }


async def cleanup_orphans(client: CoinbaseTradingClient, pool: Any, user_id: str) -> dict[str, Any]:
    report = await protection_report(client)
    ids = [o["order_id"] for o in report["orphan_stops"]]
    stuck = await _cancel_verified(client, ids)
    await journal.record(pool, user_id, "cleanup", "orphans_cancelled", detail={"ids": ids, "stuck": stuck})
    if stuck:
        raise _critical("ORPHAN_STOP", f"Bazı yetim stop emirleri iptal edilemedi: {', '.join(stuck)}. Coinbase'den elle kaldır.")
    return {"status": "cleaned", "cancelled": ids, "message": f"{len(ids)} yetim stop emri iptal edildi." if ids else "Yetim stop yok."}
