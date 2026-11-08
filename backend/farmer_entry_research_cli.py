"""KAIS FARMER entry research (C14). Research only: no network, no trading API, no repository output, no Farmer execution code.

  frequency --data-2025 D --data-2026 D     setup COUNTS per period (no outcome is computed or printed)
  check     --data-2025 D --data-2026 D --output O    implementation / sanity check on TRAIN + VALID only; OOS prices are never loaded
  run       --data-2025 D --data-2026 D --output O --protocol-hash H [--cache-2025 C --cache-2026 C]
                                            the one pre-registered test (TRAIN / VALID / OOS) + baselines + decision

`run` refuses to start unless the protocol hash you pass equals the hash of the frozen protocol in the module.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app import backtest_data as bd
from app import farmer_entry_research as er

REPO = Path(__file__).resolve().parents[1]
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
BLOCKS = {"2025": ("2025-03-01", "2025-10-01"), "2026": ("2026-03-01", "2026-10-01")}      # the archives that exist (see INTERPRETATION)
PERIODS = ("TRAIN", "VALID", "OOS")
SEED = 20260714


def day(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def month(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


def outside_repo(path: Path) -> Path:
    path = path.resolve()
    if path.is_relative_to(REPO):
        raise SystemExit("Data and results must stay outside the repository")
    return path


def period_of(ts: int) -> str | None:
    for name, (lo, hi) in (("TRAIN", er.PROTOCOL.train), ("VALID", er.PROTOCOL.valid), ("OOS", er.PROTOCOL.oos)):
        if day(lo) <= ts < day(hi):
            return name
    return None


def load_block(root: Path, key: str, end_override: int | None = None) -> dict:
    """Complete 15m grid for contract and mark candles, per symbol. Any gap stops the run (no fill, no interpolation)."""
    root = outside_repo(root)
    start, end = day(BLOCKS[key][0]), day(BLOCKS[key][1])
    if end_override is not None:
        end = min(end, end_override)
    ds = bd.load_dataset(root, root / "current-metadata.json", start, end)
    n = (end - start) // er.BAR
    out: dict = {"key": key, "start": start, "end": end, "n": n, "sym": {}}
    for sym in SYMBOLS:
        crow = [r for r in ds.frames[sym]["15m"].rows if start <= r["time"] < end]
        mrow = [r for r in ds.marks[sym].rows if start <= r["time"] < end]
        for rows, label in ((crow, "contract"), (mrow, "mark")):
            if len(rows) != n or any(r["time"] != start + er.BAR * i for i, r in enumerate(rows)):
                raise SystemExit(f"{sym} {label} 15m series is not complete on the grid")
        out["sym"][sym] = {"o": np.array([r["open"] for r in crow]), "h": np.array([r["high"] for r in crow]), "l": np.array([r["low"] for r in crow]),
                           "c": np.array([r["close"] for r in crow]), "mo": np.array([r["open"] for r in mrow]), "mh": np.array([r["high"] for r in mrow]),
                           "ml": np.array([r["low"] for r in mrow]),
                           "fund_t": np.array([e["time"] for e in ds.funding[sym]]), "fund_r": np.array([e["rate"] for e in ds.funding[sym]])}
    return out


def detect(block: dict) -> dict[str, list[dict]]:
    cfg = er.PROTOCOL
    res = {}
    for sym, d in block["sym"].items():
        atr = er.wilder_atr(d["h"], d["l"], d["c"], cfg.atr_period)
        d["atr"] = atr
        res[sym] = er.detect_setups(d["o"], d["h"], d["l"], d["c"], atr)
    return res


def frequency(blocks: dict) -> dict:
    """Counts only: how many breakouts / entries each period contains. No outcome is looked at."""
    table: dict = {p: Counter() for p in PERIODS}
    days: dict = {p: set() for p in PERIODS}
    for block in blocks.values():
        for sym, events in detect(block).items():
            for e in events:
                ts = block["start"] + e["breakout_idx"] * er.BAR
                p = period_of(ts)
                if p is None:
                    continue
                table[p]["breakouts"] += 1
                table[p][f"state_{e['state']}"] += 1
                if e["state"] == er.ENTRY:
                    table[p]["entries"] += 1
                    table[p][f"entries_{e['side']}"] += 1
                    table[p][f"entries_{sym}"] += 1
                    table[p][f"entries_month_{month(block['start'] + e['entry_idx'] * er.BAR)}"] += 1
    for block in blocks.values():
        for p in PERIODS:
            lo, hi = day(getattr(er.PROTOCOL, p.lower())[0]), day(getattr(er.PROTOCOL, p.lower())[1])
            covered = max(0, min(hi, block["end"]) - max(lo, block["start"]))
            days[p].add(round(covered / 86400, 2))
    return {p: dict(table[p]) | {"covered_days_per_block": sorted(days[p])} for p in PERIODS}


def signals(blocks: dict) -> tuple[list[dict], dict]:
    """Every ENTRY event with both direction outcomes (actual and opposite) and cost components in R."""
    rows: list[dict] = []
    funnel: dict = {p: Counter() for p in PERIODS}
    for block in blocks.values():
        start = block["start"]
        for sym, events in detect(block).items():
            d = block["sym"][sym]
            for e in events:
                bp = period_of(start + e["breakout_idx"] * er.BAR)
                if bp:
                    funnel[bp]["breakouts"] += 1
                    funnel[bp][e["state"]] += 1
                if e["state"] != er.ENTRY or e["entry_idx"] >= block["n"]:
                    continue
                ts = start + e["entry_idx"] * er.BAR
                p = period_of(ts)
                if p is None:
                    continue
                price, atr_e = float(d["o"][e["entry_idx"]]), e["entry_atr"]
                lab = {s: er.label_outcome(d["mh"], d["ml"], e["entry_idx"], price, atr_e, s) for s in ("LONG", "SHORT")}
                act, opp = lab[e["side"]], lab["SHORT" if e["side"] == "LONG" else "LONG"]
                if act["result"] == "UNRESOLVED":
                    funnel[p]["unresolved_excluded"] += 1
                    continue
                exit_end = start + (act["exit_idx"] + 1) * er.BAR
                ft, fr = d["fund_t"], d["fund_r"]
                sel = (ft > ts) & (ft <= exit_end)
                bars = ((ft[sel] - start) // er.BAR).astype(int)
                fsum = float((fr[sel] * d["mo"][bars] / price).sum())
                c2, c5 = er.cost_r(price, atr_e, e["side"], fsum, 2.0), er.cost_r(price, atr_e, e["side"], fsum, 5.0)
                rows.append({"period": p, "symbol": sym, "side": e["side"], "entry_ts": ts, "day": ts // 86400, "exit_ts": exit_end, "entry_price": price,
                             "atr_entry": atr_e, "success": act["result"] == "TARGET", "opposite_success": opp["result"] == "TARGET",
                             "long_success": lab["LONG"]["result"] == "TARGET", "short_success": lab["SHORT"]["result"] == "TARGET",
                             "ambiguous": act["ambiguous"], "hold_bars": act["exit_idx"] - e["entry_idx"] + 1,
                             "gross_r": 1.0 if act["result"] == "TARGET" else -1.0, "commission_r": c2["commission_r"], "funding_r": c2["funding_r"],
                             "slip2_r": c2["slippage_r"], "slip5_r": c5["slippage_r"], "range_width_atr": e["range_width_atr"]})
                funnel[p]["resolved_entries"] += 1
    return rows, {p: dict(funnel[p]) for p in PERIODS}


def summarize(rows: list[dict], rng: np.random.Generator) -> dict:
    if not rows:
        return {"n": 0}
    out = er.edge_stats([r["success"] for r in rows], er.PROTOCOL.confidence_level)
    out["boot_ci"] = er.day_bootstrap_ci([r["success"] for r in rows], [r["day"] for r in rows], rng)
    ordered = sorted(rows, key=lambda r: r["exit_ts"])
    out["max_drawdown_r"] = er.max_drawdown_r([r["gross_r"] for r in ordered])
    n = len(rows)
    g, cm, fd, s2, s5 = (sum(r[k] for r in rows) for k in ("gross_r", "commission_r", "funding_r", "slip2_r", "slip5_r"))
    out["layers_r_total"] = {"gross": g, "commission": -cm, "funding": -fd, "slippage_2bps": -s2, "slippage_5bps": -s5,
                             "L1_gross": g, "L2_gross_plus_funding": g - fd, "L3_fee_adjusted": g - fd - cm, "L4_realistic_2bps": g - fd - cm - s2,
                             "L5_stress_5bps": g - fd - cm - s5}
    out["layers_r_per_trade"] = {k: v / n for k, v in out["layers_r_total"].items() if k.startswith("L")}
    out["ambiguous_same_candle"] = int(sum(r["ambiguous"] for r in rows))
    out["median_hold_bars"] = float(np.median([r["hold_bars"] for r in rows]))
    return out


def baselines(rows: list[dict]) -> dict:
    """A: random direction (exact expectation on the SAME signals). C: reverse direction. Both diagnostic only."""
    out = {}
    for p in (*PERIODS, "ALL"):
        sub = [r for r in rows if p == "ALL" or r["period"] == p]
        if not sub:
            continue
        out[p] = {"n": len(sub), "A_random_expected_rate": float(np.mean([(r["long_success"] + r["short_success"]) / 2 for r in sub])),
                  "C_reverse_rate": float(np.mean([r["opposite_success"] for r in sub])), "actual_rate": float(np.mean([r["success"] for r in sub]))}
    return out


def baseline_b(blocks: dict, caches: dict[str, Path]) -> dict:
    """B: the current Farmer entry (confirmed, min score 90) labelled with the same +/-2 ATR rule; one open signal per symbol."""
    from app import (
        backtest_farmer as bf,
    )
    res: dict = {p: [] for p in PERIODS}
    for key, block in blocks.items():
        cache = {s: json.loads((outside_repo(caches[key]) / f"candidates_{s}.json").read_text(encoding="utf-8")) for s in SYMBOLS}
        cand = bf.build_candidates(cache, block["start"], block["n"], ())
        busy: dict[str, int] = {}
        for d_idx, _neg, sym in bf.confirmed_table(cand, 25.0, 3.0, 2.0, 2.0, 90):
            row, d = cand.rows[sym][d_idx], block["sym"][sym]
            if d_idx >= block["n"] or d_idx < busy.get(sym, 0) or not row.get("atr"):
                continue
            ts = block["start"] + d_idx * er.BAR
            p = period_of(ts)
            lab = er.label_outcome(d["mh"], d["ml"], d_idx, float(d["o"][d_idx]), float(row["atr"]), row["direction"])
            if lab["result"] == "UNRESOLVED":
                continue
            busy[sym] = lab["exit_idx"] + 1
            if p:
                res[p].append(lab["result"] == "TARGET")
    return {p: er.edge_stats(v) for p, v in res.items() if v}


def decide(oos: dict, by_side: dict, by_symbol: dict, periods: dict) -> dict:
    cfg = er.PROTOCOL
    n = oos.get("n", 0)
    crit = {"N_ge_min": n >= cfg.minimum_oos_trades}
    if n:
        crit.update(win_rate_ge_55=oos["win_rate"] >= cfg.primary_success_rate, ci_low_gt_50=oos["ci_low"] > 0.5,
                    expectancy_gt_005=oos["expectancy_r"] > cfg.minimum_expectancy_r, pf_ge_110=oos["profit_factor"] >= cfg.minimum_profit_factor)
    pos_periods = sum(1 for p in PERIODS if periods.get(p, {}).get("n") and periods[p]["expectancy_r"] > 0)
    stability = pos_periods >= 2
    sides_bad = any(by_side.get(s, {}).get("n", 0) and by_side[s]["win_rate"] <= 0.5 for s in ("LONG", "SHORT"))
    symbols_ok = sum(1 for v in by_symbol.values() if v.get("n") and v["win_rate"] > 0.5)
    failure6 = sides_bad or symbols_ok < 2
    if not crit["N_ge_min"]:
        verdict = "INCONCLUSIVE"
    elif all(crit.values()) and stability and not failure6:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {"criteria": crit, "periods_with_positive_gross_expectancy": pos_periods, "stability_ok": stability, "failure6_subgroup_dependence": failure6,
            "symbols_with_win_rate_gt_50": symbols_ok, "verdict": verdict}


def run(a: argparse.Namespace, blocks: dict) -> None:
    if a.protocol_hash != er.protocol_hash():
        raise SystemExit("protocol hash mismatch: the frozen protocol is not the one you are running")
    out_dir = outside_repo(a.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)
    rows, funnel = signals(blocks)
    tables: dict = {"ALL": {}, **{p: {} for p in PERIODS}}
    for p in ("ALL", *PERIODS):
        sub = [r for r in rows if p == "ALL" or r["period"] == p]
        tables[p]["TOTAL"] = summarize(sub, rng)
        for s in ("LONG", "SHORT"):
            tables[p][s] = summarize([r for r in sub if r["side"] == s], rng)
        for sym in SYMBOLS:
            tables[p][sym] = summarize([r for r in sub if r["symbol"] == sym], rng)
    oos = tables["OOS"]
    report = {"protocol_hash": er.protocol_hash(), "protocol": er.PROTOCOL.__dict__ | {}, "interpretation": er.INTERPRETATION,
              "funnel": funnel, "tables": tables, "baselines": {"A_C_same_signals": baselines(rows)}, "frequency_per_month": {
                  p: {m: sum(1 for r in rows if r["period"] == p and month(r["entry_ts"]) == m) for m in sorted({month(r["entry_ts"]) for r in rows if r["period"] == p})}
                  for p in PERIODS},
              "decision": decide(oos["TOTAL"], {s: oos[s] for s in ("LONG", "SHORT")}, {s: oos[s] for s in SYMBOLS}, {p: tables[p]["TOTAL"] for p in PERIODS})}
    if a.cache_2025 and a.cache_2026:
        report["baselines"]["B_current_farmer_entry"] = baseline_b(blocks, {"2025": a.cache_2025, "2026": a.cache_2026})
    (out_dir / "c14_report.json").write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    keys = list(rows[0]) if rows else []
    with (out_dir / "c14_signals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({"decision": report["decision"], "oos": {k: oos["TOTAL"].get(k) for k in ("n", "win_rate", "ci_low", "ci_high", "expectancy_r", "profit_factor")}}, indent=1, default=float))


def check(blocks: dict) -> dict:
    """Implementation check on data that ends before the OOS period. Counts and invariants only: no success rate, no trade list."""
    cfg = er.PROTOCOL
    rep: dict = {p: {"breakout_structures": 0, "states": Counter(), "entries_LONG": 0, "entries_SHORT": 0, "entries_by_symbol": Counter()} for p in ("TRAIN", "VALID")}
    inv = Counter()
    labels = Counter()
    for block in blocks.values():
        start = block["start"]
        assert block["end"] <= day(cfg.oos[0]), "OOS prices must not be loaded in the check"
        for sym, d in block["sym"].items():
            atr = er.wilder_atr(d["h"], d["l"], d["c"], cfg.atr_period)
            events = er.detect_setups(d["o"], d["h"], d["l"], d["c"], atr)
            sup: list[int] = []
            ref = er.reference_detect(d["o"], d["h"], d["l"], d["c"], atr, suppressed=sup)
            keys = ("breakout_idx", "side", "state", "entry_idx", "confirm_idx", "retest_idx")
            inv["chain_events_main"] += len(events)
            inv["chain_mismatch_vs_reference"] += int([{k: e.get(k) for k in keys} for e in events] != [{k: e.get(k) for k in keys} for e in ref])
            inv["duplicate_candles_suppressed"] += sup[0]
            gaps = [b["breakout_idx"] - a_["breakout_idx"] for a_, b in itertools.pairwise(events)]
            inv["structures_closer_than_21_candles"] += int(any(g < cfg.range_window + 1 for g in gaps))
            for e in events:
                p = period_of(start + e["breakout_idx"] * er.BAR)
                if p:
                    rep[p]["breakout_structures"] += 1
                    rep[p]["states"][e["state"]] += 1
                if e["state"] != er.ENTRY:
                    continue
                pe = period_of(start + e["entry_idx"] * er.BAR)
                if pe:
                    rep[pe][f"entries_{e['side']}"] += 1
                    rep[pe]["entries_by_symbol"][sym] += 1
                inv["entries_checked"] += 1
                inv["rule_violations"] += len(er.verify_entry_event(e, d["o"], d["h"], d["l"], d["c"], atr))
                m, i = e["confirm_idx"], e["entry_idx"]
                inv["entry_not_at_open_after_confirmation"] += int(start + (m + 1) * er.BAR != start + i * er.BAR or i != m + 1)
                # look-ahead: the signal must be identical when the data stops right after the confirmation candle, and absent one candle earlier
                cut = m + 1
                part = er.detect_setups(d["o"][:cut], d["h"][:cut], d["l"][:cut], d["c"][:cut], er.wilder_atr(d["h"][:cut], d["l"][:cut], d["c"][:cut], cfg.atr_period))
                twin = next((x for x in part if x["breakout_idx"] == e["breakout_idx"]), None)
                inv["lookahead_signal_changed_with_prefix"] += int(not twin or any(twin.get(k) != e.get(k) for k in ("state", "entry_idx", "entry_atr")))
                part2 = er.detect_setups(d["o"][:m], d["h"][:m], d["l"][:m], d["c"][:m], er.wilder_atr(d["h"][:m], d["l"][:m], d["c"][:m], cfg.atr_period))
                twin2 = next((x for x in part2 if x["breakout_idx"] == e["breakout_idx"]), None)
                inv["lookahead_entry_known_before_confirmation"] += int(bool(twin2 and twin2["state"] == er.ENTRY))
                inv["atr_not_causal"] += int(not np.array_equal(er.wilder_atr(d["h"][:cut], d["l"][:cut], d["c"][:cut], cfg.atr_period)[-3:], atr[cut - 3:cut], equal_nan=True))
                if i >= block["n"]:
                    continue
                price, atr_e = float(d["o"][i]), e["entry_atr"]
                for side in ("LONG", "SHORT"):
                    a1 = er.label_outcome(d["mh"], d["ml"], i, price, atr_e, side)
                    a2 = er.reference_label(d["mh"], d["ml"], i, price, atr_e, side)
                    inv["labels_checked"] += 1
                    inv["label_mismatch_vs_reference"] += int(a1 != a2)
                    if side == e["side"]:
                        labels["resolved" if a1["result"] != "UNRESOLVED" else "unresolved_excluded"] += 1
                        labels["same_candle_both_barriers_stop_first"] += int(a1["ambiguous"])
    for item in rep.values():
        item["states"] = dict(item["states"])
        item["entries_by_symbol"] = dict(item["entries_by_symbol"])
    return {"protocol_hash": er.protocol_hash(), "oos_prices_loaded": False, "periods": rep, "invariants": dict(inv), "label_counts": dict(labels)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["frequency", "check", "run"])
    p.add_argument("--data-2025", type=Path, required=True)
    p.add_argument("--data-2026", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--protocol-hash")
    p.add_argument("--cache-2025", type=Path)
    p.add_argument("--cache-2026", type=Path)
    a = p.parse_args()
    if a.command == "check":
        blocks = {"2025": load_block(a.data_2025, "2025"), "2026": load_block(a.data_2026, "2026", end_override=day(er.PROTOCOL.oos[0]))}
        result = check(blocks)
        if a.output:
            out_dir = outside_repo(a.output)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "c14_implementation_check.json").write_text(json.dumps(result, indent=1, default=float), encoding="utf-8")
        print(json.dumps(result, indent=1, default=float))
        return
    blocks = {"2025": load_block(a.data_2025, "2025"), "2026": load_block(a.data_2026, "2026")}
    if a.command == "frequency":
        print(json.dumps({"counts_only": True, "protocol_hash": er.protocol_hash(), "per_period": frequency(blocks)}, indent=1))
    else:
        if not a.output:
            raise SystemExit("--output is required")
        run(a, blocks)


if __name__ == "__main__":
    sys.exit(main())
