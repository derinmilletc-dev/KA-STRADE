"""Coin yorum motoru (saf hesap: ağ, veritabanı, saat yok; testi kolay).

Girdi kapanmış mumlardır (en son, henüz oluşan mum dışarıda bırakılır). Çıktı bir TAHMİN değil, mumların ve piyasa verisinin
tarifidir: her zaman diliminde trend/kırılım/tuzak/RSI/EMA/formasyon, zaman dilimleri arası uyum, destek-direnç, futures
verisi ve aynı kuralın bu coinin geçmişinde ne kadar tuttuğu. Ekran ve pozisyon alarmı aynı motoru kullanır.
"""

from __future__ import annotations

from statistics import fmean, pstdev
from typing import Any

from .analysis import atr, ema

TFS = ("1m", "5m", "15m", "1h", "4h", "1d")
TF_LABEL = {"1m": "1 dk", "5m": "5 dk", "15m": "15 dk", "1h": "1 saat", "4h": "4 saat", "1d": "1 gün"}
TF_WEIGHT = {"1m": 0.5, "5m": 1.0, "15m": 1.5, "1h": 2.0, "4h": 2.0, "1d": 3.0}
MIN_CANDLES = 24
REF_LEN = 20          # kırılım/tuzak için bakılan önceki mum sayısı
TAIL_LEN = 3          # "yeni" sayılan son mumlar
SPIKE_RATIO = 1.8     # son mumların hacmi önceki ortalamanın bu katından büyükse "hacim yüksek"
HISTORY_HORIZON = 12  # geçmiş doğruluk: olaydan kaç mum sonrasına bakılır
HISTORY_MIN_SAMPLES = 8
EVENT_KINDS = ("breakout_up", "breakout_down", "bull_trap", "bear_trap")
EVENT_NAME = {"breakout_up": "yukarı kırılım", "breakout_down": "aşağı kırılım", "bull_trap": "yukarı kırılım tuzağı", "bear_trap": "aşağı kırılım tuzağı"}


# ---------------------------------------------------------------- yardımcılar
def clean(rows: Any) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for row in rows or []:
        try:
            item = {key: float(row[key]) for key in ("open", "high", "low", "close")}
            item["time"] = float(row.get("time", 0))
            item["volume"] = float(row.get("volume", 0) or 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if all(value == value and abs(value) != float("inf") for value in item.values()) and item["close"] > 0 and item["high"] >= item["low"]:
            out.append(item)
    return out


def pct(a: float, b: float) -> float:
    return (a - b) / b * 100 if b > 0 else 0.0


def tr_num(value: float, digits: int = 2) -> str:
    text = f"{abs(value):,.{digits}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def signed_pct(value: float) -> str:
    return f"{'−' if value < 0 else '+'}%{tr_num(value)}"


def price_text(value: float) -> str:
    digits = 6 if value < 1 else 4 if value < 100 else 2
    text = tr_num(value, digits)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(b - a, 0.0) for a, b in zip(values, values[1:])]
    losses = [max(a - b, 0.0) for a, b in zip(values, values[1:])]
    avg_gain, avg_loss = fmean(gains[:period]), fmean(losses[:period])
    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def swings(values: list[float], k: int, high: bool) -> list[int]:
    """Yerel tepe (high=True) ya da dip indeksleri: her iki yanındaki k değerden büyük/küçük."""
    found = []
    for i in range(k, len(values) - k):
        window = values[i - k:i + k + 1]
        if (high and values[i] == max(window) and window.count(values[i]) == 1) or (not high and values[i] == min(window) and window.count(values[i]) == 1):
            found.append(i)
    return found


# ---------------------------------------------------------------- olay (kırılım / tuzak)
def detect_event(rows: list[dict[str, float]]) -> dict[str, Any] | None:
    """Son mumlara göre kırılım ya da tuzak. En az MIN_CANDLES mum gerekir."""
    if len(rows) < MIN_CANDLES:
        return None
    tail = rows[-TAIL_LEN:]
    ref = rows[-(REF_LEN + TAIL_LEN):-TAIL_LEN]
    ref_high, ref_low = max(r["high"] for r in ref), min(r["low"] for r in ref)
    last = rows[-1]["close"]
    up = any(r["high"] > ref_high for r in tail)
    down = any(r["low"] < ref_low for r in tail)
    ref_vol = fmean(r["volume"] for r in ref)
    spike = ref_vol > 0 and fmean(r["volume"] for r in tail) > ref_vol * SPIKE_RATIO
    kind = None
    if up and not down and last < ref_high:
        kind = "bull_trap"
    elif down and not up and last > ref_low:
        kind = "bear_trap"
    elif last > ref_high and not down:
        kind = "breakout_up"
    elif last < ref_low and not up:
        kind = "breakout_down"
    return {"kind": kind, "ref_high": ref_high, "ref_low": ref_low, "spike": spike} if kind else None


