"""Yeni Analist motoru – A/B ağırlık modellerinin araştırma değerlendirmesi (canlı koda bağlı değil).

ÖNCEDEN KAYDEDİLMİŞ ÖLÇÜM KURALLARI (sonuçlar görülmeden sabitlendi; değiştirmek yeni sürüm ve gerekçe ister):

Karar noktaları ve sonuç
- Her coin için 4 saatte bir (UTC 00,04,…,20) karar anı; yalnız o ana kadar kapanmış 15m/1h/4h mumlar (1h/4h, 15m'den
  yalnız tam ve kesintisiz gruplarla türetilir). Son 260 15m mum kesintisiz değilse nokta atlanır.
- Her noktada LONG ve SHORT sonucu BİR KEZ hesaplanır: giriş = sonraki 15m açılışı, stop = mevcut mekanizma
  (`analysis.entry_levels`, değiştirilmeden), hedef = 1R, 24 saat (96 mum), aynı mumda stop+hedef = kayıp,
  komisyon 5 bp + slippage 3 bp taraf başı. Ufuk içinde veri boşluğu varsa sonuç bilinmez, nokta sayılmaz.
  Böylece OLD, A ve B birebir aynı veri/giriş/çıkış/maliyetle ölçülür; modeller yalnız YÖN ve SKOR seçer.
- Sonuç üç sınıftır: KAZANC / KAYIP / ZAMAN_ASIMI. Zaman aşımı hiçbir sınıfa katılmaz.

Dönemler: eğitim 2024-11…2025-10 · doğrulama 2025-11…2026-04 · test 2026-05…2026-10.

Kalibrasyon (eğitimde kurulur, sonra donar)
- Model başına çok sınıflı lojistik regresyon; girdiler s=skor/100, s², LONG göstergesi, s×LONG; C=1,0.
- Sabit taban modeli: eğitimdeki sınıf frekansları.
- Ölçüt: çok sınıflı Brier = ortalama Σ_k (p_k − y_k)² (0 en iyi, 2 en kötü).

Güven aralıkları: gün bazlı hareketli blok bootstrap. Bir blok = 7 ardışık takvim günü, o günlerin TÜM coinleri
birlikte (aynı gün içindeki sinyaller ve 24 saatlik ufuk taşmaları bağımsız sayılmaz). B = 2000, tohum 20261104.

Seçim (yalnız doğrulama dönemi)
- Ana ölçüt: ΔBrier = Brier(sabit) − Brier(model). Bir model, ΔBrier %95 alt sınırı > 0 ise "tabandan iyi".
- İkisi de geçerse: eşli bootstrap ile ΔBrier(A) − ΔBrier(B) aralığı 0'ı dışlıyorsa iyi olan; dışlamıyorsa EŞİT →
  A seçilir (iki model eşit karmaşıklıkta; A planda ilk sıradadır ve bu kural önceden konmuştur).
- Hiçbiri geçmezse model seçilmez, yeni motor üretime alınmaz ve test dönemi AÇILMAZ.
- İkincil (raporlanır, seçimi değiştirmez): Spearman(skor, gerçekleşen R) > 0 ve aralığı; skor bandı × yön tablosu;
  piyasa koşulu tablosu.
- Ekonomik avantaj iddiası: maliyet sonrası ortalama R > 0 VE blok bootstrap alt sınırı > 0. Aksi halde olasılık
  yalnız betimleyicidir, kârlılık iddia edilmez.
- Olasılık gösterimi: bant × yön başına ≥ 200 sinyal ve ≥ 30 farklı gün; yoksa olasılık gösterilmez.
- Test dönemi: yalnız seçilen model + eski sistem referansı, donmuş kalibratörle, bir kez.

Örnek:
  python analyst_ab_cli.py build    --stage stage1 --data ~/kais-data/analyst --out ~/kais-data/ab-stage1
  python analyst_ab_cli.py checks   --stage stage1 --data ~/kais-data/analyst --out ~/kais-data/ab-stage1
  python analyst_ab_cli.py evaluate --table ~/kais-data/ab-stage2/decisions.jsonl --out ~/kais-data/ab-stage2
  python analyst_ab_cli.py timeouts --table ~/kais-data/ab-stage2/decisions.jsonl --out ~/kais-data/ab-stage2
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyst_baseline_cli as base  # noqa: E402  (veri yükleme, piyasa koşulu, maliyet sabitleri)
from app import analysis, analyst_engine as engine  # noqa: E402  (analysis yalnız okunur)

M15, H1, H4 = 900, 3600, 14400
WINDOW = 500
GRID = H4
HORIZON = 96
RESEARCH_HORIZONS = (48, 96, 192, 288)      # 12/24/48/72 saat — yalnız zaman aşımı duyarlılık araştırması
CLASSES = ("KAZANC", "KAYIP", "ZAMAN_ASIMI")
CODE = {"KAZANC": "W", "KAYIP": "L", "ZAMAN_ASIMI": "T"}
CLASS_INDEX = {"W": 0, "L": 1, "T": 2}
PERIODS = {"egitim": ("2024-11", "2025-10"), "dogrulama": ("2025-11", "2026-04"), "test": ("2026-05", "2026-10")}
BOOT_B, BOOT_BLOCK_DAYS, BOOT_SEED = 2000, 7, 20261104
CALIB_C = 1.0
MODELS = ("OLD", "A", "B")
STAGES = {
    "stage1": {"symbols": base.STAGE1_SYMBOLS, "load_from": "2026-04", "start": "2026-08", "end": "2026-10"},
    "stage2": {"symbols": base.STAGE2_SYMBOLS, "load_from": "2024-07", "start": "2024-11", "end": "2026-10"},
}


# ---------------------------------------------------------------- veri
def aggregate(rows: list[dict], step: int) -> list[dict]:
    """15m → 1h/4h. Yalnız tam ve kesintisiz gruplar (eksik 15m mum varsa o büyük mum hiç üretilmez)."""
    size = step // M15
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["time"] - row["time"] % step, []).append(row)
    out = []
    for start in sorted(groups):
        chunk = groups[start]
        if len(chunk) != size or any(chunk[i]["time"] != start + i * M15 for i in range(size)):
            continue
        out.append({"time": start, "open": chunk[0]["open"], "high": max(r["high"] for r in chunk),
                    "low": min(r["low"] for r in chunk), "close": chunk[-1]["close"], "volume": sum(r["volume"] for r in chunk)})
    return out


def closed_upto(rows: list[dict], times: list[int], step: int, now: int, size: int = WINDOW) -> list[dict]:
    """now anında kapanmış son `size` mum: açılış + step <= now."""
    end = bisect.bisect_right(times, now - step)
    return rows[max(0, end - size):end]


# ---------------------------------------------------------------- sonuç (çok ufuklu, zaman tabanlı)
def simulate(direction: str, entry: float, stop: float, rows: list[dict], start: int, fee_bps: float, slip_bps: float,
             horizons: tuple[int, ...] = RESEARCH_HORIZONS) -> dict[int, dict[str, Any] | None]:
    """rows[start] giriş mumudur. Her ufuk için sonuç; ufuk dolmadan veri boşluğu/sonu gelirse o ufuk None."""
    sign = 1 if direction == "LONG" else -1
    risk = (entry - stop) * sign
    out: dict[int, dict[str, Any] | None] = {h: None for h in horizons}
    if not risk > 0:
        return out
    target = entry + sign * risk
    rate = (fee_bps + slip_bps) / 10_000
    t0 = rows[start]["time"]
    longest = max(horizons)
    hit: tuple[int, str, float] | None = None
    last_close: dict[int, float] = {}
    for offset in range(longest):
        index = start + offset
        if index >= len(rows) or rows[index]["time"] != t0 + offset * M15:
            break
        bar = rows[index]
        adverse = bar["low"] if sign == 1 else bar["high"]
        favorable = bar["high"] if sign == 1 else bar["low"]
        if (adverse - stop) * sign <= 0:
            hit = (offset, "KAYIP", stop)
        elif (favorable - target) * sign >= 0:
            hit = (offset, "KAZANC", target)
        if hit:
            break
        for h in horizons:
            if offset == h - 1:
                last_close[h] = bar["close"]
    for h in horizons:
        if hit and hit[0] < h:
            exit_price = hit[2]
            out[h] = {"o": hit[1], "r": (exit_price - entry) * sign / risk - (entry + exit_price) * rate / risk, "bars": hit[0] + 1}
        elif h in last_close:
            exit_price = last_close[h]
            out[h] = {"o": "ZAMAN_ASIMI", "r": (exit_price - entry) * sign / risk - (entry + exit_price) * rate / risk, "bars": h}
    return out


# ---------------------------------------------------------------- karar tablosu
def build_symbol(args: tuple[str, str, str, float, float]) -> dict[str, Any]:
    symbol, data_root, stage, fee_bps, slip_bps = args
    spec = STAGES[stage]
    months = base.month_list(spec["load_from"], spec["end"])
    rows, missing = base.load_series(Path(data_root), symbol, "15m", months)
    btc, _ = base.load_series(Path(data_root), "BTCUSDT", "15m", months)
    regime = base.regime_lookup(aggregate(btc, H4))
    h1, h4 = aggregate(rows, H1), aggregate(rows, H4)
    t15, t1, t4 = [r["time"] for r in rows], [r["time"] for r in h1], [r["time"] for r in h4]
    index_of = {r["time"]: i for i, r in enumerate(rows)}
    lo, hi = base.month_start(spec["start"]), base.month_end(spec["end"])
    out: list[dict[str, Any]] = []
    skipped = {"bosluk": 0, "giris_yok": 0, "veri_gecersiz": 0}
    timing = {"old": 0.0, "engine": 0.0, "n": 0}
    first_grid = lo - lo % GRID
    for now in range(first_grid, hi, GRID):
        if now < lo:
            continue
        w15 = closed_upto(rows, t15, M15, now)
        if len(w15) < 260 or any(b["time"] - a["time"] != M15 for a, b in zip(w15[-260:], w15[-259:])):
            skipped["bosluk"] += 1
            continue
        entry_index = index_of.get(now)
        if entry_index is None:
            skipped["giris_yok"] += 1
            continue
        assert w15[-1]["time"] + M15 <= now, "açık mum hesaba girdi"
        w1, w4 = closed_upto(h1, t1, H1, now), closed_upto(h4, t4, H4, now)
        started = time.perf_counter()
        scores: dict[str, int] = {}
        old = analysis.analyze(w15, observation_scores=scores)
        mid = time.perf_counter()
        parts = engine.components(w15, w1, w4, now=now)
        timing["old"] += mid - started
        timing["engine"] += time.perf_counter() - mid
        timing["n"] += 1
        if parts["values"] is None:
            skipped["veri_gecersiz"] += 1
            continue
        entry = rows[entry_index]["open"]
        atr_value, price = old["atr"], old["entry"]
        record: dict[str, Any] = {
            "symbol": symbol, "time": now, "day": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d"),
            "regime": regime(now), "dq": parts["data_quality"]["overall"], "risk": parts["risk"],
            "c": [round(parts["values"][name], 6) for name in engine.COMPONENTS],
            "old_long": scores["long_score"], "old_short": scores["short_score"],
            "old_dir": old["direction"], "old_conf": old["confidence"], "atr_pct": round(atr_value / price, 6),
        }
        # Son 4 günün 24 saatlik aralık medyanı (yalnız geçmiş, kapanmış mumlar) — zaman aşımı araştırması için.
        blocks = [w15[-96 * (k + 1):len(w15) - 96 * k] for k in range(4)]
        ranges = sorted(max(b["high"] for b in blk) - min(b["low"] for b in blk) for blk in blocks if len(blk) == 96)
        record["range24_pct"] = round(ranges[len(ranges) // 2] / price, 6) if ranges else None
        for direction in ("LONG", "SHORT"):
            stop, _risk, _targets = analysis.entry_levels(direction, price, atr_value, old["support"], old["resistance"])
            res = simulate(direction, entry, stop, rows, entry_index, fee_bps, slip_bps)
            key = direction[0]
            record[f"{key}_risk_atr"] = round(abs(entry - stop) / atr_value, 4) if atr_value else None
            record[f"{key}_risk_pct"] = round(abs(entry - stop) / entry, 6)
            for h, value in res.items():
                record[f"{key}{h}"] = None if value is None else [CODE[value["o"]], round(value["r"], 5), value["bars"]]
        out.append(record)
    return {"symbol": symbol, "candles": len(rows), "missing_months": missing, "gaps": base.gaps(rows, M15),
            "decisions": out, "skipped": skipped,
            "old_ms": timing["old"] / timing["n"] * 1000 if timing["n"] else None,
            "engine_ms": timing["engine"] / timing["n"] * 1000 if timing["n"] else None}


def build(stage: str, data_root: Path, out: Path, workers: int) -> dict[str, Any]:
    before = base.sha256(base.ANALYSIS_PATH)
    started = time.perf_counter()
    jobs = [(symbol, str(data_root), stage, base.FEE_BPS, base.SLIP_BPS) for symbol in STAGES[stage]["symbols"]]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            per = list(pool.map(build_symbol, jobs))
    else:
        per = [build_symbol(job) for job in jobs]
    out.mkdir(parents=True, exist_ok=True)
    with (out / "decisions.jsonl").open("w", encoding="utf-8") as handle:
        for row in per:
            for record in row["decisions"]:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
    summary = {"stage": stage, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "engine_version": engine.ENGINE_VERSION, "runtime_seconds": round(time.perf_counter() - started, 1),
               "symbols": [{k: v for k, v in row.items() if k != "decisions"} | {"decisions": len(row["decisions"])} for row in per],
               "analysis_sha256_before": before, "analysis_sha256_after": base.sha256(base.ANALYSIS_PATH)}
    summary["analysis_unchanged"] = summary["analysis_sha256_before"] == summary["analysis_sha256_after"]
    (out / "build.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


# ---------------------------------------------------------------- aşama 1 kontrolleri (yeni motor)
def mirror(rows: list[dict], pivot: float) -> list[dict]:
    return [{"time": r["time"], "open": pivot - r["open"], "close": pivot - r["close"], "high": pivot - r["low"],
             "low": pivot - r["high"], "volume": r["volume"]} for r in rows]


def checks(stage: str, data_root: Path, out: Path, samples: int = 200, seed: int = 11) -> dict[str, Any]:
    spec = STAGES[stage]
    months = base.month_list(spec["load_from"], spec["end"])
    rng = random.Random(seed)
    result = {"samples": 0, "leak_failures": 0, "open_candle_failures": 0, "symmetry_match": 0, "symmetry_mismatch": 0,
              "symmetry_max_raw_error": 0.0, "weights_not_redistributed": 0, "quality_rules_ok": True,
              "old_api_identical": 0, "old_api_changed": 0}
    per_symbol = max(1, samples // len(spec["symbols"]))
    for symbol in spec["symbols"]:
        rows, _ = base.load_series(data_root, symbol, "15m", months)
        h1, h4 = aggregate(rows, H1), aggregate(rows, H4)
        t15, t1, t4 = [r["time"] for r in rows], [r["time"] for r in h1], [r["time"] for r in h4]
        lo = base.month_start(spec["start"])
        candidates = [r["time"] for r in rows if r["time"] >= lo and r["time"] % GRID == 0][:-30]
        for now in rng.sample(candidates, per_symbol):
            w15, w1, w4 = closed_upto(rows, t15, M15, now), closed_upto(h1, t1, H1, now), closed_upto(h4, t4, H4, now)
            ref = {m: engine.combine(engine.components(w15, w1, w4, now=now), m) for m in ("A", "B")}
            result["samples"] += 1
            # 1) Sızıntı: gelecekteki mumlar bozulsa da sonuç aynı.
            fut = [dict(r, close=r["close"] * rng.uniform(0.5, 1.5), high=r["high"] * 2, low=r["low"] * 0.4) for r in rows]
            f15 = rows[:bisect.bisect_right(t15, now - M15)] + fut[bisect.bisect_right(t15, now - M15):]
            f1, f4 = aggregate(f15, H1), aggregate(f15, H4)
            again = engine.combine(engine.components(closed_upto(f15, t15, M15, now), closed_upto(f1, [r["time"] for r in f1], H1, now),
                                                     closed_upto(f4, [r["time"] for r in f4], H4, now), now=now), "A")
            if again["analyst_raw"] != ref["A"]["analyst_raw"]:
                result["leak_failures"] += 1
            # 2) Açık mum: listeye henüz kapanmamış mum eklense de sonuç aynı (motor kendisi süzer).
            opened = engine.combine(engine.components(w15 + [dict(rows[t15.index(now)])], w1 + [dict(w1[-1], time=now - H1 + M15 * 2)],
                                                      w4 + [dict(w4[-1], time=now - M15)], now=now), "A")
            if opened["analyst_raw"] != ref["A"]["analyst_raw"]:
                result["open_candle_failures"] += 1
            # 3) Simetri: fiyat yansıtılınca ham değer tam ters, skor aynı.
            pivot = 3 * max(r["high"] for r in w15 + w1 + w4)
            mir = engine.combine(engine.components(mirror(w15, pivot), mirror(w1, pivot), mirror(w4, pivot), now=now), "A")
            error = abs(mir["analyst_raw"] + ref["A"]["analyst_raw"])
            result["symmetry_max_raw_error"] = max(result["symmetry_max_raw_error"], error)
            if error < 1e-9:
                result["symmetry_match"] += 1
            else:
                result["symmetry_mismatch"] += 1
            # 4) Eksik zaman dilimi: 4h kaldırılınca ağırlık yeniden dağıtılmamalı → |ham| büyüyemez fazla.
            no4 = engine.components(w15, w1, w4[:100], now=now)
            full = engine.components(w15, w1, w4, now=now)
            expected = full["values"]["mtf"] - 0.5 * (engine.higher_timeframe(w4) or 0.0)
            if abs(no4["values"]["mtf"] - expected) < 1e-12 and no4["data_quality"]["timeframes"]["4h"] == "GECERSIZ":
                result["weights_not_redistributed"] += 1
            # 5) Eski API: analyze() çıktısı motor çalıştıktan sonra da birebir aynı, yeni alan eklenmemiş.
            old_a = analysis.analyze(w15)
            engine.components(w15, w1, w4, now=now)
            old_b = analysis.analyze(w15)
            same = json.dumps(old_a, sort_keys=True, default=str) == json.dumps(old_b, sort_keys=True, default=str)
            result["old_api_identical" if same and not any(k.startswith("analyst_") for k in old_b) else "old_api_changed"] += 1
    # Veri kalitesi eşikleri
    q = engine.quality
    result["quality_rules_ok"] = (q(400) == "IYI" and q(399) == "UYARI" and q(250) == "UYARI" and q(249) == "GECERSIZ")
    short = engine.analyze_analyst([{"time": i * M15, "open": 1, "high": 1.1, "low": 0.9, "close": 1, "volume": 1} for i in range(249)], [], [])
    result["no_score_when_15m_invalid"] = short["analyst_score"] is None and short["calibrated_success_probability"] is None
    out.mkdir(parents=True, exist_ok=True)
    (out / "checks.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------- değerlendirme
def load_table(path: Path):
    import numpy as np
    import pandas as pd
    df = pd.read_json(path, lines=True, convert_dates=False, dtype=False)
    df["period"] = None
    for name, (first, last) in PERIODS.items():
        mask = (df["time"] >= base.month_start(first)) & (df["time"] < base.month_end(last))
        df.loc[mask, "period"] = name
    comps = np.array(df["c"].tolist())
    for model in ("A", "B"):
        w = np.array([engine.WEIGHTS[model][name] for name in engine.COMPONENTS]) / 100
        raw = comps @ w
        df[f"{model}_raw"] = raw
        df[f"{model}_score"] = np.round(100 * np.abs(raw)).astype(int)
        df[f"{model}_dir"] = np.where(raw > 0, "L", np.where(raw < 0, "S", ""))
        df[f"{model}_signal"] = (df[f"{model}_score"] >= engine.DIRECTION_MIN_SCORE) & (df[f"{model}_dir"] != "")
    diff = df["old_long"] - df["old_short"]
    df["OLD_dir"] = np.where(diff > 0, "L", np.where(diff < 0, "S", ""))
    df["OLD_score"] = df["old_conf"]
    df["OLD_signal"] = df["old_dir"].isin(["LONG", "SHORT"])
    # Ortak nokta kümesi: her modelin bir yönü var ve iki yönün de ana ufuk sonucu biliniyor.
    keep = (df["A_dir"] != "") & (df["B_dir"] != "") & (df["OLD_dir"] != "") & df["L96"].notna() & df["S96"].notna()
    df = df[keep].reset_index(drop=True)
    for model in MODELS:
        outcome = [lv if d == "L" else sv for d, lv, sv in zip(df[f"{model}_dir"], df["L96"], df["S96"])]
        df[f"{model}_y"] = [CLASS_INDEX[v[0]] for v in outcome]
        df[f"{model}_r"] = [v[1] for v in outcome]
    return df


def features(df, model: str):
    import numpy as np
    s = df[f"{model}_score"].to_numpy(float) / 100
    is_long = (df[f"{model}_dir"] == "L").to_numpy(float)
    return np.column_stack([s, s * s, is_long, s * is_long])


def fit_calibrator(df, model: str):
    from sklearn.linear_model import LogisticRegression
    calibrator = LogisticRegression(C=CALIB_C, max_iter=2000)
    calibrator.fit(features(df, model), df[f"{model}_y"].to_numpy())
    assert list(calibrator.classes_) == [0, 1, 2], "eğitimde üç sınıfın hepsi bulunmalı"
    return calibrator


def brier_rows(prob, y):
    import numpy as np
    onehot = np.zeros_like(prob)
    onehot[np.arange(len(y)), y] = 1
    return ((prob - onehot) ** 2).sum(axis=1)


class Blocks:
    """Gün bazlı hareketli blok bootstrap. Aynı dizinler tüm istatistiklerde kullanılır (eşli karşılaştırma)."""

    def __init__(self, days: list[str], seed: int = BOOT_SEED, b: int = BOOT_B, block: int = BOOT_BLOCK_DAYS):
        import numpy as np
        self.days = sorted(set(days))
        self.position = {day: i for i, day in enumerate(self.days)}
        count = len(self.days)
        rng = np.random.default_rng(seed)
        k = math.ceil(count / block)
        starts = rng.integers(0, count, size=(b, k))
        self.idx = ((starts[:, :, None] + np.arange(block)) % count).reshape(b, -1)[:, :count]

    def day_ids(self, day_series):
        import numpy as np
        return np.array([self.position[d] for d in day_series])

    def ratio(self, day_ids, numerator, denominator=None):
        """Σpay / Σpayda için nokta tahmini ve %95 aralık (gün toplamları üzerinden)."""
        import numpy as np
        count = len(self.days)
        num = np.bincount(day_ids, weights=numerator, minlength=count)
        den = np.bincount(day_ids, weights=np.ones(len(day_ids)) if denominator is None else denominator, minlength=count)
        point = num.sum() / den.sum() if den.sum() else float("nan")
        boot_den = den[self.idx].sum(axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            boot = num[self.idx].sum(axis=1) / boot_den
        boot = boot[np.isfinite(boot)]
        lo, hi = (np.percentile(boot, [2.5, 97.5]) if len(boot) else (float("nan"), float("nan")))
        return float(point), float(lo), float(hi), boot

    def spearman(self, day_ids, x, y):
        import numpy as np
        from scipy.stats import spearmanr
        point = float(spearmanr(x, y).statistic)
        rows_by_day = [np.flatnonzero(day_ids == d) for d in range(len(self.days))]
        values = []
        for row in self.idx:
            pick = np.concatenate([rows_by_day[d] for d in row])
            if len(pick) > 2:
                values.append(spearmanr(x[pick], y[pick]).statistic)
        lo, hi = np.nanpercentile(values, [2.5, 97.5])
        return point, float(lo), float(hi)


def band_edges(model: str) -> list[tuple[int, int]]:
    if model == "OLD":
        return [(50, 59), (60, 69), (70, 79), (80, 89), (90, 100)]
    return [(20, 29), (30, 39), (40, 49), (50, 59), (60, 69), (70, 79), (80, 89), (90, 100)]


def model_tables(df, model: str, calibrator, blocks: Blocks) -> dict[str, Any]:
    """Sinyal noktaları (skor eşiğini geçen) için bant × yön, yön, koşul ve ekonomik avantaj tabloları."""
    import numpy as np
    sig = df[df[f"{model}_signal"]].reset_index(drop=True)
    prob = calibrator.predict_proba(features(sig, model)) if len(sig) else np.zeros((0, 3))
    y, r = sig[f"{model}_y"].to_numpy(), sig[f"{model}_r"].to_numpy(float)
    ids = blocks.day_ids(sig["day"]) if len(sig) else np.array([], dtype=int)
    score, direction = sig[f"{model}_score"].to_numpy(), sig[f"{model}_dir"].to_numpy()

    def describe(mask) -> dict[str, Any]:
        n = int(mask.sum())
        days = int(sig.loc[mask, "day"].nunique()) if n else 0
        row: dict[str, Any] = {"n": n, "days": days, "enough_sample": n >= base.MIN_SIGNALS and days >= base.MIN_DAYS}
        if not n:
            return row
        for k, name in enumerate(("win", "loss", "timeout")):
            row[f"{name}_rate"] = float((y[mask] == k).mean())
            row[f"p_{name}"] = float(prob[mask, k].mean())
        win_ci = base.wilson(int((y[mask] == 0).sum()), n)
        row["win_wilson95"] = list(win_ci) if win_ci else None
        point, lo, hi, _ = blocks.ratio(ids[mask], r[mask])
        row.update(avg_r=point, avg_r_ci=[lo, hi], total_r=float(r[mask].sum()),
                   win_block_ci=list(blocks.ratio(ids[mask], (y[mask] == 0).astype(float))[1:3]))
        return row

    bands = []
    for low, high in band_edges(model):
        for d, label in (("L", "LONG"), ("S", "SHORT")):
            mask = (score >= low) & (score <= high) & (direction == d)
            bands.append({"band": f"{low}-{high}", "direction": label, **describe(mask)})
    by_direction = {label: describe(direction == d) for d, label in (("L", "LONG"), ("S", "SHORT"))}
    regimes = {name: describe((sig["regime"] == name).to_numpy()) for name in sorted(sig["regime"].unique())} if len(sig) else {}
    overall = describe(np.ones(len(sig), dtype=bool)) if len(sig) else {"n": 0}
    overall["economic_edge"] = bool(overall.get("n") and overall["avg_r"] > 0 and overall["avg_r_ci"][0] > 0)
    for row in bands:
        row["economic_edge"] = bool(row["n"] and row["avg_r"] > 0 and row["avg_r_ci"][0] > 0)
    return {"signals": len(sig), "bands": bands, "by_direction": by_direction, "regimes": regimes, "overall": overall}


def reliability(prob, y, bins: int = 10) -> list[dict[str, Any]]:
    import numpy as np
    out = []
    for k, name in enumerate(CLASSES):
        edges = np.unique(np.quantile(prob[:, k], np.linspace(0, 1, bins + 1)))
        which = np.clip(np.searchsorted(edges, prob[:, k], side="right") - 1, 0, len(edges) - 2)
        for b in range(len(edges) - 1):
            mask = which == b
            if mask.any():
                out.append({"class": name, "bin": b, "n": int(mask.sum()), "predicted": float(prob[mask, k].mean()),
                            "observed": float((y[mask] == k).mean())})
    return out


def period_metrics(df, model: str, calibrator, constant, blocks: Blocks) -> dict[str, Any]:
    import numpy as np
    prob = calibrator.predict_proba(features(df, model))
    y = df[f"{model}_y"].to_numpy()
    loss_model = brier_rows(prob, y)
    loss_const = brier_rows(np.tile(constant, (len(y), 1)), y)
    ids = blocks.day_ids(df["day"])
    delta, lo, hi, boot = blocks.ratio(ids, loss_const - loss_model)
    rho, rlo, rhi = blocks.spearman(ids, df[f"{model}_score"].to_numpy(float), df[f"{model}_r"].to_numpy(float))
    return {"n": int(len(y)), "days": int(df["day"].nunique()), "brier": float(loss_model.mean()),
            "brier_constant": float(loss_const.mean()), "delta_brier": delta, "delta_brier_ci": [lo, hi],
            "beats_constant": bool(lo > 0), "spearman_score_r": rho, "spearman_ci": [rlo, rhi],
            "reliability": reliability(prob, y), "_boot": boot}


def evaluate(table: Path, out: Path) -> dict[str, Any]:
    import numpy as np
    df = load_table(table)
    parts = {name: df[df["period"] == name].reset_index(drop=True) for name in PERIODS}
    train, valid = parts["egitim"], parts["dogrulama"]
    calibrators, constants = {}, {}
    for model in MODELS:
        calibrators[model] = fit_calibrator(train, model)
        constants[model] = np.bincount(train[f"{model}_y"], minlength=3) / len(train)
    vblocks = Blocks(valid["day"].tolist())
    validation = {m: period_metrics(valid, m, calibrators[m], constants[m], vblocks) for m in MODELS}
    diff = validation["A"]["_boot"] - validation["B"]["_boot"]
    ab_lo, ab_hi = (float(v) for v in np.percentile(diff, [2.5, 97.5]))
    passing = [m for m in ("A", "B") if validation[m]["beats_constant"]]
    if not passing:
        selected, reason = None, "Ne A ne B doğrulamada sabit taban modelini güvenilir biçimde geçemedi (ΔBrier alt sınırı ≤ 0)."
    elif len(passing) == 1:
        selected, reason = passing[0], f"Yalnız {passing[0]} sabit tabanı geçti."
    elif ab_lo > 0:
        selected, reason = "A", "İkisi de geçti; A'nın ΔBrier üstünlüğü bootstrap ile anlamlı."
    elif ab_hi < 0:
        selected, reason = "B", "İkisi de geçti; B'nin ΔBrier üstünlüğü bootstrap ile anlamlı."
    else:
        selected, reason = "A", "İkisi de geçti ama fark anlamlı değil (eşit) → önceden kaydedilen kurala göre A."
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "engine_version": engine.ENGINE_VERSION,
        "rules": {"bootstrap": {"B": BOOT_B, "block_days": BOOT_BLOCK_DAYS, "seed": BOOT_SEED}, "calibration_C": CALIB_C,
                  "direction_min_score": engine.DIRECTION_MIN_SCORE, "weights": engine.WEIGHTS, "periods": PERIODS},
        "points": {name: int(len(frame)) for name, frame in parts.items()},
        "constants": {m: constants[m].tolist() for m in MODELS},
        "validation": {m: {k: v for k, v in validation[m].items() if k != "_boot"} for m in MODELS},
        "a_minus_b_delta_brier_ci": [ab_lo, ab_hi],
        "selection": {"selected": selected, "reason": reason, "passing": passing},
        "tables": {"egitim": {}, "dogrulama": {}},
    }
    tblocks = Blocks(train["day"].tolist())
    for m in MODELS:
        report["tables"]["egitim"][m] = model_tables(train, m, calibrators[m], tblocks)
        report["tables"]["dogrulama"][m] = model_tables(valid, m, calibrators[m], vblocks)
    if selected:   # test dönemi yalnız bir model seçildiyse ve bir kez açılır
        test = parts["test"]
        sblocks = Blocks(test["day"].tolist())
        report["test"] = {m: {k: v for k, v in period_metrics(test, m, calibrators[m], constants[m], sblocks).items() if k != "_boot"}
                          for m in (selected, "OLD")}
        report["tables"]["test"] = {m: model_tables(test, m, calibrators[m], sblocks) for m in (selected, "OLD")}
    out.mkdir(parents=True, exist_ok=True)
    (out / "ab-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "ab-report.md").write_text(ab_markdown(report), encoding="utf-8")
    for m in MODELS:
        (out / f"reliability-dogrulama-{m}.svg").write_text(reliability_svg(validation[m]["reliability"], f"{m} · doğrulama"), encoding="utf-8")
    return report


# ---------------------------------------------------------------- zaman aşımı araştırması (eski sistem sinyalleri)
def timeouts(table: Path, out: Path) -> dict[str, Any]:
    import numpy as np
    import pandas as pd
    df = load_table(table)
    sig = df[df["OLD_signal"]].reset_index(drop=True)
    d = sig["OLD_dir"].to_numpy()
    sig["risk_atr"] = np.where(d == "L", sig["L_risk_atr"], sig["S_risk_atr"])
    sig["risk_pct"] = np.where(d == "L", sig["L_risk_pct"], sig["S_risk_pct"])
    sig["risk_vs_range24"] = sig["risk_pct"] / sig["range24_pct"]
    sig["stop_type"] = np.where(sig["risk_atr"] <= 1.5 * 1.02 + 0.05, "1,5 ATR tabanı", "80 mum dip/tepe (yapısal)")
    sig["timeout"] = sig["OLD_y"] == 2
    sig["win"] = sig["OLD_y"] == 0
    sig["band"] = pd.cut(sig["OLD_score"], [49, 59, 69, 79, 89, 100], labels=["50-59", "60-69", "70-79", "80-89", "90-100"])
    sig["risk_range_q"] = pd.qcut(sig["risk_vs_range24"], 5, labels=["Q1 (dar)", "Q2", "Q3", "Q4", "Q5 (geniş)"])

    def group(key: str) -> list[dict[str, Any]]:
        g = sig.groupby(key, observed=True)
        return [{"group": str(name), "n": int(len(x)), "timeout_rate": float(x["timeout"].mean()), "win_rate": float(x["win"].mean()),
                 "median_risk_atr": float(x["risk_atr"].median()), "median_risk_vs_range24": float(x["risk_vs_range24"].median()),
                 "avg_r": float(x["OLD_r"].mean())} for name, x in g]

    q = sig["risk_atr"].quantile([0.1, 0.25, 0.5, 0.75, 0.9]).to_dict()
    sens = []
    complete = sig.copy()
    for h in RESEARCH_HORIZONS:
        complete = complete[[(v is not None and v == v) for v in np.where(d[complete.index] == "L", complete[f"L{h}"], complete[f"S{h}"])]]
    cd = complete["OLD_dir"].to_numpy()
    for h in RESEARCH_HORIZONS:
        res = [lv if x == "L" else sv for x, lv, sv in zip(cd, complete[f"L{h}"], complete[f"S{h}"])]
        codes = np.array([v[0] for v in res])
        rs = np.array([v[1] for v in res], dtype=float)
        sens.append({"horizon_hours": h // 4, "n": int(len(res)), "win_rate": float((codes == "W").mean()),
                     "loss_rate": float((codes == "L").mean()), "timeout_rate": float((codes == "T").mean()), "avg_r": float(rs.mean())})
    report = {
        "note": "Araştırma: eski sistemin 4 saatlik ızgaradaki sinyalleri. Stop mekanizması ve ana kazanma tanımı DEĞİŞMEDİ; "
                "farklı ufuklar yalnız duyarlılık içindir.",
        "signals": int(len(sig)), "timeout_rate": float(sig["timeout"].mean()),
        "risk_atr_quantiles": {f"q{int(k * 100)}": float(v) for k, v in q.items()},
        "mean_risk_atr": float(sig["risk_atr"].mean()), "mean_risk_pct": float(sig["risk_pct"].mean()),
        "median_range24_pct": float(sig["range24_pct"].median()), "median_risk_vs_range24": float(sig["risk_vs_range24"].median()),
        "by_direction": group("OLD_dir"), "by_stop_type": group("stop_type"), "by_band": group("band"),
        "by_regime": group("regime"), "by_coin": group("symbol"), "by_risk_vs_range24": group("risk_range_q"),
        "horizon_sensitivity": sens,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "timeouts.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "timeouts.md").write_text(timeouts_markdown(report), encoding="utf-8")
    return report


# ---------------------------------------------------------------- rapor biçimi
def pct(value) -> str:
    return "—" if value is None or value != value else f"%{value * 100:.1f}"


def ci(pair, scale: float = 1.0, fmt: str = "{:+.3f}") -> str:
    return "—" if not pair else f"{fmt.format(pair[0] * scale)} … {fmt.format(pair[1] * scale)}"


def band_lines(tables: dict[str, Any]) -> list[str]:
    lines = ["| Bant | Yön | n | Gün | Kazanç | Kayıp | Zaman aşımı | P(kazanç) kalibre | Ort. R | R %95 (blok) | Toplam R | Gösterilebilir |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in tables["bands"]:
        if not row["n"]:
            continue
        ok = row["enough_sample"]
        lines.append(f"| {row['band']} | {row['direction']} | {row['n']} | {row['days']} | {pct(row['win_rate'])} | {pct(row['loss_rate'])} | "
                     f"{pct(row['timeout_rate'])} | {pct(row['p_win']) if ok else 'yetersiz veri'} | {row['avg_r']:+.3f} | {ci(row['avg_r_ci'])} | "
                     f"{row['total_r']:+.1f} | {'evet' if ok else 'hayır'} |")
    return lines


def ab_markdown(report: dict[str, Any]) -> str:
    v = report["validation"]
    sel = report["selection"]
    lines = [f"# Analist motoru A/B – doğrulama raporu ({report['engine_version']})", "",
             f"Oluşturma: {report['created_at']}. Nokta sayısı: eğitim {report['points']['egitim']}, doğrulama {report['points']['dogrulama']}, "
             f"test {report['points']['test']}. Kurallar `analyst_ab_cli.py` başındaki önceden kayıtlı listededir.", "",
             "## Ana ölçüt: çok sınıflı Brier (doğrulama)", "",
             "| Model | Brier | Sabit taban | ΔBrier (taban − model) | %95 (7 günlük blok) | Tabandan iyi | Spearman(skor, R) | %95 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for m in MODELS:
        row = v[m]
        lines.append(f"| {m} | {row['brier']:.4f} | {row['brier_constant']:.4f} | {row['delta_brier']:+.5f} | {ci(row['delta_brier_ci'], fmt='{:+.5f}')} | "
                     f"{'EVET' if row['beats_constant'] else 'hayır'} | {row['spearman_score_r']:+.4f} | {ci(row['spearman_ci'], fmt='{:+.4f}')} |")
    lines += ["", f"A − B ΔBrier farkı %95: {ci(report['a_minus_b_delta_brier_ci'], fmt='{:+.5f}')}", "",
              f"**Seçim:** {sel['selected'] or 'MODEL SEÇİLMEDİ'} — {sel['reason']}", ""]
    for period in ("dogrulama", "egitim"):
        for m in MODELS:
            t = report["tables"][period][m]
            o = t["overall"]
            if not o.get("n"):
                continue
            lines += [f"## {m} · {period} · {t['signals']} sinyal", "",
                      f"Genel: kazanç {pct(o['win_rate'])}, kayıp {pct(o['loss_rate'])}, zaman aşımı {pct(o['timeout_rate'])}, "
                      f"ort. R {o['avg_r']:+.4f} (%95 {ci(o['avg_r_ci'], fmt='{:+.4f}')}) → ekonomik avantaj: {'VAR' if o['economic_edge'] else 'YOK'}", ""]
            lines += band_lines(t)
            lines += ["", "| Yön | n | Kazanç | Zaman aşımı | Ort. R | %95 |", "| --- | --- | --- | --- | --- | --- |"]
            for name, row in t["by_direction"].items():
                if row["n"]:
                    lines.append(f"| {name} | {row['n']} | {pct(row['win_rate'])} | {pct(row['timeout_rate'])} | {row['avg_r']:+.4f} | {ci(row['avg_r_ci'], fmt='{:+.4f}')} |")
            if period == "dogrulama":
                lines += ["", "| Piyasa koşulu | n | Kazanç | Zaman aşımı | Ort. R | %95 |", "| --- | --- | --- | --- | --- | --- |"]
                for name, row in t["regimes"].items():
                    if row["n"]:
                        lines.append(f"| {name} | {row['n']} | {pct(row['win_rate'])} | {pct(row['timeout_rate'])} | {row['avg_r']:+.4f} | {ci(row['avg_r_ci'], fmt='{:+.4f}')} |")
            lines.append("")
    if "test" in report:
        lines += ["## TEST dönemi (bir kez açıldı, donmuş kalibratör)", "",
                  "| Model | Brier | Sabit taban | ΔBrier | %95 | Tabandan iyi | Spearman | %95 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for m, row in report["test"].items():
            lines.append(f"| {m} | {row['brier']:.4f} | {row['brier_constant']:.4f} | {row['delta_brier']:+.5f} | {ci(row['delta_brier_ci'], fmt='{:+.5f}')} | "
                         f"{'EVET' if row['beats_constant'] else 'hayır'} | {row['spearman_score_r']:+.4f} | {ci(row['spearman_ci'], fmt='{:+.4f}')} |")
        for m, t in report["tables"]["test"].items():
            o = t["overall"]
            lines += ["", f"### {m} · test · {t['signals']} sinyal", "",
                      f"Ort. R {o['avg_r']:+.4f} (%95 {ci(o['avg_r_ci'], fmt='{:+.4f}')}) → ekonomik avantaj: {'VAR' if o['economic_edge'] else 'YOK'}", ""]
            lines += band_lines(t)
    else:
        lines += ["## TEST dönemi", "", "Açılmadı: önceden kaydedilen kurala göre test yalnız bir model seçildiğinde açılır."]
    return "\n".join(lines) + "\n"


def timeouts_markdown(r: dict[str, Any]) -> str:
    def table(rows, title):
        out = [f"### {title}", "", "| Grup | n | Zaman aşımı | Kazanç | Medyan stop (ATR) | Medyan stop / 24s aralık | Ort. R |",
               "| --- | --- | --- | --- | --- | --- | --- |"]
        out += [f"| {x['group']} | {x['n']} | {pct(x['timeout_rate'])} | {pct(x['win_rate'])} | {x['median_risk_atr']:.2f} | "
                f"{x['median_risk_vs_range24']:.2f} | {x['avg_r']:+.3f} |" for x in rows]
        return out + [""]
    q = r["risk_atr_quantiles"]
    lines = ["# Zaman aşımı araştırması – eski sistem", "", r["note"], "",
             f"Sinyal: {r['signals']} · zaman aşımı {pct(r['timeout_rate'])}", "",
             f"Stop mesafesi (15m ATR katı): ortalama {r['mean_risk_atr']:.2f}; yüzdelikler 10/25/50/75/90 = "
             f"{q['q10']:.2f} / {q['q25']:.2f} / {q['q50']:.2f} / {q['q75']:.2f} / {q['q90']:.2f}", "",
             f"Stop mesafesi fiyatın ortalama {pct(r['mean_risk_pct'])}'i; son 4 günün medyan 24 saatlik aralığı {pct(r['median_range24_pct'])}; "
             f"stop / 24s aralık medyanı {r['median_risk_vs_range24']:.2f}. 1R hedef için fiyatın stop kadar ters yönde de gitmesi gerekir.", ""]
    lines += table(r["by_stop_type"], "Stop türüne göre")
    lines += table(r["by_risk_vs_range24"], "Stop / 24 saatlik aralık (beşte birlik dilimler)")
    lines += table(r["by_direction"], "Yöne göre")
    lines += table(r["by_band"], "Eski skor bandına göre")
    lines += table(r["by_regime"], "Piyasa koşuluna göre")
    lines += table(r["by_coin"], "Coin'e göre")
    lines += ["### Süre duyarlılığı (yalnız araştırma; ana tanım 24 saat olarak kalır)", "",
              "| Süre | n | Kazanç | Kayıp | Zaman aşımı | Ort. R |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {x['horizon_hours']} saat | {x['n']} | {pct(x['win_rate'])} | {pct(x['loss_rate'])} | {pct(x['timeout_rate'])} | {x['avg_r']:+.3f} |"
              for x in r["horizon_sensitivity"]]
    return "\n".join(lines) + "\n"


def reliability_svg(rows: list[dict[str, Any]], title: str) -> str:
    size, pad = 380, 48
    colors = {"KAZANC": "#16a34a", "KAYIP": "#dc2626", "ZAMAN_ASIMI": "#6b7280"}
    scale = lambda value: pad + value * (size - 2 * pad)  # noqa: E731
    marks = []
    for name, color in colors.items():
        pts = [r for r in rows if r["class"] == name]
        path = " ".join(f"{scale(p['predicted']):.1f},{size - scale(p['observed']):.1f}" for p in pts)
        marks.append(f"<polyline points='{path}' fill='none' stroke='{color}' stroke-width='1.5'/>")
        marks += [f"<circle cx='{scale(p['predicted']):.1f}' cy='{size - scale(p['observed']):.1f}' r='3' fill='{color}'/>" for p in pts]
    legend = "".join(f"<text x='{pad + 110 * i}' y='{size - 8}' font-size='10' fill='{c}'>{n}</text>" for i, (n, c) in enumerate(colors.items()))
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' font-family='sans-serif'><rect width='{size}' height='{size}' fill='white'/>"
            f"<text x='{pad}' y='24' font-size='13' font-weight='600'>{title} · güvenilirlik (tahmin → gerçek)</text>"
            f"<line x1='{pad}' y1='{size - pad}' x2='{size - pad}' y2='{pad}' stroke='#999' stroke-dasharray='4 4'/>"
            f"<line x1='{pad}' y1='{size - pad}' x2='{size - pad}' y2='{size - pad}' stroke='#333'/><line x1='{pad}' y1='{size - pad}' x2='{pad}' y2='{pad}' stroke='#333'/>"
            f"{''.join(marks)}{legend}</svg>")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "checks"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--stage", choices=sorted(STAGES), required=True)
        cmd.add_argument("--data", type=Path, required=True)
        cmd.add_argument("--out", type=Path, required=True)
        cmd.add_argument("--workers", type=int, default=2)
    for name in ("evaluate", "timeouts"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--table", type=Path, required=True)
        cmd.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    if args.out.resolve().is_relative_to(repo):
        parser.error("Rapor klasörü repo dışında olmalı.")
    if args.command == "build":
        summary = build(args.stage, args.data.resolve(), args.out.resolve(), args.workers)
        print(json.dumps({k: summary[k] for k in ("stage", "runtime_seconds", "analysis_unchanged")}
                         | {"decisions": sum(s["decisions"] for s in summary["symbols"])}, indent=2))
    elif args.command == "checks":
        print(json.dumps(checks(args.stage, args.data.resolve(), args.out.resolve()), indent=2))
    elif args.command == "evaluate":
        report = evaluate(args.table.resolve(), args.out.resolve())
        print(json.dumps(report["selection"], indent=2, ensure_ascii=False))
    else:
        report = timeouts(args.table.resolve(), args.out.resolve())
        print(json.dumps({"signals": report["signals"], "timeout_rate": report["timeout_rate"]}, indent=2))


if __name__ == "__main__":
    main()
