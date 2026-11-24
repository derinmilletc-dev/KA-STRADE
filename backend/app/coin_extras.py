"""Yorum motorunun ek katmanları (saf hesap; ağ/veritabanı yok): piyasa bağlamı, stop-hedef planı, emir defteri ve likidasyon.

Hepsi tarif ya da hesaptır, tahmin değildir; eşikler bu projenin kendi seçimidir ve canlı veriyle ayarlanmamıştır."""

from __future__ import annotations

from statistics import fmean
from typing import Any

from .analysis import atr
from .coin_read import clean, pct, price_text, signed_pct, tr_num

BOOK_BAND_PCT = 1.0        # fiyatın ±%1'i içindeki derinlik
WALL_RATIO = 4.0           # duvar: bandın ortalama kademesinin bu katından büyük kademe
STOP_ATR = 1.5             # seviye yoksa stop mesafesi (ATR katı)
STOP_PAD_ATR = 0.25        # seviyenin ötesine bırakılan pay
MIN_RR = 1.5


# ---------------------------------------------------------------- piyasa bağlamı
def returns(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]


def correlation(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = fmean(a), fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else None


def market_context(symbol: str, own: dict[str, Any], btc: dict[str, Any] | None, own_1h: Any, btc_1h: Any) -> dict[str, Any] | None:
    """BTC yönü, bu coinin BTC ile bağı ve rejim (trend/yatay). own/btc: coin_read.build çıktıları."""
    if not own or not own.get("timeframes"):
        return None
    reads = {r["tf"]: r for r in own["timeframes"]}
    ref = reads.get("1h") or reads.get("15m")
    regime = None
    if ref:
        regime = "trend" if ref["efficiency"] >= 0.3 else "yatay" if ref["efficiency"] < 0.15 else "karisik"
    out: dict[str, Any] = {"regime": regime, "regime_text": None, "btc": None, "corr": None, "relation": None, "notes": []}
    if regime == "trend":
        out["regime_text"] = "Piyasa trend modunda: kırılımların tutma ihtimali yatay döneme göre daha yüksektir."
    elif regime == "yatay":
        out["regime_text"] = "Piyasa yatay modda: kırılımlar sık sahte çıkar, aralık sınırları daha önemli."
    if symbol.upper() == "BTCUSDT" or not btc or not btc.get("summary"):
        return out if (out["regime_text"]) else None
    bs = btc["summary"]
    btc_lean = bs["lean"]
    out["btc"] = {"lean": btc_lean, "bias": bs["bias"], "text": {"LONG": "BTC yükseliş yönünde", "SHORT": "BTC düşüş yönünde", "NEUTRAL": "BTC'de belirgin yön yok"}[btc_lean]}
    own_ret = returns([r["close"] for r in clean(own_1h)][-72:]) if own_1h else []
    btc_ret = returns([r["close"] for r in clean(btc_1h)][-72:]) if btc_1h else []
    corr = correlation(own_ret, btc_ret)
    out["corr"] = None if corr is None else round(corr, 2)
    lean = own["summary"]["lean"]
    if corr is not None and corr >= 0.6:
        out["relation"] = "bagli"
        out["notes"].append(f"Bu coin BTC'ye güçlü bağlı (korelasyon {tr_num(corr)}): BTC'nin yönü bunu da çeker.")
        if lean != "NEUTRAL" and btc_lean != "NEUTRAL" and lean != btc_lean:
            out["notes"].append(f"Uyarı: coin {'yükselişe' if lean == 'LONG' else 'düşüşe'} yakın ama BTC ters yönde; BTC baskın çıkabilir.")
        elif lean != "NEUTRAL" and lean == btc_lean:
            out["notes"].append("BTC ile aynı yönde: yön teyidi var.")
    elif corr is not None and corr <= 0.3:
        out["relation"] = "bagimsiz"
        out["notes"].append(f"Coin şu an BTC'den bağımsız hareket ediyor (korelasyon {tr_num(corr)}): BTC yönü çok belirleyici olmayabilir.")
    elif corr is not None:
        out["relation"] = "orta"
    return out


# ---------------------------------------------------------------- stop / hedef planı
def trade_plan(lean: str, price: float, levels: dict[str, list[dict[str, Any]]], atr_value: float | None, confidence: str) -> dict[str, Any] | None:
    """Yorumun yönünde bir işlem için düşünülebilecek stop ve hedefler. Emir DEĞİL; kullanıcı kendisi uygular."""
    if lean not in ("LONG", "SHORT") or price <= 0:
        return None
    long = lean == "LONG"
    guard = levels["support"] if long else levels["resistance"]
    goals = levels["resistance"] if long else levels["support"]
    pad = (atr_value or price * 0.004) * STOP_PAD_ATR
    if guard:
        stop = guard[0]["price"] - pad if long else guard[0]["price"] + pad
        stop_basis = "seviye"
    elif atr_value:
        stop = price - atr_value * STOP_ATR if long else price + atr_value * STOP_ATR
        stop_basis = "atr"
    else:
        return None
    risk = (price - stop) if long else (stop - price)
    if risk <= 0 or risk / price > 0.2:
        return None
    targets = []
    for lv in goals[:2]:
        reward = (lv["price"] - price) if long else (price - lv["price"])
        if reward > 0:
            targets.append({"price": lv["price"], "rr": round(reward / risk, 2), "basis": "seviye"})
    if not targets:
        targets = [{"price": price + risk * 2 if long else price - risk * 2, "rr": 2.0, "basis": "2R"}]
    best = targets[0]
    rr = best["rr"]
    if rr >= 2:
        verdict, tone = "Risk/ödül uygun", "good"
    elif rr >= MIN_RR:
        verdict, tone = "Risk/ödül kabul edilebilir", "flat"
    else:
        verdict, tone = "Risk/ödül zayıf: hedef stoptan çok yakın değil", "warn"
    if rr < MIN_RR:
        verdict = f"Risk/ödül zayıf (1:{tr_num(rr)}): beklemek daha mantıklı olabilir"
    return {"side": lean, "entry": price, "stop": stop, "stop_pct": round(pct(stop, price), 2), "stop_basis": stop_basis, "targets": targets,
            "rr": rr, "verdict": verdict, "tone": tone, "confidence": confidence,
            "text": f"{'Alım' if long else 'Satım'} düşünülürse stop {price_text(stop)} ({signed_pct(pct(stop, price))}), ilk hedef {price_text(best['price'])} (1:{tr_num(rr)})."}


def atr_of(rows: Any) -> float | None:
    rows = clean(rows)[:-1]
    if len(rows) < 20:
        return None
    return float(atr([r["high"] for r in rows], [r["low"] for r in rows], [r["close"] for r in rows]))


# ---------------------------------------------------------------- emir defteri ve likidasyon
def _levels(raw: Any) -> list[tuple[float, float]]:
    out = []
    for row in raw if isinstance(raw, list) else []:
        try:
            p, q = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if p > 0 and q > 0:
            out.append((p, q))
    return out


def book_read(depth: Any, price: float) -> dict[str, Any] | None:
    """Emir defteri: ±%1 içinde alış/satış değeri oranı ve büyük duvarlar. depth: {bids:[[fiyat,miktar]], asks:[...]}."""
    if not isinstance(depth, dict) or price <= 0:
        return None
    bids = [(p, q) for p, q in _levels(depth.get("bids")) if p >= price * (1 - BOOK_BAND_PCT / 100)]
    asks = [(p, q) for p, q in _levels(depth.get("asks")) if p <= price * (1 + BOOK_BAND_PCT / 100)]
    if len(bids) < 3 or len(asks) < 3:
        return None
    bid_val, ask_val = sum(p * q for p, q in bids), sum(p * q for p, q in asks)
    total = bid_val + ask_val
    imbalance = (bid_val - ask_val) / total if total > 0 else 0.0
    mean_level = total / (len(bids) + len(asks))

    def walls(side: list[tuple[float, float]]) -> list[dict[str, float]]:
        big = [(p, p * q) for p, q in side if p * q >= mean_level * WALL_RATIO]
        big.sort(key=lambda x: -x[1])
        return [{"price": p, "value": round(v), "dist_pct": round(pct(p, price), 2)} for p, v in big[:2]]

    notes = []
    if imbalance >= 0.2:
        notes.append(f"Alış emirleri ağır basıyor (%{round(imbalance * 100)} fazla): altta destek var.")
    elif imbalance <= -0.2:
        notes.append(f"Satış emirleri ağır basıyor (%{round(-imbalance * 100)} fazla): üstte baskı var.")
    else:
        notes.append("Alış ve satış emirleri dengeli.")
    bw, aw = walls(bids), walls(asks)
    if aw:
        notes.append(f"Üstte büyük satış duvarı {price_text(aw[0]['price'])} ({signed_pct(aw[0]['dist_pct'])}).")
    if bw:
        notes.append(f"Altta büyük alış duvarı {price_text(bw[0]['price'])} ({signed_pct(bw[0]['dist_pct'])}).")
    return {"imbalance": round(imbalance, 2), "bid_value": round(bid_val), "ask_value": round(ask_val), "bid_walls": bw, "ask_walls": aw, "notes": notes,
            "caveat": "Emir defteri anlıktır ve emirler hızla çekilebilir; duvarlar kesin destek/direnç değildir."}


def liquidation_zones(events: list[dict[str, float]], price: float, *, buckets: int = 12, span_pct: float = 3.0) -> dict[str, Any] | None:
    """Son dakikalardaki zorunlu kapanışlar. events: [{price, usd, side}] side: 'SELL' = long tasfiyesi, 'BUY' = short tasfiyesi."""
    if not events or price <= 0:
        return None
    long_usd = sum(e["usd"] for e in events if e["side"] == "SELL")
    short_usd = sum(e["usd"] for e in events if e["side"] == "BUY")
    total = long_usd + short_usd
    if total < 50_000:
        return None
    bucket_w = price * span_pct / 100 * 2 / buckets
    agg: dict[int, float] = {}
    for e in events:
        idx = int((e["price"] - price * (1 - span_pct / 100)) // bucket_w)
        if 0 <= idx < buckets:
            agg[idx] = agg.get(idx, 0.0) + e["usd"]
    top = sorted(agg.items(), key=lambda kv: -kv[1])[:2]
    zones = [{"price": price * (1 - span_pct / 100) + (i + 0.5) * bucket_w, "usd": round(v)} for i, v in top]
    for z in zones:
        z["dist_pct"] = round(pct(z["price"], price), 2)
    lead = "long" if long_usd > short_usd else "short"
    text = f"Son dakikalarda {round(total / 1000)} bin $ zorunlu kapanış: ağırlıkla {lead} pozisyonlar tasfiye oldu."
    return {"long_usd": round(long_usd), "short_usd": round(short_usd), "zones": zones, "text": text}