# ---------------------------------------------------------------- formasyonlar
def candle_patterns(rows: list[dict[str, float]]) -> list[dict[str, str]]:
    if len(rows) < 8:
        return []
    last, prev = rows[-1], rows[-2]
    rng = last["high"] - last["low"]
    avg = fmean(r["high"] - r["low"] for r in rows[-21:-1]) or rng
    if rng <= 0:
        return []
    body = abs(last["close"] - last["open"])
    upper = last["high"] - max(last["open"], last["close"])
    lower = min(last["open"], last["close"]) - last["low"]
    before = pct(rows[-2]["close"], rows[-7]["close"])
    out: list[dict[str, str]] = []
    pbody = abs(prev["close"] - prev["open"])
    if prev["close"] < prev["open"] and last["close"] > last["open"] and last["open"] <= prev["close"] and last["close"] >= prev["open"] and body > pbody:
        out.append({"name": "Yutan boğa mumu", "bias": "up", "text": "Yeşil mum öncekini tamamen yuttu, alıcılar devraldı."})
    elif prev["close"] > prev["open"] and last["close"] < last["open"] and last["open"] >= prev["close"] and last["close"] <= prev["open"] and body > pbody:
        out.append({"name": "Yutan ayı mumu", "bias": "down", "text": "Kırmızı mum öncekini tamamen yuttu, satıcılar devraldı."})
    if body > 0 and lower >= 2 * body and upper <= 0.6 * body and before < 0:
        out.append({"name": "Çekiç", "bias": "up", "text": "Düşüşün sonunda uzun alt iğne: satış geri alındı."})
    elif body > 0 and upper >= 2 * body and lower <= 0.6 * body and before > 0:
        out.append({"name": "Kayan yıldız", "bias": "down", "text": "Yükselişin sonunda uzun üst iğne: alış geri çevrildi."})
    elif rng >= 0.5 * avg and body <= 0.1 * rng:
        out.append({"name": "Doji", "bias": "flat", "text": "Açılış ve kapanış neredeyse aynı: kararsızlık."})
    if not out and rng >= 1.2 * avg:
        if upper >= 0.6 * rng:
            out.append({"name": "Üstte uzun iğne", "bias": "down", "text": "Fiyat yukarı çıktı ama kabul görmedi, geri döndü."})
        elif lower >= 0.6 * rng:
            out.append({"name": "Altta uzun iğne", "bias": "up", "text": "Fiyat aşağı indi ama kabul görmedi, geri döndü."})
    return out[:2]


# ---------------------------------------------------------------- tek zaman dilimi
def divergence(closes: list[float], rsis: list[float | None]) -> str | None:
    """Fiyat yeni tepe/dip yaparken RSI yapmıyorsa uyumsuzluk. Son tepe/dip yakın olmalı."""
    n = len(closes)
    if n < 40:
        return None
    start = n - 50 if n > 50 else 0
    for high in (True, False):
        idx = [i for i in swings(closes, 2, high) if i >= start and rsis[i] is not None]
        if len(idx) < 2 or idx[-1] < n - 10:
            continue
        a, b = idx[-2], idx[-1]
        if high and closes[b] > closes[a] and rsis[b] < rsis[a] - 2:  # type: ignore[operator]
            return "bearish"
        if not high and closes[b] < closes[a] and rsis[b] > rsis[a] + 2:  # type: ignore[operator]
            return "bullish"
    return None


def volatility(rows: list[dict[str, float]]) -> tuple[int | None, str | None]:
    """Son 14 mumun ortalama gerçek aralığı (% olarak) geçmiş 100 pencereye göre kaçıncı yüzdelikte? 80+ genişliyor, 20- sakin.
    Puanı DEĞİŞTİRMEZ; yalnız bilgi ve kayıt içindir (kalibrasyon bu alanla yapılır)."""
    if len(rows) < 40:
        return None, None
    ranges = [max(r["high"] - r["low"], abs(r["high"] - p["close"]), abs(r["low"] - p["close"])) / r["close"] * 100
              for p, r in zip(rows, rows[1:]) if r["close"] > 0]
    windows = [fmean(ranges[i - 13:i + 1]) for i in range(13, len(ranges))][-100:]
    if len(windows) < 20:
        return None, None
    rank = round(sum(1 for w in windows if w <= windows[-1]) / len(windows) * 100)
    return rank, "expanding" if rank >= 80 else "quiet" if rank <= 20 else "normal"


