"""Emir ön kontrolü: SAF hesap (ağ yok, veritabanı yok). Hepsi Decimal.

Girdi: istek + ürün bilgisi + fiyat + hesap + pozisyonlar + açık emirler + bugünkü zarar.
Çıktı: ok / sebepler (sade Türkçe, HEPSİ listelenir) / plan (açılacak miktar, stop, en kötü kayıp).
Kural: tek bir şüphe bile varsa ok=False. Eksik veri = red (tahmin yok).

En kötü kayıp hesabı: giriş = piyasadan olumsuz kayma payıyla, çıkış = stop-limit'in LİMİT fiyatı
(stop tetiklenir ama dolmazsa kayıp daha da büyür: bu yüzden ayrıca stop-limit mesafesi sınırlıdır),
artı açılış + kapanış komisyonu.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .limits import Limits
from .money import ROUND_DOWN, ROUND_UP, fmt, is_multiple, round_to_increment
from .service import level_rank

ZERO = Decimal("0")
ONE = Decimal("1")


def _amount_value(block: Any) -> Decimal | None:
    if isinstance(block, dict) and block.get("value") not in (None, ""):
        try:
            value = Decimal(str(block["value"]))
        except Exception:  # noqa: BLE001 - bozuk sayı = veri yok
            return None
        return value if value.is_finite() else None
    return None


def _window_bp(window: Any) -> Decimal | None:
    if isinstance(window, dict) and window.get("futures_buying_power") not in (None, ""):
        try:
            value = Decimal(str(window["futures_buying_power"]))
        except Exception:  # noqa: BLE001
            return None
        return value if value.is_finite() else None
    return None


def worst_case_loss(*, side: str, entry: Decimal, limit_price: Decimal, contracts: int, contract_size: Decimal, fee_rate: Decimal) -> Decimal:
    """Stop-limit limit fiyatından kapanırsa toplam kayıp (komisyon dahil)."""
    units = Decimal(contracts) * contract_size
    move = (entry - limit_price) if side == "BUY" else (limit_price - entry)
    price_loss = move * units
    fees = (entry + limit_price) * units * fee_rate
    return price_loss + fees


def run_preflight(
    *,
    request: dict[str, Any],
    product: dict[str, Any],
    bid: Decimal,
    ask: Decimal,
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    open_orders: list[dict[str, Any]],
    daily_loss_usd: Decimal,
    limits: Limits,
) -> dict[str, Any]:
    reasons: list[str] = []
    product_id = str(request.get("product_id") or "")
    side = str(request.get("side") or "").upper()

    if side not in ("BUY", "SELL"):
        reasons.append("Yön 'BUY' (alış/long) veya 'SELL' (satış/short) olmalı.")
    if not product_id:
        reasons.append("Ürün seçilmedi.")
    if product.get("product_id") not in (None, product_id):
        reasons.append("Ürün bilgisi istenen ürünle uyuşmuyor.")

    # --- ürün kuralları ---
    contract_size = product.get("contract_size")
    base_increment = product.get("base_increment")
    price_increment = product.get("price_increment")
    base_min = product.get("base_min_size")
    if not all(isinstance(v, Decimal) and v > 0 for v in (contract_size, base_increment, price_increment)):
        reasons.append("Ürün bilgisi eksik (sözleşme boyutu veya adımlar yok); güvenlik için emir verilmez.")
    if product.get("trading_disabled") is True or product.get("status") not in (None, "online"):
        reasons.append("Bu ürün şu an işlem görmüyor.")
    if reasons and (not isinstance(contract_size, Decimal) or not isinstance(price_increment, Decimal) or not isinstance(base_increment, Decimal)):
        return _result(False, reasons, None)

    # --- fiyat ---
    if bid <= 0 or ask <= 0 or bid > ask:
        return _result(False, reasons + ["Fiyat verisi geçersiz."], None)
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid
    if spread_pct > limits.max_spread_pct:
        reasons.append(f"Alış-satış farkı çok açık (%{fmt((spread_pct * 100).quantize(Decimal('0.01')))}); piyasa sağlıklı değil.")

    # --- stop ---
    try:
        stop_raw = Decimal(str(request.get("stop_price")))
        if not stop_raw.is_finite() or stop_raw <= 0:
            raise ValueError
    except Exception:  # noqa: BLE001
        return _result(False, reasons + ["Stop fiyatı zorunlu ve sıfırdan büyük olmalı (stop'suz işlem açılmaz)."], None)

    entry = ask * (ONE + limits.entry_slippage_pct) if side == "BUY" else bid * (ONE - limits.entry_slippage_pct)
    if side == "BUY":
        stop = round_to_increment(stop_raw, price_increment, ROUND_UP)      # girişe doğru yuvarla (güvenli)
        if stop >= bid:
            reasons.append("Alış (long) için stop fiyatı mevcut fiyatın ALTINDA olmalı.")
        limit_price = round_to_increment(stop * (ONE - limits.stop_limit_gap_pct), price_increment, ROUND_DOWN)
    else:
        stop = round_to_increment(stop_raw, price_increment, ROUND_DOWN)
        if stop <= ask:
            reasons.append("Satış (short) için stop fiyatı mevcut fiyatın ÜSTÜNDE olmalı.")
        limit_price = round_to_increment(stop * (ONE + limits.stop_limit_gap_pct), price_increment, ROUND_UP)
    if limit_price <= 0:
        return _result(False, reasons + ["Stop-limit fiyatı hesaplanamadı."], None)

    distance_pct = abs(entry - stop) / entry
    if distance_pct < limits.min_stop_distance_pct:
        reasons.append("Stop girişe çok yakın; hemen tetiklenir.")
    if distance_pct > limits.max_stop_distance_pct:
        reasons.append(f"Stop girişten çok uzak (en fazla %{fmt(limits.max_stop_distance_pct * 100)}).")

    # --- miktar: kayıp kuralı ---
    loss_per_contract = worst_case_loss(
        side=side, entry=entry, limit_price=limit_price, contracts=1, contract_size=contract_size, fee_rate=limits.fee_rate,
    )
    if loss_per_contract <= 0:
        return _result(False, reasons + ["Kayıp hesabı tutarsız (stop yanlış tarafta); emir verilmez."], None)
    max_by_loss = int((limits.max_loss_usd / loss_per_contract).to_integral_value(rounding=ROUND_DOWN))

    # --- miktar: kaldıraç ve marjin ---
    try:
        margin_usd = Decimal(str(request.get("margin_usd")))
        if not margin_usd.is_finite() or margin_usd <= 0:
            raise ValueError
    except Exception:  # noqa: BLE001
        return _result(False, reasons + ["Kullanılacak teminat (margin_usd) sıfırdan büyük olmalı."], None)
    notional_per_contract = entry * contract_size
    max_by_leverage = int((margin_usd * limits.leverage_max / notional_per_contract).to_integral_value(rounding=ROUND_DOWN))
    min_by_leverage = int((margin_usd * limits.leverage_min / notional_per_contract).to_integral_value(rounding=ROUND_UP))

    requested = request.get("contracts")
    contracts: int
    if requested in (None, ""):
        contracts = min(max_by_loss, max_by_leverage)
    else:
        try:
            contracts = int(str(requested))
        except ValueError:
            return _result(False, reasons + ["Sözleşme sayısı tam sayı olmalı."], None)
        if contracts < 1:
            return _result(False, reasons + ["Sözleşme sayısı en az 1 olmalı."], None)
        if contracts > max_by_loss:
            reasons.append(f"Bu miktarda en kötü kayıp {fmt(limits.max_loss_usd)} USD sınırını aşar (bu stop ile en fazla {max_by_loss} sözleşme).")
        if contracts > max_by_leverage:
            reasons.append(f"Bu miktar {fmt(limits.leverage_max)}x kaldıraç sınırını aşar (bu teminatla en fazla {max_by_leverage} sözleşme).")

    if contracts < 1:
        if max_by_loss < 1:
            reasons.append(
                f"1 sözleşme bile {fmt(limits.max_loss_usd)} USD kayıp sınırına sığmıyor "
                f"(1 sözleşme ≈ {fmt(loss_per_contract.quantize(Decimal('0.01')))} USD en kötü kayıp). Stop'u yaklaştır veya daha küçük sözleşmeli ürün seç."
            )
        else:
            reasons.append(
                f"Bu teminatla ({fmt(margin_usd)} USD) 1 sözleşme {fmt(limits.leverage_max)}x kaldıracı aşar. Teminatı artır."
            )
        return _result(False, reasons, None)

    if isinstance(base_min, Decimal) and Decimal(contracts) < base_min:
        reasons.append("Sözleşme sayısı ürünün en küçük miktarının altında.")
    if not is_multiple(Decimal(contracts), base_increment):
        reasons.append("Sözleşme sayısı ürünün miktar adımına uymuyor.")

    notional = notional_per_contract * contracts
    leverage = notional / margin_usd
    if leverage < limits.leverage_min:
        reasons.append(
            f"Kaldıraç {fmt(leverage.quantize(Decimal('0.01'), rounding=ROUND_DOWN))}x; en az {fmt(limits.leverage_min)}x olmalı. "
            f"Teminatı düşür veya miktarı artır (en az {min_by_leverage} sözleşme gerekir)."
        )
    if leverage > limits.leverage_max:
        reasons.append(f"Kaldıraç {fmt(leverage.quantize(Decimal('0.01'), rounding=ROUND_UP))}x; en fazla {fmt(limits.leverage_max)}x olabilir.")

    worst = worst_case_loss(side=side, entry=entry, limit_price=limit_price, contracts=contracts, contract_size=contract_size, fee_rate=limits.fee_rate)
    if worst > limits.max_loss_usd:
        reasons.append(f"En kötü kayıp {fmt(worst.quantize(Decimal('0.01')))} USD; sınır {fmt(limits.max_loss_usd)} USD.")

    # --- günlük zarar ---
    if daily_loss_usd + worst > limits.daily_loss_limit_usd:
        reasons.append(
            f"Günlük zarar sınırı ({fmt(limits.daily_loss_limit_usd)} USD) aşılır: bugün {fmt(daily_loss_usd.quantize(Decimal('0.01')))} USD zarar var."
        )

    # --- hesap: marjin seviyesi ve alım gücü (gün içi VE gece, hangisi kötüyse) ---
    balance = account.get("balance") if isinstance(account, dict) else None
    if not isinstance(balance, dict):
        reasons.append("Hesap bilgisi alınamadı; güvenlik için emir verilmez.")
    else:
        if level_rank(balance.get("worst_margin_level")) >= 1:
            reasons.append(f"Marjin seviyesi sağlıksız ({balance.get('worst_margin_level')}, pencere: {balance.get('worst_margin_window')}).")
        powers = [
            _amount_value(balance.get("futures_buying_power")),
            _amount_value(balance.get("available_margin")),
            _window_bp(balance.get("intraday_window")),
            _window_bp(balance.get("overnight_window")),
        ]
        if any(p is None for p in powers):
            reasons.append("Alım gücü bilgisi eksik; güvenlik için emir verilmez.")
        else:
            usable = min(powers)  # type: ignore[type-var]
            if margin_usd > usable:
                reasons.append(f"Alım gücü yetersiz: istenen teminat {fmt(margin_usd)} USD, kullanılabilir {fmt(usable.quantize(Decimal('0.01')))} USD (gün içi ve gece pencerelerinin kötüsü).")

    # --- sayı sınırları ---
    if any(str(p.get("product_id")) == product_id for p in positions):
        reasons.append("Bu üründe zaten açık pozisyon var; üstüne eklenmez.")
    if len(positions) >= limits.max_positions:
        reasons.append(f"En fazla {limits.max_positions} açık pozisyon olabilir.")
    if len(open_orders) + 1 > limits.max_open_orders:
        reasons.append(f"Açık emir sınırı ({limits.max_open_orders}) dolu; yeni koruma emri için yer yok.")

    plan = {
        "product_id": product_id, "side": side, "contracts": contracts,
        "contract_size": fmt(contract_size), "entry_reference": fmt(entry),
        "stop_price": fmt(stop), "stop_limit_price": fmt(limit_price),
        "notional_usd": fmt(notional.quantize(Decimal("0.01"))), "margin_usd": fmt(margin_usd),
        "leverage": fmt(leverage.quantize(Decimal("0.01"))),
        "worst_case_loss_usd": fmt(worst.quantize(Decimal("0.01"))),
        "stop_distance_pct": fmt((distance_pct * 100).quantize(Decimal("0.01"))),
    }
    return _result(not reasons, reasons, plan)


def _result(ok: bool, reasons: list[str], plan: dict[str, Any] | None) -> dict[str, Any]:
    return {"ok": ok and bool(plan) and not reasons, "reasons": reasons, "plan": plan}
