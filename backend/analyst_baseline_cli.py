"""Analist motoru – eski sistemin (analyze) başlangıç performans ölçümü.

Yalnız araştırma aracıdır: canlı koda, API'ye ya da emir sistemine dokunmaz; `app.analysis` dosyasını yalnız okur.
Veri: data.binance.vision statik USDⓈ-M arşivleri (kimlik bilgisi yok, işlem API'si yok), repo dışındaki bir klasöre.

Kurallar (onaylı plan):
- Karar anı = 15m mumun kapanışı. Hesaba yalnız o ana kadar KAPANMIŞ mumlar girer.
- Giriş = bir sonraki 15m mumun açılışı. Stop = analyze() stop seviyesi, R = |giriş − stop|, hedef = giriş ± 1R.
- En fazla 24 saat (96 mum). Aynı mumda stop ve hedef → kayıp. Süre dolarsa ZAMAN AŞIMI (ayrı raporlanır).
- Komisyon + slippage her iki tarafta düşülür (R cinsinden).
- Aynı coinde açık sinyal sonuçlanmadan yeni sinyal sayılmaz.

Aşamalar:
  stage1 = 5 coin × 3 ay (kontroller + performans)    stage2 = 30 coin × 24 ay (eğitim / doğrulama / test)

Örnek:
  python analyst_baseline_cli.py download --stage stage1 --data ~/kais-data/analyst
  python analyst_baseline_cli.py run      --stage stage1 --data ~/kais-data/analyst --out ~/kais-data/analyst-report-stage1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import analysis  # noqa: E402  (yalnız okunur)
from app.backtest_data import normalize_candles, read_rows  # noqa: E402

ANALYSIS_PATH = Path(__file__).resolve().parent / "app" / "analysis.py"
M15 = 900
WINDOW = 500                 # analist evreniyle aynı: 500 mum
MIN_CLOSED = 400             # onaylı plan: tercihen ≥ 400 kapanmış mum
HORIZON = 96                 # 24 saat
FEE_BPS = 5.0                # taker, taraf başı
SLIP_BPS = 3.0               # taraf başı
MIN_SIGNALS = 200
MIN_DAYS = 30
BANDS = ((35, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 100))

STAGE1_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
STAGE2_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
    "DOTUSDT", "TRXUSDT", "BCHUSDT", "NEARUSDT", "SUIUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "FILUSDT", "ATOMUSDT",
    "ETCUSDT", "UNIUSDT", "AAVEUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT", "WLDUSDT", "1000PEPEUSDT", "ENAUSDT", "HBARUSDT",
)
# Isınma ayı puanlanmaz (500 mumluk pencere + EMA200 ısınması için).
STAGES = {
    "stage1": {"symbols": STAGE1_SYMBOLS, "warmup": "2026-07", "start": "2026-08", "end": "2026-10",
               "periods": {"tum": ("2026-08", "2026-10")}},
    "stage2": {"symbols": STAGE2_SYMBOLS, "warmup": "2024-10", "start": "2024-11", "end": "2026-10",
               "periods": {"egitim": ("2024-11", "2025-10"), "dogrulama": ("2025-11", "2026-04"), "test": ("2026-05", "2026-10")}},
}


# ---------------------------------------------------------------- yardımcılar
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def month_list(first: str, last: str) -> list[str]:
    year, month = map(int, first.split("-"))
    end = tuple(map(int, last.split("-")))
    out = []
    while (year, month) <= end:
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def month_start(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m").replace(tzinfo=timezone.utc).timestamp())


def month_end(value: str) -> int:
    year, month = map(int, value.split("-"))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return month_start(f"{year:04d}-{month:02d}")


def wilson(wins: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def band_of(confidence: float) -> str | None:
    for low, high in BANDS:
        if low <= confidence <= high:
            return f"{low}-{high}"
    return None


def max_drawdown(rs: list[float]) -> float:
    peak = equity = drawdown = 0.0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


# ---------------------------------------------------------------- veri
def archive_path(root: Path, symbol: str, interval: str, month: str) -> Path:
    return root / "klines" / symbol / interval / f"{symbol}-{interval}-{month}.zip"


def download_stage(stage: str, root: Path, workers: int) -> dict[str, Any]:
    import backtest_download as dl  # statik arşiv indirici; checksum doğrular, yalnız data.binance.vision
    spec = STAGES[stage]
    months = month_list(spec["warmup"], spec["end"])
    jobs = [(root, symbol, "klines", "15m", month) for symbol in spec["symbols"] for month in months]
    jobs += [(root, "BTCUSDT", "klines", "4h", month) for month in months]  # piyasa koşulu etiketi
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda job: dl.download(*job), jobs))
    missing = [row["path"] for row in results if row["status"] != "OK"]
    summary = {"stage": stage, "archives": len(results), "missing": missing}
    root.mkdir(parents=True, exist_ok=True)
    (root / f"download-{stage}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_series(root: Path, symbol: str, interval: str, months: list[str]) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    missing = []
    for month in months:
        path = archive_path(root, symbol, interval, month)
        if not path.exists():
            missing.append(month)
            continue
        rows.extend(read_rows(path))
    if not rows:
        return [], missing
    series, _duplicates = normalize_candles(rows, interval)
    return series.rows, missing


def gaps(rows: list[dict], step: int) -> int:
    return sum(1 for left, right in zip(rows, rows[1:]) if right["time"] - left["time"] != step)


# ---------------------------------------------------------------- piyasa koşulu (BTC 4h, yalnız kapanmış mum)
def regime_lookup(btc4h: list[dict]):
    import bisect
    times = [row["time"] for row in btc4h]
    closes = [row["close"] for row in btc4h]
    e50, e200 = analysis.ema(closes, 50), analysis.ema(closes, 200)
    atr_pct = []
    for index in range(len(btc4h)):
        lo = max(1, index - 13)
        ranges = [max(btc4h[k]["high"] - btc4h[k]["low"], abs(btc4h[k]["high"] - closes[k - 1]), abs(btc4h[k]["low"] - closes[k - 1]))
                  for k in range(lo, index + 1)] or [0.0]
        atr_pct.append(sum(ranges) / len(ranges) / closes[index])

    def at(decision_time: int) -> str:
        # 4h mumu decision_time'dan önce kapanmış olmalı: açılış + 14400 <= decision_time
        index = bisect.bisect_right(times, decision_time - 14400) - 1
        if index < 200:
            return "bilinmiyor"
        gap = (e50[index] - e200[index]) / e200[index]
        trend = "yukselis" if gap > 0.01 else "dusus" if gap < -0.01 else "yatay"
        history = sorted(atr_pct[max(0, index - 180):index + 1])
        vol = "yuksek_vol" if atr_pct[index] >= history[len(history) // 2] else "dusuk_vol"
        return f"{trend}/{vol}"
    return at


# ---------------------------------------------------------------- sonuç simülasyonu
def simulate(direction: str, entry: float, stop: float, bars: list[dict], fee_bps: float, slip_bps: float) -> dict[str, Any] | None:
    """1R hedef / stop / 24 saat. bars = girişten itibaren mumlar (ilk mum giriş mumudur)."""
    sign = 1 if direction == "LONG" else -1
    risk = (entry - stop) * sign
    if not risk > 0 or not bars:
        return None
    target = entry + sign * risk
    rate = (fee_bps + slip_bps) / 10_000
    for offset, bar in enumerate(bars[:HORIZON]):
        adverse = bar["low"] if sign == 1 else bar["high"]
        favorable = bar["high"] if sign == 1 else bar["low"]
        hit_stop = (adverse - stop) * sign <= 0
        hit_target = (favorable - target) * sign >= 0
        if hit_stop or hit_target:
            exit_price, outcome = (stop, "KAYIP") if hit_stop else (target, "KAZANC")  # ikisi birden → kayıp
            gross = (exit_price - entry) * sign / risk
            cost = (entry + exit_price) * rate / risk
            return {"outcome": outcome, "r": gross - cost, "bars": offset + 1, "exit_time": bar["time"] + M15}
    if len(bars) < HORIZON:
        return None  # veri bitti: sonuç bilinmiyor, sayılmaz
    last = bars[HORIZON - 1]
    gross = (last["close"] - entry) * sign / risk
    cost = (entry + last["close"]) * rate / risk
    return {"outcome": "ZAMAN_ASIMI", "r": gross - cost, "bars": HORIZON, "exit_time": last["time"] + M15}


# ---------------------------------------------------------------- tek coin
def closed_window(rows: list[dict], index: int) -> list[dict]:
    """index numaralı mumun kapanışındaki karar için pencere: yalnız 0..index (hepsi kapanmış)."""
    return rows[max(0, index - WINDOW + 1):index + 1]


def run_symbol(args: tuple[str, str, str, float, float]) -> dict[str, Any]:
    symbol, data_root, stage, fee_bps, slip_bps = args
    spec = STAGES[stage]
    months = month_list(spec["warmup"], spec["end"])
    rows, missing = load_series(Path(data_root), symbol, "15m", months)
    btc4h, _ = load_series(Path(data_root), "BTCUSDT", "4h", months)
    regime = regime_lookup(btc4h) if btc4h else (lambda _t: "bilinmiyor")
    scored_from, scored_to = month_start(spec["start"]), month_end(spec["end"])
    signals: list[dict[str, Any]] = []
    skipped = {"az_veri": 0, "bekle": 0, "gecersiz_stop": 0, "sonuc_yok": 0, "bosluk": 0}
    timings: list[float] = []
    busy_until = -1
    index = 0
    while index < len(rows) - 1:
        decision_time = rows[index]["time"] + M15
        if decision_time < scored_from or decision_time >= scored_to:
            index += 1
            continue
        if decision_time < busy_until:
            index += 1
            continue
        window = closed_window(rows, index)
        if len(window) < MIN_CLOSED:
            skipped["az_veri"] += 1
            index += 1
            continue
        if window[-1]["time"] + M15 > decision_time:
            raise AssertionError("açık mum hesaba girdi")  # yapısal güvence
        if any(right["time"] - left["time"] != M15 for left, right in zip(window[-260:], window[-259:])):
            skipped["bosluk"] += 1
            index += 1
            continue
        started = time.perf_counter()
        result = analysis.analyze(window)
        timings.append(time.perf_counter() - started)
        direction = result["direction"]
        if direction not in {"LONG", "SHORT"}:
            skipped["bekle"] += 1
            index += 1
            continue
        entry_bar = rows[index + 1]
        if entry_bar["time"] != decision_time:
            skipped["bosluk"] += 1
            index += 1
            continue
        entry = entry_bar["open"]
        outcome = simulate(direction, entry, float(result["stop_loss"]), rows[index + 1:index + 1 + HORIZON], fee_bps, slip_bps)
        if outcome is None:
            skipped["gecersiz_stop" if (entry - result["stop_loss"]) * (1 if direction == "LONG" else -1) <= 0 else "sonuc_yok"] += 1
            index += 1
            continue
        signals.append({
            "symbol": symbol, "time": decision_time, "day": datetime.fromtimestamp(decision_time, timezone.utc).strftime("%Y-%m-%d"),
            "direction": direction, "confidence": int(result["confidence"]), "trap": int(result["radar"]["trap_score"]),
            "regime": regime(decision_time), **outcome,
        })
        busy_until = outcome["exit_time"]
        index += 1
    return {"symbol": symbol, "candles": len(rows), "missing_months": missing, "gaps": gaps(rows, M15),
            "signals": signals, "skipped": skipped,
            "analyze_ms": (sum(timings) / len(timings) * 1000) if timings else None, "analyze_calls": len(timings)}


# ---------------------------------------------------------------- kontroller (aşama 1)
def mirror(rows: list[dict]) -> list[dict]:
    """Fiyatı yansıt: p → 2M − p. EMA/MACD/Bollinger tam yansır, RSI → 100 − RSI, ADX/ATR/hacim aynı kalır."""
    pivot = 2 * max(row["high"] for row in rows) * 1.5
    return [{"time": row["time"], "open": pivot - row["open"], "close": pivot - row["close"],
             "high": pivot - row["low"], "low": pivot - row["high"], "volume": row["volume"]} for row in rows]


def checks(data_root: Path, stage: str, samples: int = 200, seed: int = 7) -> dict[str, Any]:
    spec = STAGES[stage]
    months = month_list(spec["warmup"], spec["end"])
    rng = random.Random(seed)
    leak_fail = sym_total = sym_match = sym_tie = 0
    leak_total = 0
    for symbol in spec["symbols"]:
        rows, _ = load_series(data_root, symbol, "15m", months)
        if len(rows) < WINDOW + HORIZON + 2:
            continue
        for _ in range(samples // len(spec["symbols"])):
            index = rng.randrange(WINDOW, len(rows) - HORIZON - 1)
            # 1) Sızıntı: karar anından SONRAKİ mumlar rastgele bozulunca karar değişmemeli.
            base = analysis.analyze(closed_window(rows, index))
            future_broken = rows[:index + 1] + [{**row, "close": row["close"] * rng.uniform(0.5, 1.5),
                                                  "high": row["high"] * 2, "low": row["low"] * 0.4} for row in rows[index + 1:]]
            again = analysis.analyze(closed_window(future_broken, index))
            leak_total += 1
            if (base["direction"], base["confidence"]) != (again["direction"], again["confidence"]):
                leak_fail += 1
            # 2) Simetri: yansıtılmış seri ters yönü ve aynı güveni vermeli.
            mirrored = analysis.analyze(mirror(closed_window(rows, index)))
            expected = {"LONG": "SHORT", "SHORT": "LONG", "BEKLE": "BEKLE"}[base["direction"]]
            sym_total += 1
            if mirrored["direction"] == expected and mirrored["confidence"] == base["confidence"]:
                sym_match += 1
            else:
                sym_tie += 1
    return {"leak_samples": leak_total, "leak_failures": leak_fail,
            "symmetry_samples": sym_total, "symmetry_match": sym_match, "symmetry_mismatch": sym_tie}


# ---------------------------------------------------------------- özet
def summarize(signals: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for signal in signals:
        band = band_of(signal["confidence"])
        if band:
            groups.setdefault((band, signal["direction"]), []).append(signal)
    table = []
    for (band, direction), rows in sorted(groups.items()):
        rows.sort(key=lambda row: row["time"])
        wins = sum(row["outcome"] == "KAZANC" for row in rows)
        losses = sum(row["outcome"] == "KAYIP" for row in rows)
        timeouts = sum(row["outcome"] == "ZAMAN_ASIMI" for row in rows)
        n = len(rows)
        days = len({row["day"] for row in rows})
        enough = n >= MIN_SIGNALS and days >= MIN_DAYS
        interval = wilson(wins, n)
        table.append({
            "band": band, "direction": direction, "signals": n, "days": days, "wins": wins, "losses": losses, "timeouts": timeouts,
            "win_rate": wins / n if n else None,                       # hedef 24 saatte, stoptan önce
            "win_rate_resolved": wins / (wins + losses) if wins + losses else None,  # zaman aşımı hariç
            "wilson95": interval, "avg_r": sum(row["r"] for row in rows) / n if n else None,
            "total_r": sum(row["r"] for row in rows), "max_drawdown_r": max_drawdown([row["r"] for row in rows]),
            "enough_sample": enough,
        })
    brier = None
    resolved = [row for row in signals]
    if resolved:
        brier = sum((row["confidence"] / 100 - (row["outcome"] == "KAZANC")) ** 2 for row in resolved) / len(resolved)
        base_rate = sum(row["outcome"] == "KAZANC" for row in resolved) / len(resolved)
        brier_base = sum((base_rate - (row["outcome"] == "KAZANC")) ** 2 for row in resolved) / len(resolved)
    reliability = []
    for low, high in BANDS:
        rows = [row for row in signals if low <= row["confidence"] <= high]
        if rows:
            reliability.append({"band": f"{low}-{high}", "n": len(rows),
                                "predicted": sum(row["confidence"] for row in rows) / len(rows) / 100,
                                "observed": sum(row["outcome"] == "KAZANC" for row in rows) / len(rows)})
    return {"signals": len(signals), "table": table, "brier": brier,
            "brier_constant_base_rate": brier_base if resolved else None, "reliability": reliability}


def reliability_svg(reliability: list[dict[str, Any]], title: str) -> str:
    size, pad = 360, 48
    scale = lambda value: pad + value * (size - 2 * pad)  # noqa: E731
    points = "".join(
        f"<circle cx='{scale(row['predicted']):.1f}' cy='{size - scale(row['observed']):.1f}' r='{3 + min(9, math.log10(row['n'] + 1) * 2):.1f}' fill='#3b82f6' fill-opacity='0.7'/>"
        f"<text x='{scale(row['predicted']) + 8:.1f}' y='{size - scale(row['observed']) - 6:.1f}' font-size='10' fill='#444'>{row['band']} (n={row['n']})</text>"
        for row in reliability)
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' font-family='sans-serif'>"
            f"<rect width='{size}' height='{size}' fill='white'/>"
            f"<text x='{pad}' y='24' font-size='13' font-weight='600'>{title}</text>"
            f"<line x1='{pad}' y1='{size - pad}' x2='{size - pad}' y2='{pad}' stroke='#999' stroke-dasharray='4 4'/>"
            f"<line x1='{pad}' y1='{size - pad}' x2='{size - pad}' y2='{size - pad}' stroke='#333'/>"
            f"<line x1='{pad}' y1='{size - pad}' x2='{pad}' y2='{pad}' stroke='#333'/>"
            f"<text x='{size / 2}' y='{size - 12}' font-size='11' text-anchor='middle'>Puan / 100 (tahmin)</text>"
            f"<text x='14' y='{size / 2}' font-size='11' transform='rotate(-90 14 {size / 2})' text-anchor='middle'>Gerçek kazanma oranı</text>"
            f"{points}</svg>")


def pct(value: float | None) -> str:
    return "—" if value is None else f"%{value * 100:.1f}"


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# Eski analist sistemi – başlangıç ölçümü ({report['stage']})", "",
             f"Oluşturma: {report['created_at']} · analysis.py sha256 `{report['analysis_sha256_before']}` "
             f"({'DEĞİŞMEDİ' if report['analysis_unchanged'] else 'DEĞİŞTİ!'})", "",
             f"Maliyet: komisyon {report['fee_bps']} bp + slippage {report['slip_bps']} bp (taraf başı). "
             f"Kazanç = 24 saatte stoptan önce +1R. Zaman aşımı ayrı sütunda; 'kazanma' oranına kazanç sayılmaz.", ""]
    checks_ = report.get("checks")
    if checks_:
        lines += ["## Kontroller", "",
                  f"- Gelecek verisi sızıntısı: {checks_['leak_samples']} örnekte {checks_['leak_failures']} hata.",
                  f"- LONG/SHORT simetrisi (fiyat yansıtma): {checks_['symmetry_samples']} örnekte {checks_['symmetry_match']} birebir, "
                  f"{checks_['symmetry_mismatch']} farklı (eşitlik durumları).",
                  f"- Açık mum: yapısal olarak engelli (her kararda doğrulanır).",
                  f"- analyze() ortalama süre: {report['analyze_ms']:.2f} ms / çağrı ({report['analyze_calls']} çağrı)." if report.get("analyze_ms") else "",
                  ""]
    lines += ["## Veri", "", "| Coin | 15m mum | Eksik ay | Boşluk | Sinyal | BEKLE | Az veri |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["symbols"]:
        lines.append(f"| {row['symbol']} | {row['candles']} | {', '.join(row['missing_months']) or '—'} | {row['gaps']} | "
                     f"{len(row['signals']) if isinstance(row['signals'], list) else row['signals']} | {row['skipped']['bekle']} | {row['skipped']['az_veri']} |")
    for name, summary in report["periods"].items():
        lines += ["", f"## Dönem: {name} · {summary['signals']} sinyal", "",
                  f"Brier: {summary['brier']:.4f} (sabit taban oranla: {summary['brier_constant_base_rate']:.4f}; düşük = iyi)" if summary["brier"] is not None else "Brier: —", "",
                  "| Bant | Yön | Sinyal | Gün | Kazanç | Kayıp | Zaman aşımı | Kazanma | Wilson %95 | Ort. R | Toplam R | Maks. düşüş R | Yeterli örnek |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for row in summary["table"]:
            interval = row["wilson95"]
            ci = f"{pct(interval[0])}–{pct(interval[1])}" if interval else "—"
            lines.append(f"| {row['band']} | {row['direction']} | {row['signals']} | {row['days']} | {row['wins']} | {row['losses']} | {row['timeouts']} | "
                         f"{pct(row['win_rate']) if row['enough_sample'] else 'yetersiz veri'} | {ci if row['enough_sample'] else '—'} | "
                         f"{row['avg_r']:+.3f} | {row['total_r']:+.1f} | {row['max_drawdown_r']:.1f} | {'evet' if row['enough_sample'] else 'hayır'} |")
        lines += ["", "Güvenilirlik (puan/100 ile gerçek kazanma oranı):", "", "| Bant | n | Puan/100 | Gerçek |", "| --- | --- | --- | --- |"]
        lines += [f"| {row['band']} | {row['n']} | {pct(row['predicted'])} | {pct(row['observed'])} |" for row in summary["reliability"]]
    regimes = report.get("regimes") or {}
    if regimes:
        lines += ["", "## Piyasa koşuluna göre (tüm dönem, tüm bantlar)", "", "| Koşul | Sinyal | Kazanma | Ort. R |", "| --- | --- | --- | --- |"]
        for name, row in sorted(regimes.items()):
            lines.append(f"| {name} | {row['n']} | {pct(row['win_rate'])} | {row['avg_r']:+.3f} |")
    return "\n".join(line for line in lines if line is not None) + "\n"


def run(stage: str, data_root: Path, out: Path, workers: int, fee_bps: float, slip_bps: float, with_checks: bool) -> dict[str, Any]:
    before = sha256(ANALYSIS_PATH)
    spec = STAGES[stage]
    started = time.perf_counter()
    jobs = [(symbol, str(data_root), stage, fee_bps, slip_bps) for symbol in spec["symbols"]]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            per_symbol = list(pool.map(run_symbol, jobs))
    else:
        per_symbol = [run_symbol(job) for job in jobs]
    signals = [signal for row in per_symbol for signal in row["signals"]]
    periods = {}
    for name, (first, last) in spec["periods"].items():
        lo, hi = month_start(first), month_end(last)
        periods[name] = summarize([row for row in signals if lo <= row["time"] < hi])
    regimes: dict[str, dict[str, Any]] = {}
    for signal in signals:
        bucket = regimes.setdefault(signal["regime"], {"n": 0, "wins": 0, "r": 0.0})
        bucket["n"] += 1
        bucket["wins"] += signal["outcome"] == "KAZANC"
        bucket["r"] += signal["r"]
    regimes = {key: {"n": value["n"], "win_rate": value["wins"] / value["n"], "avg_r": value["r"] / value["n"]} for key, value in regimes.items()}
    calls = sum(row["analyze_calls"] for row in per_symbol)
    total_ms = sum((row["analyze_ms"] or 0) * row["analyze_calls"] for row in per_symbol)
    report = {
        "stage": stage, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fee_bps": fee_bps, "slip_bps": slip_bps, "window": WINDOW, "min_closed": MIN_CLOSED, "horizon_bars": HORIZON,
        "symbols": [{**row, "signals": len(row["signals"])} for row in per_symbol],
        "periods": periods, "regimes": regimes,
        "analyze_ms": total_ms / calls if calls else None, "analyze_calls": calls,
        "runtime_seconds": round(time.perf_counter() - started, 1),
        "checks": checks(data_root, stage) if with_checks else None,
        "analysis_sha256_before": before,
    }
    report["analysis_sha256_after"] = sha256(ANALYSIS_PATH)
    report["analysis_unchanged"] = report["analysis_sha256_after"] == before
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "signals.jsonl").write_text("".join(json.dumps(row) + "\n" for row in signals), encoding="utf-8")
    md_report = {**report, "symbols": [{**row} for row in per_symbol]}
    (out / "report.md").write_text(markdown(md_report), encoding="utf-8")
    for name, summary in periods.items():
        (out / f"reliability-{name}.svg").write_text(reliability_svg(summary["reliability"], f"Eski puan · {name}"), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download")
    download.add_argument("--stage", choices=sorted(STAGES), required=True)
    download.add_argument("--data", type=Path, required=True)
    download.add_argument("--workers", type=int, default=6)
    runner = sub.add_parser("run")
    runner.add_argument("--stage", choices=sorted(STAGES), required=True)
    runner.add_argument("--data", type=Path, required=True)
    runner.add_argument("--out", type=Path, required=True)
    runner.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    runner.add_argument("--fee-bps", type=float, default=FEE_BPS)
    runner.add_argument("--slip-bps", type=float, default=SLIP_BPS)
    runner.add_argument("--no-checks", action="store_true")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    for path in [args.data] + ([args.out] if args.command == "run" else []):
        if path.resolve().is_relative_to(repo):
            parser.error("Veri ve rapor klasörü repo dışında olmalı.")
    if args.command == "download":
        print(json.dumps(download_stage(args.stage, args.data.resolve(), args.workers), indent=2))
        return
    report = run(args.stage, args.data.resolve(), args.out.resolve(), args.workers, args.fee_bps, args.slip_bps, not args.no_checks)
    print(json.dumps({"out": str(args.out.resolve()), "signals": sum(row["signals"] for row in report["symbols"]),
                      "runtime_seconds": report["runtime_seconds"], "analysis_unchanged": report["analysis_unchanged"]}, indent=2))


if __name__ == "__main__":
    main()