def read_timeframe(raw: Any, tf: str) -> dict[str, Any] | None:
    rows = clean(raw)
    if len(rows) < MIN_CANDLES:
        return None
    window = rows[-60:]
    closes = [r["close"] for r in rows]
    last = window[-1]
    first = window[0]
    net = pct(last["close"], first["close"])
    path = sum(abs(b["close"] - a["close"]) for a, b in zip(window, window[1:]))
    efficiency = abs(last["close"] - first["close"]) / path if path > 0 else 0.0
    noise = fmean((r["high"] - r["low"]) / r["close"] * 100 for r in window)
    recent = pct(last["close"], window[-6]["close"])
    lo, hi = min(r["low"] for r in window), max(r["high"] for r in window)
    place = (last["close"] - lo) / (hi - lo) if hi > lo else 0.5
    trending = efficiency >= 0.3 and abs(net) >= noise * 1.5
    trend = ("up" if net > 0 else "down") if trending else "flat"
    pullback = None
    if trending and ((net > 0 and recent <= -noise) or (net < 0 and recent >= noise)):
        pullback = "up" if net > 0 else "down"
    event = detect_event(window)

    rsis = rsi_series(closes)
    rsi_now = rsis[-1] if rsis and rsis[-1] is not None else 50.0
    div = divergence(closes, rsis)
    ema_state, ema_fast, ema_slow = "mixed", None, None
    if len(closes) >= 50:
        fast, slow = ema(closes, 20)[-1], ema(closes, 50)[-1]
        ema_fast, ema_slow = fast, slow
        ema_state = "up" if closes[-1] > fast > slow else "down" if closes[-1] < fast < slow else "mixed"
    squeeze = False
    if len(closes) >= 60:
        widths = []
        for i in range(19, len(closes)):
            seg = closes[i - 19:i + 1]
            mean = fmean(seg)
            widths.append(pstdev(seg) * 4 / mean if mean else 0.0)
        recent_w = widths[-100:]
        squeeze = len(recent_w) >= 40 and recent_w[-1] <= sorted(recent_w)[max(0, int(len(recent_w) * 0.2) - 1)]
    patterns = candle_patterns(rows)
    vol_pct, vol_state = volatility(rows)

    score = 0.0
    score += {"up": 1.0, "down": -1.0, "flat": 0.0}[trend]
    score += {"up": 0.5, "down": -0.5, "mixed": 0.0}[ema_state]
    if event:
        score += {"breakout_up": 1.0, "breakout_down": -1.0, "bull_trap": -1.0, "bear_trap": 1.0}[event["kind"]]
    if div:
        score += 0.5 if div == "bullish" else -0.5
    score = max(-2.0, min(2.0, score))
    tone = "warn" if event and event["kind"] in ("bull_trap", "bear_trap") else "up" if score >= 0.75 else "down" if score <= -0.75 else "flat"

    head, text = describe(event, trend, pullback, net, recent, place, efficiency, tf)
    return {
        "tf": tf, "label": TF_LABEL.get(tf, tf), "trend": trend, "net_pct": round(net, 3), "efficiency": round(efficiency, 3),
        "noise_pct": round(noise, 3), "event": event["kind"] if event else None, "volume_spike": bool(event and event["spike"]),
        "pullback": pullback, "rsi": round(rsi_now, 1), "rsi_state": "overbought" if rsi_now >= 70 else "oversold" if rsi_now <= 30 else "normal",
        "divergence": div, "ema": ema_state, "squeeze": squeeze, "patterns": patterns, "place": round(place, 3),
        "regime": "trend" if trending else "range", "vol_pct": vol_pct, "vol_state": vol_state,
        "score": round(score, 2), "tone": tone, "head": head, "text": text,
        "price": last["close"], "ref_high": event["ref_high"] if event else None, "ref_low": event["ref_low"] if event else None,
    }


