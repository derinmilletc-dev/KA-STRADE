"""Kais 2 · geçmiş veri backtest'i (yerel, yalnız okuma).

Kais'in MEVCUT giriş kurallarını (analysis.analyze + canlı MTF kapısı + canlı risk boyutlama) 15 dakikalık geçmiş mumlarda
çalıştırır. Emir göndermez, internet ve API anahtarı kullanmaz. Yalnız `history_download.py` çıktısını okur.

  python kais2_backtest.py --data C:\\Users\\ahmtt\\kaistrade-data\\history-v1 --out C:\\Users\\ahmtt\\kaistrade-data\\backtest-v1

Kilitli dönem (2026-06 ve sonrası) ASLA okunmaz. Eksik coin-aylar atlanır, hiçbir şey tahminle doldurulmaz.
Dürüstlük kuralları: karar yalnız KAPANMIŞ mumlarla verilir; giriş bir sonraki mumun açılışında; aynı mumda stop ve hedef
birlikte görülürse STOP önce sayılır (kötümser); komisyon, kayma ve funding düşülür.
"""
from __future__ import annotations

import argparse
import ast
import bisect
import csv
import io
import json
import math
import random
import sys
import zipfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app import execution_core as core  # noqa: E402  (yalnız standart kütüphane)
from app.analysis import analyze  # noqa: E402

VERSION = "kais2-backtest-v1"
FIRST_MONTH, LAST_MONTH = "2024-07", "2026-05"       # analiz dönemi
LOAD_FROM = "2024-05"                                  # ısınma (gösterge için); işlem açılmaz
LOCKED_FROM = "2026-06"
WINDOW = 300
MIN_15M, MIN_HIGHER = 220, 50
TP1_FRACTION = 0.60
FEE_BPS, SPREAD_BPS, SLIPPAGE_BPS = 5.0, 2.0, 3.0   # taraf başına; canlı politika ile aynı
COST_SCENARIOS = (1.0, 1.5, 2.0, 3.0)
TRUE_NO = {"0", "false", "hayır", "hayir", "no"}


def locked(month: str) -> bool:
    return month >= LOCKED_FROM


# --------------------------------------------------------------------------- canlı MTF kapısı (main.py'den birebir)

