"""C17 locked hypothesis definitions. Every number is fixed BEFORE any outcome is computed and is never changed after TRAIN/VALID/OOS results.
`INTERPRETATIONS` lists the places where the spec was silent; they were fixed a priori (not from outcomes) and are reported."""
from __future__ import annotations

import hashlib
import json

CONFIG = {
    "H1_BREAKOUT_RETEST_RECLAIM": {"range_window": 20, "breakout_body_atr": 0.35, "breakout_close_location": 0.65, "max_retest_bars": 3,
                                   "retest_zone_atr": 0.25, "confirmation_body_atr": 0.20},
    "H2_LIQUIDITY_SWEEP_REVERSAL": {"swing_window": 20, "max_confirmation_bars": 2, "minimum_sweep_atr": 0.20, "confirmation_close_location": 0.60,
                                    "sweep_close_location": 0.60},
    "H3_COMPRESSION_DISPLACEMENT_CONTINUATION": {"compression_window": 10, "compression_lookback": 96, "displacement_body_atr": 1.00,
                                                 "displacement_range_atr": 1.25, "displacement_close_location": 0.75, "max_continuation_bars": 2},
    "swing_fractal_K": 3, "atr_period": 14, "label": {"target_atr": 2.0, "stop_atr": 2.0, "both_in_one_candle": "STOP first", "entry": "open of C+1"},
}
INTERPRETATIONS = {
    "H1_level": "the HIGHEST confirmed swing high (SHORT: lowest swing low) whose pivot lies in the last 20 closed candles and was confirmed at or before the previous candle",
    "H1_breakout": "close crosses the level (previous close <= level), bullish body, body/ATR and close location as locked; one setup per level pivot",
    "H1_retest": "within 3 candles after the breakout a candle with low <= level + 0.25 ATR(breakout candle); a close below the level (wrong side) before the retest or confirmation = FAIL",
    "H1_confirmation_window": "SPEC SILENT -> fixed a priori: the confirmation candle is the candle immediately after the retest candle (1 bar), bullish, close > level, body/ATR(own candle) >= 0.20",
    "H2_level": "the LOWEST confirmed swing low (SHORT: highest swing high) in the last 20 candles confirmed at or before the previous candle; one setup per level pivot",
    "H2_sweep": "low < level - 0.20 ATR (minimum structural distance), close > level, lower wick (min(open,close)-low) >= 0.20 ATR (reuses minimum_sweep_atr: no extra parameter), close location >= 0.60",
    "H2_confirmation": "within 2 candles: close > sweep high, OR (bullish close, close location >= 0.60, close > level); close below the level first = FAIL",
    "H3_compression": "SPEC SILENT on the number -> fixed a priori: mean range of the 10 closed candles before the displacement candle <= 0.70 x mean range of the 96 candles before them",
    "H3_displacement": "bullish, body/ATR >= 1.0, range/ATR >= 1.25, close location >= 0.75, close above the highest high of the compression window",
    "H3_continuation": "within 2 candles: low >= displacement low and close > compression high; confirmation = bullish close meeting both; either broken first = FAIL",
    "H3_one_per_region": "after a displacement fires, no new H3 candidate for 10 candles (compression_window) and a fresh compression is needed",
    "SHORT": "identical engine on mirrored candles (o,h,l,c -> -o,-l,-h,-c)",
    "timestamps": "setup/retest/confirmation_ts = candle OPEN times; level_known_ts = close time of the candle that confirmed the pivot; decision_ts = confirmation candle close = entry_ts",
    "atr": "ATR14 (Wilder) of the candle named: threshold ATRs use that candle's own ATR (H1 retest zone: breakout candle); label ATR = decision (confirmation) candle",
    "expectancy": "gross symmetric +-1R (C14/C15 convention); net of 5 bps taker fee per side and actual funding reported as a secondary figure",
}
DECISION_RULES = {"min_n": 100, "pass": "Wilson CI lower > 50% AND expectancy > +0.05R AND PF >= 1.10 AND walk-forward stable",
                  "walk_forward_stable": "expectancy > 0 in at least 2 of the 3 TRAIN folds (folds with N<10 count as not positive)",
                  "verdict_stage": "OOS (run once, after freeze); N<100 -> INCONCLUSIVE; criteria failed with N>=100 -> FAIL; PASS -> PROMISING — REQUIRES SEPARATE VALIDATION",
                  "valid_is_result_only": True}


def definition_hash() -> str:
    return hashlib.sha256(json.dumps({"config": CONFIG, "interpretations": INTERPRETATIONS, "rules": DECISION_RULES}, sort_keys=True).encode()).hexdigest()


def definitions() -> dict:
    return {"config": CONFIG, "interpretations": INTERPRETATIONS, "decision_rules": DECISION_RULES, "sha256": definition_hash(),
            "production_import": False, "ml": False, "scoring_used": False}