def describe(event: dict[str, Any] | None, trend: str, pullback: str | None, net: float, recent: float, place: float, efficiency: float, tf: str) -> tuple[str, str]:
    span = {"1m": "son 1 saat", "5m": "son 5 saat", "15m": "son 15 saat", "1h": "son 60 saat", "4h": "son 10 gün", "1d": "son 60 gün"}.get(tf, "son 60 mum")
    spike = bool(event and event["spike"])
    kind = event["kind"] if event else None
    if kind == "bull_trap":
        return "Yukarı kırılım tuzağı olabilir", f"Fiyat son {REF_LEN} mumun tepesini aştı ama tutunamadı, geri döndü (bull trap). Acele alım riskli.{' Hacim de yüksek, hareket sert.' if spike else ''}"
    if kind == "bear_trap":
        return "Aşağı kırılım tuzağı olabilir", f"Fiyat son {REF_LEN} mumun dibinin altına indi ama toparlandı (bear trap). Acele satış riskli.{' Hacim de yüksek, hareket sert.' if spike else ''}"
    if kind == "breakout_up":
        return "Yukarı kırılım", f"Fiyat son {REF_LEN} mumun tepesinin üstünde kapanıyor." + (" Hacim de destekliyor, kırılım daha güvenilir." if spike else " Hacim güçlü değil, tutması için sıradaki mumlara bak.")
    if kind == "breakout_down":
        return "Aşağı kırılım", f"Fiyat son {REF_LEN} mumun dibinin altında kapanıyor." + (" Hacim de destekliyor, düşüş daha güvenilir." if spike else " Hacim güçlü değil, tutması için sıradaki mumlara bak.")
    if pullback == "up":
        return "Yükselişte geri çekilme", f"{span.capitalize()} yukarı gidiyor ama son mumlarda {signed_pct(recent)} geri çekilme var. Ana yön hâlâ yukarı."
    if pullback == "down":
        return "Düşüşte tepki yükselişi", f"{span.capitalize()} aşağı gidiyor ama son mumlarda {signed_pct(recent)} tepki yükselişi var. Ana yön hâlâ aşağı."
    if trend == "up":
        return "Düzenli yükseliş", f"{span.capitalize()} içinde fiyat {signed_pct(net)} yükseldi, geri çekilmeler sığ kaldı."
    if trend == "down":
        return "Düzenli düşüş", f"{span.capitalize()} içinde fiyat {signed_pct(net)} düştü, toparlanmalar zayıf kaldı."
    where = "Aralığın tepesinde, yukarıda direnç aranabilir." if place >= 0.8 else "Aralığın dibinde, aşağıda destek aranabilir." if place <= 0.2 else "Aralığın ortasında, net bir avantaj yok."
    return ("Yatay ve dalgalı" if efficiency < 0.15 else "Belirgin yön yok"), f"{span.capitalize()} içinde fiyat bir aralıkta gidip geliyor ({signed_pct(net)}). {where}"


# ---------------------------------------------------------------- destek / direnç
def key_levels(by_tf: dict[str, list[dict[str, float]]], price: float) -> dict[str, list[dict[str, Any]]]:
    """15 dk, 1 saat ve 4 saatlik yerel tepe/diplerden kümelenmiş seviyeler; fiyatın altı destek, üstü direnç."""
    weights = {"15m": 1.0, "1h": 2.0, "4h": 3.0}
    points: list[tuple[float, float]] = []
    for tf, weight in weights.items():
        rows = clean(by_tf.get(tf))
        if len(rows) < 30:
            continue
        rows = rows[-200:]
        highs, lows = [r["high"] for r in rows], [r["low"] for r in rows]
        points += [(highs[i], weight) for i in swings(highs, 3, True)]
        points += [(lows[i], weight) for i in swings(lows, 3, False)]
    if not points or price <= 0:
        return {"support": [], "resistance": []}
    one_hour = clean(by_tf.get("1h")) or clean(by_tf.get("15m"))
    tol = 0.0015
    if len(one_hour) >= 20:
        tol = max(0.0015, atr([r["high"] for r in one_hour], [r["low"] for r in one_hour], [r["close"] for r in one_hour]) / price * 0.35)
    points.sort()
    clusters: list[list[tuple[float, float]]] = []
    for point in points:
        if clusters and (point[0] - clusters[-1][-1][0]) / clusters[-1][-1][0] <= tol:
            clusters[-1].append(point)
        else:
            clusters.append([point])
    levels = []
    for cluster in clusters:
        weight = sum(w for _, w in cluster)
        levels.append({"price": sum(p * w for p, w in cluster) / weight, "strength": round(weight, 1), "touches": len(cluster)})
    strong = [lv for lv in levels if lv["strength"] >= 2.0] or levels
    below = sorted((lv for lv in strong if lv["price"] < price), key=lambda lv: price - lv["price"])[:2]
    above = sorted((lv for lv in strong if lv["price"] > price), key=lambda lv: lv["price"] - price)[:2]
    fix = lambda lv: {**lv, "dist_pct": round(pct(lv["price"], price), 2)}  # noqa: E731
    return {"support": [fix(lv) for lv in below], "resistance": [fix(lv) for lv in above]}