def load_live_mtf() -> Any:
    """`shared_mtf_decision` fonksiyonunu app/main.py kaynağından olduğu gibi alır (FastAPI kurmadan)."""
    import __future__ as future

    source = (HERE / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted_const, func = None, None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SHORT_MTF_ALIGNMENT_MAX" for t in node.targets):
            wanted_const = ast.literal_eval(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == "shared_mtf_decision":
            func = node
    if wanted_const is None or func is None:
        raise RuntimeError("main.py içinde shared_mtf_decision / SHORT_MTF_ALIGNMENT_MAX bulunamadı")
    module = ast.Module(body=[func], type_ignores=[])
    namespace: dict[str, Any] = {"SHORT_MTF_ALIGNMENT_MAX": wanted_const}
    exec(compile(module, "main.py:shared_mtf_decision", "exec", flags=future.annotations.compiler_flag), namespace)
    return namespace["shared_mtf_decision"]


# --------------------------------------------------------------------------- veri okuma

def read_zip_csv(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv"):
            raise ValueError(f"Beklenmeyen zip içeriği: {path.name}")
        text = archive.read(names[0]).decode("utf-8", "replace")
    return list(csv.reader(io.StringIO(text)))


def month_of_file(path: Path) -> str:
    return path.stem[-7:]


def load_candles(root: Path, symbol: str) -> list[dict]:
    rows: dict[int, dict] = {}
    folder = root / "raw" / "klines_15m" / symbol
    for path in sorted(folder.glob("*.zip")):
        month = month_of_file(path)
        if locked(month) or month < LOAD_FROM:
            continue
        for row in read_zip_csv(path):
            if len(row) < 6 or not row[0].strip().isdigit():
                continue
            stamp = int(row[0])
            stamp = stamp // 1_000_000 if stamp >= 10 ** 15 else stamp // 1000 if stamp >= 10 ** 12 else stamp
            rows[stamp] = {"time": stamp, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
                           "close": float(row[4]), "volume": float(row[5])}
    return [rows[key] for key in sorted(rows)]


def load_funding(root: Path, symbol: str) -> list[tuple[int, float]]:
    events: dict[int, float] = {}
    folder = root / "raw" / "fundingRate" / symbol
    for path in sorted(folder.glob("*.zip")):
        month = month_of_file(path)
        if locked(month) or month < LOAD_FROM:
            continue
        for row in read_zip_csv(path):
            if len(row) < 3 or not row[0].strip().isdigit():
                continue
            stamp = int(row[0])
            stamp = stamp // 1_000_000 if stamp >= 10 ** 15 else stamp // 1000 if stamp >= 10 ** 12 else stamp
            try:
                events[stamp] = float(row[2])
            except ValueError:
                continue
    return sorted(events.items())


def aggregate(candles: list[dict], size: int) -> list[dict]:
    """15m → 1h/4h. Yalnız 15m mumların TAMAMI olan dilimler (eksik dilim atlanır, doldurulmaz)."""
    need = size // 900
    groups: dict[int, list[dict]] = {}
    for candle in candles:
        groups.setdefault(candle["time"] - candle["time"] % size, []).append(candle)
    bars = []
    for start in sorted(groups):
        group = groups[start]
        if len(group) != need:
            continue
        bars.append({"time": start, "open": group[0]["open"], "high": max(c["high"] for c in group),
                     "low": min(c["low"] for c in group), "close": group[-1]["close"], "volume": sum(c["volume"] for c in group)})
    return bars


# --------------------------------------------------------------------------- işlem yönetimi

def month_str(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m")


def simulate_exit(direction: str, entry_time: int, entry_open: float, stop: float, tp1: float, tp3: float,
                  bars: list[dict]) -> dict[str, Any]:
    """Giriş mumundan itibaren çıkış yolu. Fiyatlar BEKLENEN (kayma/komisyon sonradan)."""
    sign = 1 if direction == "LONG" else -1
    legs: list[dict] = []
    remaining, tp1_done, ambiguous = 1.0, False, 0
    last = None
    previous_time = None
    for bar in bars:
        if previous_time is not None and bar["time"] != previous_time + 900:
            legs.append({"time": previous_time + 900, "fraction": remaining, "price": last["close"], "reason": "VERI_BOSLUGU"})
            return {"legs": legs, "ambiguous": ambiguous}
        previous_time, last = bar["time"], bar
        adverse = bar["low"] if sign == 1 else bar["high"]
        favorable = bar["high"] if sign == 1 else bar["low"]
        stop_hit = sign * (adverse - stop) <= 0
        tp1_hit = (not tp1_done) and sign * (favorable - tp1) >= 0
        tp3_hit = sign * (favorable - tp3) >= 0
        if stop_hit and (tp1_hit or tp3_hit):
            ambiguous += 1
        if stop_hit:
            legs.append({"time": bar["time"] + 899, "fraction": remaining, "price": stop, "reason": "STOP"})
            return {"legs": legs, "ambiguous": ambiguous}
        if tp1_hit:
            legs.append({"time": bar["time"] + 899, "fraction": TP1_FRACTION, "price": tp1, "reason": "TP1"})
            remaining -= TP1_FRACTION
            tp1_done = True
        if tp3_hit:
            legs.append({"time": bar["time"] + 899, "fraction": remaining, "price": tp3, "reason": "TP3"})
            return {"legs": legs, "ambiguous": ambiguous}
    if last is not None and remaining > 1e-12:
        legs.append({"time": last["time"] + 899, "fraction": remaining, "price": last["close"], "reason": "VERI_SONU"})
    return {"legs": legs, "ambiguous": ambiguous}


def price_net(direction: str, entry: float, legs: list[dict], notional: float, funding_usdt: float, multiplier: float) -> float:
    """Net USDT: brüt - komisyon - kayma(+yarım spread) - funding. `multiplier` maliyetleri çarpar."""
    sign = 1 if direction == "LONG" else -1
    fee = FEE_BPS * multiplier / 10_000
    slip = (SLIPPAGE_BPS + SPREAD_BPS / 2) * multiplier / 10_000
    quantity = notional / entry
    actual_entry = entry * (1 + sign * slip)
    total = -quantity * actual_entry * fee
    for leg in legs:
        leg_quantity = quantity * leg["fraction"]
        actual = leg["price"] * (1 - sign * slip)
        total += leg_quantity * sign * (actual - actual_entry) - leg_quantity * actual * fee
    return total - funding_usdt


def funding_cost(direction: str, entry_time: int, notional: float, entry: float, legs: list[dict],
                 funding: list[tuple[int, float]], price_at) -> float:
    sign = 1 if direction == "LONG" else -1
    quantity = notional / entry
    end = legs[-1]["time"] if legs else entry_time
    total = 0.0
    times = [stamp for stamp, _ in funding]
    low, high = bisect.bisect_right(times, entry_time), bisect.bisect_right(times, end)
    for stamp, rate in funding[low:high]:
        held = 1.0 - sum(leg["fraction"] for leg in legs if leg["time"] < stamp)
        if held <= 1e-12:
            continue
        total += sign * rate * quantity * held * price_at(stamp)
    return total


# --------------------------------------------------------------------------- bir coin

def run_symbol(args: tuple[str, str, list[str], dict]) -> dict[str, Any]:
    root_text, symbol, months, settings = args
    root = Path(root_text)
    mtf_gate = load_live_mtf()
    policy = core.sanitize_execution_policy({})
    min_confidence = int(core.configured_min_confidence())
    allow_either = bool(core.configured_mtf_allow_either_timeframe())
    eligible = set(months)
    candles = load_candles(root, symbol)
    skipped: Counter = Counter()
    trades: list[dict] = []
    if len(candles) < MIN_15M + 2:
        return {"symbol": symbol, "trades": trades, "skipped": {"veri_yok": 1}, "decisions": 0}
    funding = load_funding(root, symbol)
    hours, fours = aggregate(candles, 3600), aggregate(candles, 14400)
    hour_ends, four_ends = [b["time"] + 3600 for b in hours], [b["time"] + 14400 for b in fours]
    times = [c["time"] for c in candles]
    by_time = {c["time"]: c for c in candles}

    def price_at(stamp: int) -> float:
        candle = by_time.get(stamp - stamp % 900)
        return candle["open"] if candle else candles[-1]["close"]

    decisions, busy_until = 0, -1
    limit = settings.get("limit_decisions")
    only_side = settings.get("only_side") or ""
    for index in range(MIN_15M - 1, len(candles) - 1):
        signal = candles[index]
        decision_time = signal["time"] + 900
        if month_str(signal["time"]) not in eligible or decision_time <= busy_until:
            continue
        following = candles[index + 1]
        if following["time"] != decision_time or signal["time"] - candles[index - MIN_15M + 1]["time"] != 900 * (MIN_15M - 1):
            skipped["mum_boslugu"] += 1
            continue
        decisions += 1
        if limit and decisions > limit:
            break
        analysis = analyze(candles[max(0, index - WINDOW + 1):index + 1])
        direction = analysis["direction"]
        if direction not in {"LONG", "SHORT"}:
            continue
        if only_side and direction != only_side:
            skipped["yon_disi"] += 1
            continue
        if not (analysis["confidence"] >= min_confidence and analysis["radar"]["trap_score"] <= 35
                and analysis["radar"]["breakout_quality"] >= 50):
            skipped["kalite_kapisi"] += 1
            continue
        frames = {}
        for name, bars, ends in (("1h", hours, hour_ends), ("4h", fours, four_ends)):
            stop_index = bisect.bisect_right(ends, decision_time)
            window = bars[max(0, stop_index - WINDOW):stop_index]
            if len(window) < MIN_HIGHER:
                skipped["ust_zaman_yetersiz"] += 1
                frames = None
                break
            frames[name] = analyze(window)
        if frames is None:
            continue
        mtf = mtf_gate(symbol=symbol, entry_direction=direction, confidence_15m=float(analysis["confidence"]),
                       timeframe_results={k: {"direction": v["direction"], "confidence": v["confidence"]} for k, v in frames.items()},
                       allow_either_timeframe=allow_either, short_filter=True)
        if not mtf["entry_permission"]:
            skipped["mtf_kapisi"] += 1
            continue
        entry, stop = analysis["entry"], analysis["stop_loss"]
        try:
            sized = core.risk_sized_order(entry, stop, policy, atr=analysis["atr"])
        except ValueError:
            skipped["risk_kapisi"] += 1
            continue
        sign = 1 if direction == "LONG" else -1
        entry_open = following["open"]
        if sign * (entry_open - stop) <= 0 or sign * (analysis["tp1"] - entry_open) <= 0:
            skipped["giris_gecersiz"] += 1
            continue
        path = candles[index + 1:]
        # yol yalnız gerektiği kadar taranır: bar sayısı sınırsız ama ilk çıkışta durur
        outcome = simulate_exit(direction, following["time"], entry_open, stop, analysis["tp1"], analysis["tp3"], path)
        legs = outcome["legs"]
        if not legs:
            continue
        notional = float(sized["notional_usdt"])
        risk_usdt = float(sized["estimated_stop_loss_usdt"])
        funding_usdt = funding_cost(direction, following["time"], notional, entry_open, legs, funding, price_at)
        row = {"symbol": symbol, "direction": direction, "signal_time": signal["time"], "entry_time": following["time"],
               "exit_time": legs[-1]["time"], "entry_open": entry_open, "stop": stop, "tp1": analysis["tp1"], "tp3": analysis["tp3"],
               "confidence": analysis["confidence"], "alignment": mtf["alignment"], "notional_usdt": notional,
               "risk_usdt": risk_usdt, "funding_usdt": funding_usdt, "ambiguous_bars": outcome["ambiguous"],
               "exits": "|".join(f"{leg['reason']}:{leg['fraction']:.2f}@{leg['price']:.8g}" for leg in legs),
               "last_reason": legs[-1]["reason"], "legs": legs}
        for multiplier in COST_SCENARIOS:
            row[f"net_x{multiplier:g}"] = price_net(direction, entry_open, legs, notional, funding_usdt, multiplier)
        trades.append(row)
        busy_until = legs[-1]["time"]
    return {"symbol": symbol, "trades": trades, "skipped": dict(skipped), "decisions": decisions}


# --------------------------------------------------------------------------- rapor

def stats(rows: list[dict], key: str = "net_x1") -> dict[str, Any]:
    if not rows:
        return {"trades": 0}
    pnl = [row[key] for row in rows]
    rs = [row[key] / row["risk_usdt"] for row in rows if row["risk_usdt"] > 0]
    wins, losses = sum(p for p in pnl if p > 0), -sum(p for p in pnl if p < 0)
    ordered = sorted(rows, key=lambda row: row["exit_time"])
    equity = peak = drawdown = 0.0
    for row in ordered:
        equity += row[key] / row["risk_usdt"] if row["risk_usdt"] > 0 else 0.0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"trades": len(rows), "win_rate": sum(1 for p in pnl if p > 0) / len(pnl), "net_usdt": sum(pnl),
            "avg_r": sum(rs) / len(rs) if rs else 0.0, "total_r": sum(rs),
            "profit_factor": (wins / losses) if losses else None, "max_drawdown_r": drawdown}


def bootstrap(rows: list[dict], samples: int = 2000, seed: int = 2026) -> list[float] | None:
    values = [row["net_x1"] / row["risk_usdt"] for row in rows if row["risk_usdt"] > 0]
    if len(values) < 20:
        return None
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(samples))
    return [means[int(samples * 0.025)], means[int(samples * 0.975) - 1]]


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def iso(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M")


def report(out: Path, trades: list[dict], skipped: Counter, decisions: int, excluded: list[dict], symbols: int) -> list[str]:
    trades = sorted(trades, key=lambda row: row["entry_time"])
    flat = [{**row, "signal_time": iso(row["signal_time"]), "entry_time": iso(row["entry_time"]), "exit_time": iso(row["exit_time"])}
            for row in trades]
    fields = ["symbol", "direction", "signal_time", "entry_time", "exit_time", "entry_open", "stop", "tp1", "tp3", "confidence",
              "alignment", "notional_usdt", "risk_usdt", "funding_usdt", "ambiguous_bars", "exits", "last_reason"] + \
             [f"net_x{m:g}" for m in COST_SCENARIOS]
    write_csv(out / "trades.csv", flat, fields)
    months: dict[str, list[dict]] = {}
    coins: dict[str, list[dict]] = {}
    for row in trades:
        months.setdefault(month_str(row["entry_time"]), []).append(row)
        coins.setdefault(row["symbol"], []).append(row)
    head = ["trades", "win_rate", "net_usdt", "avg_r", "total_r", "profit_factor", "max_drawdown_r"]
    write_csv(out / "by-month.csv", [{"month": m, **stats(r)} for m, r in sorted(months.items())], ["month"] + head)
    write_csv(out / "by-coin.csv", [{"symbol": s, **stats(r)} for s, r in sorted(coins.items(), key=lambda kv: -stats(kv[1]).get("net_usdt", 0))],
              ["symbol"] + head)
    write_csv(out / "excluded-coin-months.csv", excluded, ["month", "symbol", "status"])
    total = stats(trades)
    ci = bootstrap(trades)
    longs, shorts = [r for r in trades if r["direction"] == "LONG"], [r for r in trades if r["direction"] == "SHORT"]
    reasons = Counter(r["last_reason"] for r in trades)
    lines = [f"# Kais 2 backtest raporu ({VERSION})", "",
             f"Oluşturma: {datetime.now(timezone.utc).isoformat(timespec='seconds')} · Dönem: {FIRST_MONTH} → {LAST_MONTH}. "
             "**Kilitli dönem (2026-06 ve sonrası) okunmadı.**", "",
             "Kural: Kais'in mevcut canlı giriş kuralları (analiz + kalite kapısı + 15m/1h/4h onayı + risk boyutlama). "
             "Emir yok, API yok. 1h/4h mumlar 15m'den üretildi. Portföy limitleri (en çok 5 pozisyon, günlük işlem sınırı) "
             "UYGULANMADI: her coin kendi başına, aynı anda en fazla 1 pozisyon. Çıkış: stop, %60'ı 1R hedefte (TP1), kalanı 3R hedefte (TP3) "
             "ya da stopta; R = işlemin başlangıçtaki risk tutarı (canlı risk boyutlama).", "",
             "## Genel (maliyetler: komisyon 5 bp, kayma+yarım spread 4 bp, taraf başına; funding dahil)", "",
             f"- Coin: {symbols} · karar anı: {decisions} · açılan işlem: {total['trades']}",
             f"- Kazanma oranı: {fmt(total.get('win_rate') and total['win_rate'] * 100, 1)}%" if total["trades"] else "- İşlem yok",
             ]
    if total["trades"]:
        lines += [f"- Net kâr/zarar: {fmt(total['net_usdt'])} USDT · toplam {fmt(total['total_r'])} R · işlem başına ort. {fmt(total['avg_r'], 3)} R",
                  f"- Profit factor: {fmt(total['profit_factor'])} · en büyük düşüş: {fmt(total['max_drawdown_r'])} R",
                  f"- Ort. R için %95 güven aralığı (bootstrap): {fmt(ci[0], 3) + ' … ' + fmt(ci[1], 3) if ci else 'yetersiz işlem'}",
                  f"- LONG: {stats(longs).get('trades', 0)} işlem, {fmt(stats(longs).get('total_r'))} R · SHORT: {stats(shorts).get('trades', 0)} işlem, "
                  f"{fmt(stats(shorts).get('total_r'))} R",
                  f"- Çıkış nedeni: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common()),
                  f"- Aynı mumda stop+hedef (stop önce sayıldı): {sum(1 for r in trades if r['ambiguous_bars'])} işlem", "",
                  "## Maliyet duyarlılığı (maliyetler × katsayı)", "", "| Katsayı | Net USDT | Toplam R | Ort. R | Profit factor |", "| --- | --- | --- | --- | --- |"]
        for multiplier in COST_SCENARIOS:
            s = stats(trades, f"net_x{multiplier:g}")
            lines.append(f"| ×{multiplier:g} | {fmt(s['net_usdt'])} | {fmt(s['total_r'])} | {fmt(s['avg_r'], 3)} | {fmt(s['profit_factor'])} |")
        lines += ["", "## Aylar", "", "| Ay | İşlem | Kazanma | Net USDT | Toplam R |", "| --- | --- | --- | --- | --- |"]
        for month, rows in sorted(months.items()):
            s = stats(rows)
            lines.append(f"| {month} | {s['trades']} | {fmt(s['win_rate'] * 100, 0)}% | {fmt(s['net_usdt'])} | {fmt(s['total_r'])} |")
        positive = sum(1 for rows in months.values() if stats(rows)["net_usdt"] > 0)
        lines += ["", f"Kârlı ay: {positive} / {len(months)}"]
    lines += ["", "## Elenen adaylar (kural gereği)", "", ", ".join(f"{k}: {v}" for k, v in skipped.most_common()) or "—", "",
              f"## Testten çıkarılan coin-aylar: {len(excluded)}", "", "Eksik/sorunlu veri nedeniyle atlandı, doldurulmadı (`excluded-coin-months.csv`).", "",
              "## Sınırlar (dürüstlük)", "",
              "- Stop/hedef kontrolü son fiyat mumlarıyla yapıldı (mark fiyat verisi indirilmedi).",
              "- Aynı mumda stop ve hedef birlikte görülürse stop önce sayıldı (kötümser).",
              "- Gerçek defter derinliği/spread bilinmiyor; kayma sabit varsayıldı, duyarlılık tablosu bunu sınar.",
              "- Sonuç geçmiş verinin tek örneklemidir; gelecek için garanti değildir."]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [p.name for p in sorted(out.iterdir())]


# --------------------------------------------------------------------------- giriş noktası

def latest_reports(root: Path) -> Path:
    folders = sorted(p for p in root.glob("reports*") if (p / "universe-by-month.csv").is_file())
    if not folders:
        raise FileNotFoundError("universe-by-month.csv bulunamadı")
    return folders[-1]


def eligible_coin_months(root: Path) -> tuple[dict[str, list[str]], list[dict]]:
    """Doğrulama raporunda 'tam' olan coin-aylar. Diğerleri elenir ve listelenir."""
    status: dict[tuple[str, str], str] = {}
    candidates = sorted(p for p in root.glob("reports2-*") if (p / "validation-coin-month.csv").is_file()) or \
        sorted(p for p in root.glob("reports*") if (p / "validation-coin-month.csv").is_file())
    if not candidates:
        raise FileNotFoundError("validation-coin-month.csv bulunamadı; önce veri doğrulaması çalışmalı")
    with (candidates[-1] / "validation-coin-month.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            status[(row["symbol"], row["month"])] = row["status"].strip()
    not_testable: set[tuple[str, str]] = set()
    with (latest_reports(root) / "universe-by-month.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("testable", "")).strip().lower() in TRUE_NO:
                not_testable.add((row["symbol"], row["month"]))
    months: dict[str, list[str]] = {}
    excluded: list[dict] = []
    for (symbol, month), state in sorted(status.items()):
        if locked(month) or not (FIRST_MONTH <= month <= LAST_MONTH):
            continue
        if state != "tam" or (symbol, month) in not_testable:
            excluded.append({"month": month, "symbol": symbol, "status": state if state != "tam" else "evrende test edilemez"})
            continue
        months.setdefault(symbol, []).append(month)
    return months, excluded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kais 2 yerel backtest (yalnız okuma)")
    parser.add_argument("--data", type=Path, required=True, help="history_download çıktısı (history-v1)")
    parser.add_argument("--out", type=Path, required=True, help="YENİ boş rapor klasörü")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit-coins", type=int, default=0, help="hızlı deneme: yalnız ilk N coin")
    parser.add_argument("--limit-decisions", type=int, default=0, help="hızlı deneme: coin başına en çok N karar anı")
    parser.add_argument("--only-side", choices=["LONG", "SHORT"], default="", help="yalnız bu yöndeki girişleri test et (diğer yön hiç açılmaz)")
    args = parser.parse_args(argv)
    if args.out.exists() and any(args.out.iterdir()):
        print(json.dumps({"error": "Çıktı klasörü boş değil; yeni bir klasör verin."}, ensure_ascii=False), file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    months, excluded = eligible_coin_months(args.data)
    symbols = sorted(months)
    if args.limit_coins:
        symbols = symbols[:args.limit_coins]
    settings = {"limit_decisions": args.limit_decisions, "only_side": args.only_side}
    jobs = [(str(args.data), symbol, months[symbol], settings) for symbol in symbols]
    trades: list[dict] = []
    skipped: Counter = Counter()
    decisions = 0
    print(json.dumps({"coins": len(jobs), "coin_months": sum(len(months[s]) for s in symbols), "excluded": len(excluded)}, ensure_ascii=False),
          file=sys.stderr, flush=True)
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for done, result in enumerate(pool.map(run_symbol, jobs), 1):
            trades += result["trades"]
            skipped.update(result["skipped"])
            decisions += result["decisions"]
            print(f"coin {done}/{len(jobs)} {result['symbol']}: {len(result['trades'])} işlem", file=sys.stderr, flush=True)
    names = report(args.out, trades, skipped, decisions, excluded, len(jobs))
    print(json.dumps({"output": str(args.out), "reports": names, "trades": len(trades), "decisions": decisions}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