# ---------------------------------------------------------------- futures verisi
def read_futures(funding: float | None, oi_change_pct: float | None, price_change_pct: float | None, taker_ratio: float | None) -> dict[str, Any] | None:
    """Fonlama oranı (kesir, 8 saatlik), açık pozisyon değişimi (%), aynı sürede fiyat değişimi (%), alıcı/satıcı hacim oranı."""
    if funding is None and oi_change_pct is None and taker_ratio is None:
        return None
    notes: list[dict[str, str]] = []
    bias = 0.0
    if funding is not None:
        value = funding * 100
        if value >= 0.05:
            notes.append({"tone": "warn", "text": f"Fonlama yüksek ({signed_pct(value)}): long pozisyonlar kalabalık, ters düşüş riski artar."})
            bias -= 0.3
        elif value <= -0.03:
            notes.append({"tone": "warn", "text": f"Fonlama negatif ({signed_pct(value)}): short pozisyonlar kalabalık, sert toparlanma olabilir."})
            bias += 0.3
        else:
            notes.append({"tone": "flat", "text": f"Fonlama normal ({signed_pct(value)})."})
    if oi_change_pct is not None and price_change_pct is not None and abs(oi_change_pct) >= 1 and abs(price_change_pct) >= 0.3:
        up_price, up_oi = price_change_pct > 0, oi_change_pct > 0
        if up_price and up_oi:
            notes.append({"tone": "up", "text": "Fiyat ve açık pozisyon birlikte artıyor: yeni para giriyor, yükseliş güçlü."}); bias += 0.5
        elif up_price:
            notes.append({"tone": "warn", "text": "Fiyat artıyor ama açık pozisyon azalıyor: yükseliş short kapanışlarından, zayıf olabilir."}); bias += 0.1
        elif up_oi:
            notes.append({"tone": "down", "text": "Fiyat düşüyor, açık pozisyon artıyor: satış yeni pozisyonlarla güçlü."}); bias -= 0.5
        else:
            notes.append({"tone": "warn", "text": "Fiyat ve açık pozisyon birlikte düşüyor: düşüş pozisyon kapanışından, zayıflıyor olabilir."}); bias -= 0.1
    if taker_ratio is not None:
        if taker_ratio >= 1.15:
            notes.append({"tone": "up", "text": f"Piyasa alımları baskın (alıcı/satıcı {tr_num(taker_ratio)})."}); bias += 0.3
        elif taker_ratio <= 0.87:
            notes.append({"tone": "down", "text": f"Piyasa satımları baskın (alıcı/satıcı {tr_num(taker_ratio)})."}); bias -= 0.3
    return {
        "funding_pct": None if funding is None else round(funding * 100, 4), "oi_change_pct": None if oi_change_pct is None else round(oi_change_pct, 2),
        "taker_ratio": None if taker_ratio is None else round(taker_ratio, 2), "notes": notes, "bias": round(max(-1.0, min(1.0, bias)), 2),
    }


# ---------------------------------------------------------------- geçmiş doğruluk
def backtest(raw: Any, horizon: int = HISTORY_HORIZON) -> dict[str, Any]:
    """Aynı olay kuralını bu coinin geçmiş mumlarında dener. Kırılım: horizon mum sonra seviyenin doğru tarafında mı?
    Tuzak: horizon mum sonra fiyat tuzağın tersine (yukarı tuzak → aşağıda, aşağı tuzak → yukarıda) mi?"""
    rows = clean(raw)
    stats = {kind: {"n": 0, "hits": 0} for kind in EVENT_KINDS}
    last_seen = {kind: -10 ** 9 for kind in EVENT_KINDS}
    for i in range(MIN_CANDLES + REF_LEN, len(rows) - horizon):
        event = detect_event(rows[max(0, i - 59):i + 1])
        if not event or i - last_seen[event["kind"]] < 5:
            continue
        kind = event["kind"]
        last_seen[kind] = i
        later, now = rows[i + horizon]["close"], rows[i]["close"]
        hit = (later > event["ref_high"] if kind == "breakout_up" else later < event["ref_low"] if kind == "breakout_down"
               else later < now if kind == "bull_trap" else later > now)
        stats[kind]["n"] += 1
        stats[kind]["hits"] += 1 if hit else 0
    return {"candles": len(rows), "horizon": horizon, "kinds": stats}


def history_text(history: dict[str, Any] | None, kind: str | None, label: str) -> str | None:
    if not history or not kind:
        return None
    row = history["kinds"].get(kind)
    if not row or row["n"] < HISTORY_MIN_SAMPLES:
        return f"Bu coinde {label} grafiğinde bu olay için yeterli geçmiş örnek yok."
    rate = round(row["hits"] / row["n"] * 100)
    meaning = {"breakout_up": "yukarıda kaldı", "breakout_down": "aşağıda kaldı", "bull_trap": "gerçekten geri düştü", "bear_trap": "gerçekten toparlandı"}[kind]
    return f"Bu coinin son {history['candles']} {label} mumunda {EVENT_NAME[kind]} {row['n']} kez görüldü; {history['horizon']} mum sonra %{rate} oranında {meaning}."


# ---------------------------------------------------------------- toplam okuma
def summarize(reads: dict[str, dict[str, Any]], levels: dict[str, list[dict[str, Any]]], futures: dict[str, Any] | None, price: float) -> dict[str, Any]:
    present = [tf for tf in TFS if tf in reads]
    if not present:
        return {"bias": 0.0, "lean": "NEUTRAL", "confidence": "Düşük", "title": "Veri yetersiz", "text": "Yorum için yeterli mum verisi yok.", "flags": [], "alignment": {"up": 0, "down": 0, "flat": 0, "total": 0}, "conflict": None}
    total_w = sum(TF_WEIGHT[tf] for tf in present)
    bias = sum(TF_WEIGHT[tf] * reads[tf]["score"] for tf in present) / total_w
    if futures:
        bias += futures["bias"] * 0.3
    up = sum(1 for tf in present if reads[tf]["score"] >= 0.75)
    down = sum(1 for tf in present if reads[tf]["score"] <= -0.75)
    flat = len(present) - up - down
    short = [reads[tf]["score"] for tf in present if tf in ("1m", "5m", "15m")]
    long = [reads[tf]["score"] for tf in present if tf in ("1h", "4h", "1d")]
    conflict = None
    if short and long:
        s, l = fmean(short), fmean(long)
        if s >= 0.5 and l <= -0.5:
            conflict = "Kısa vade yukarı, uzun vade aşağı: ters yönlü, yükseliş kalıcı olmayabilir."
        elif s <= -0.5 and l >= 0.5:
            conflict = "Kısa vade aşağı, uzun vade yukarı: ters yönlü, düşüş geçici olabilir."
    lean = "LONG" if bias >= 0.5 else "SHORT" if bias <= -0.5 else "NEUTRAL"
    agree = up if lean == "LONG" else down if lean == "SHORT" else 0
    confidence = "Yüksek" if lean != "NEUTRAL" and abs(bias) >= 1.0 and agree >= max(3, len(present) * 0.6) and not conflict else "Orta" if lean != "NEUTRAL" and not conflict else "Düşük"
    title = {"LONG": "Yükseliş yönü ağır basıyor", "SHORT": "Düşüş yönü ağır basıyor", "NEUTRAL": "Belirgin yön yok"}[lean]
    flags: list[dict[str, str]] = []
    for tf in present:
        r = reads[tf]
        if tf in ("15m", "1h", "4h", "1d") and r["event"] in ("bull_trap", "bear_trap"):
            flags.append({"tone": "warn", "text": f"{r['label']} grafikte {EVENT_NAME[r['event']]} olabilir."})
        if tf in ("15m", "1h", "4h") and r["divergence"]:
            flags.append({"tone": "warn", "text": f"{r['label']} grafikte RSI uyumsuzluğu: {'fiyat yeni tepe yaptı ama RSI yapmadı, yükseliş yorulabilir' if r['divergence'] == 'bearish' else 'fiyat yeni dip yaptı ama RSI yapmadı, düşüş yorulabilir'}."})
        if tf in ("15m", "1h", "4h") and r["squeeze"]:
            flags.append({"tone": "flat", "text": f"{r['label']} grafikte fiyat sıkışmış: sert bir hareket yakın olabilir, yönü belli değil."})
    if futures:
        flags += [n for n in futures["notes"] if n["tone"] == "warn"]
    # Rejim ve volatilite: puana girmez, yalnız bağlam (yanlış alarmı azaltmak için not düşer).
    regime_tfs = [tf for tf in ("15m", "1h", "4h", "1d") if tf in reads]
    trend_w = sum(TF_WEIGHT[tf] for tf in regime_tfs if reads[tf].get("regime") == "trend")
    range_w = sum(TF_WEIGHT[tf] for tf in regime_tfs if reads[tf].get("regime") == "range")
    regime = None if not regime_tfs else "trend" if trend_w > range_w else "range"
    expanding = [tf for tf in ("15m", "1h", "4h") if tf in reads and reads[tf].get("vol_state") == "expanding"]
    quiet = [tf for tf in ("15m", "1h", "4h") if tf in reads and reads[tf].get("vol_state") == "quiet"]
    context: list[dict[str, str]] = []
    for tf in ("1h", "4h"):
        r = reads.get(tf)
        if r and r.get("regime") == "trend" and not r["divergence"] and ((r["trend"] == "up" and r["rsi_state"] == "overbought") or (r["trend"] == "down" and r["rsi_state"] == "oversold")):
            context.append({"tone": "flat", "text": f"{r['label']} grafikte RSI {'aşırı alımda' if r['trend'] == 'up' else 'aşırı satımda'} ama trend güçlü: tek başına dönüş sinyali sayılmaz."})
            break
    if len(expanding) >= 2:
        context.append({"tone": "warn", "text": "Oynaklık genişliyor: stop mesafesi ve kaldıraç buna göre gözden geçirilmeli, ani hareket normalden büyük olabilir."})
    elif len(quiet) >= 2:
        context.append({"tone": "flat", "text": "Oynaklık çok düşük: sıkışma sonrası sert hareket gelebilir, yönü belli değil."})
    near = [(lv, "direnç") for lv in levels["resistance"][:1]] + [(lv, "destek") for lv in levels["support"][:1]]
    for lv, name in near:
        if abs(lv["dist_pct"]) <= 0.4:
            flags.append({"tone": "warn", "text": f"Fiyat {name} bölgesine çok yakın ({price_text(lv['price'])}, {signed_pct(lv['dist_pct'])})."})
    sentences = [conflict] if conflict else [f"{up} zaman diliminde yükseliş, {down} zaman diliminde düşüş ağır basıyor, {flat} tanesi nötr."]
    if levels["resistance"]:
        r0 = levels["resistance"][0]; sentences.append(f"En yakın direnç {price_text(r0['price'])} ({signed_pct(r0['dist_pct'])}).")
    if levels["support"]:
        s0 = levels["support"][0]; sentences.append(f"En yakın destek {price_text(s0['price'])} ({signed_pct(s0['dist_pct'])}).")
    return {"bias": round(bias, 2), "lean": lean, "confidence": confidence, "title": title, "text": " ".join(sentences[:3]), "flags": (flags + context)[:5],
            "alignment": {"up": up, "down": down, "flat": flat, "total": len(present)}, "conflict": conflict,
            "regime": regime, "volatility": "expanding" if len(expanding) >= 2 else "quiet" if len(quiet) >= 2 else "normal" if any(reads[tf].get("vol_state") for tf in present) else None}


# ---------------------------------------------------------------- pozisyon ilişkisi
def position_fit(summary: dict[str, Any], levels: dict[str, list[dict[str, Any]]], price: float, side: str, entry: float | None) -> dict[str, Any]:
    side = side.upper()
    bias = summary["bias"]
    mine = 1 if side == "LONG" else -1
    aligned = bias * mine
    fit = "uyumlu" if aligned >= 0.5 else "ters" if aligned <= -0.5 else "kararsiz"
    text = {"uyumlu": f"Yorum {'yükseliş' if mine > 0 else 'düşüş'} yönünde, pozisyonunla uyumlu.",
            "ters": f"Yorum {'düşüş' if mine > 0 else 'yükseliş'} yönünde, pozisyonuna ters. Dikkatli ol.",
            "kararsiz": "Yorum net bir yön vermiyor, pozisyonun için ne destek ne tehdit."}[fit]
    out: dict[str, Any] = {"side": side, "fit": fit, "text": text, "pnl_pct": None, "guard": None, "target": None}
    if entry and entry > 0:
        out["pnl_pct"] = round(pct(price, entry) * mine, 2)
    guard = (levels["support"] if mine > 0 else levels["resistance"])
    target = (levels["resistance"] if mine > 0 else levels["support"])
    if guard:
        out["guard"] = {"price": guard[0]["price"], "dist_pct": guard[0]["dist_pct"], "label": "Altındaki destek (stop için düşünülebilir)" if mine > 0 else "Üstündeki direnç (stop için düşünülebilir)"}
    if target:
        out["target"] = {"price": target[0]["price"], "dist_pct": target[0]["dist_pct"], "label": "Üstündeki direnç (kâr alma bölgesi)" if mine > 0 else "Altındaki destek (kâr alma bölgesi)"}
    return out


# ---------------------------------------------------------------- pozisyon alarmı olayları
def position_events(reads: dict[str, dict[str, Any]], summary: dict[str, Any], side: str, *, min_tf: str = "15m", mode: str = "against") -> dict[str, dict[str, str]]:
    """Pozisyon yönüne göre şu an AKTİF olaylar: {anahtar: {tone, text}}. Aynı anahtar tekrar bildirilmez (servis tarafında)."""
    side = side.upper()
    long = side == "LONG"
    allowed = TFS[TFS.index(min_tf):] if min_tf in TFS else TFS[2:]
    out: dict[str, dict[str, str]] = {}
    against_kinds = ("breakout_down", "bull_trap") if long else ("breakout_up", "bear_trap")
    for_kinds = ("breakout_up", "bear_trap") if long else ("breakout_down", "bull_trap")
    for tf in allowed:
        r = reads.get(tf)
        if not r:
            continue
        label = r["label"]
        if r["event"] in against_kinds:
            out[f"{tf}:{r['event']}"] = {"tone": "danger", "text": f"{label} grafikte {EVENT_NAME[r['event']]}: pozisyonuna ters."}
        elif mode == "all" and r["event"] in for_kinds:
            out[f"{tf}:{r['event']}"] = {"tone": "good", "text": f"{label} grafikte {EVENT_NAME[r['event']]}: pozisyonunun lehine."}
        if r["divergence"] == ("bearish" if long else "bullish"):
            out[f"{tf}:div"] = {"tone": "warn", "text": f"{label} grafikte RSI uyumsuzluğu: {'yükseliş' if long else 'düşüş'} yorulabilir."}
    bias = summary["bias"] * (1 if long else -1)
    if bias <= -0.5:
        out["mtf:against"] = {"tone": "danger", "text": f"Zaman dilimlerinin çoğu artık {'düşüşü' if long else 'yükselişi'} gösteriyor: pozisyonuna ters."}
    elif mode == "all" and bias >= 1.0:
        out["mtf:for"] = {"tone": "good", "text": "Zaman dilimlerinin çoğu pozisyonunla aynı yönde."}
    return out


def build(candles: dict[str, Any], futures_raw: dict[str, Any] | None = None, *, history: dict[str, Any] | None = None, history_tf: str | None = None) -> dict[str, Any]:
    """Tam okuma. candles: {tf: ham mum listesi (son mum henüz oluşan mum olabilir)}. futures_raw: read_futures argümanları."""
    closed = {tf: clean(rows)[:-1] for tf, rows in candles.items() if rows}
    reads = {tf: r for tf in TFS if tf in closed and (r := read_timeframe(closed[tf], tf))}
    live = None
    for tf in TFS:
        rows = clean(candles.get(tf))
        if rows:
            live = rows[-1]["close"]
            break
    price = live if live else 0.0
    levels = key_levels(closed, price)
    futures = read_futures(**futures_raw) if futures_raw else None
    summary = summarize(reads, levels, futures, price)
    result = {"price": price, "timeframes": [reads[tf] for tf in TFS if tf in reads], "levels": levels, "futures": futures, "summary": summary, "history": None}
    if history and history_tf and history_tf in reads:
        ev = reads[history_tf]["event"]
        result["history"] = {"tf": history_tf, "kinds": history["kinds"], "candles": history["candles"], "horizon": history["horizon"],
                             "text": history_text(history, ev, TF_LABEL.get(history_tf, history_tf)), "event": ev}
    return result


def pulse_item(symbol: str, side: str, data: dict[str, Any]) -> dict[str, Any]:
    """Açık pozisyon kartı için kısa nabız: yorumun yönü, pozisyonla uyumu ve pozisyona ters aktif olaylar. Saf hesap."""
    side = side.upper()
    reads = {r["tf"]: r for r in data.get("timeframes", [])}
    summary = data["summary"]
    fit = position_fit(summary, data["levels"], data["price"], side, None)
    events = position_events(reads, summary, side, min_tf="15m", mode="against")
    danger = [e["text"] for e in events.values() if e["tone"] in ("danger", "warn")]
    return {"symbol": symbol, "side": side, "lean": summary["lean"], "confidence": summary["confidence"], "fit": fit["fit"],
            "conflict": bool(summary.get("conflict")), "warnings": danger[:3], "price": data["price"]}
