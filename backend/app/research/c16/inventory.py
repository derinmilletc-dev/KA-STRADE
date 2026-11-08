"""C16 feature inventory: family, one-line definition, longest look-back (15m bars) of the source data, all direction-aligned (SHORT = mirrored)."""
from app.research.c16 import structure as st

DESC = {
    "struct_score": ("last two confirmed swing highs/lows: +1 per higher high, +1 per higher low, -1 otherwise (range -2..+2)", 60),
    "hh": ("last confirmed swing high above the previous one", 60), "hl": ("last confirmed swing low above the previous one", 60),
    "bars_since_swing_high": ("bars since the last swing high was CONFIRMED", 60), "bars_since_swing_low": ("bars since the last swing low was CONFIRMED", 60),
    "dist_last_sh_atr": ("(close - last confirmed swing high) / ATR", 60), "dist_last_sl_atr": ("(close - last confirmed swing low) / ATR", 60),
    "swing_amp_atr": ("|last swing high - last swing low| / ATR", 60), "range_width_atr": ("(max of last 3 swing highs - min of last 3 swing lows) / ATR", 120),
    "above_last_sh": ("close above last confirmed swing high", 60), "below_last_sl": ("close below last confirmed swing low", 60),
    "bos_with": ("up-structure and close above last swing high (break of structure)", 60), "choch_with": ("down-structure and close above last swing high (change of character)", 60),
    "swing_count_recent": ("swings confirmed in the last 48 bars", 48),
    "bo_fresh": ("close crosses above the range high KNOWN at the previous bar", 120), "bo_dist_atr": ("(close - broken level) / ATR, breakout bar only", 120),
    "bo_body_ratio": ("body / range of the breakout bar", 120), "bo_close_pos": ("close position in the breakout bar", 120),
    "bo_vol_ratio": ("breakout bar volume / mean of the previous 20", 140), "bo_age": ("bars since the breakout (active window 16)", 140),
    "bo_active": ("a breakout is active and not failed", 140), "rt_state": ("0 none, 1 waiting, 2 retest touched, 3 retest confirmed, -1 failed", 140),
    "rt_depth_atr": ("(level - lowest low since breakout) / ATR of the breakout bar", 140), "rt_bars_to_touch": ("bars from breakout to retest touch", 140),
    "rt_failed_recent": ("a breakout failed within the last 16 bars", 140), "rt_touched": ("state 2 or 3", 140), "rt_confirmed": ("state 3", 140), "rt_fail_active": ("state -1", 140),
    "range_pos": ("(close - range low) / (range high - range low), clipped to [-0.5, 1.5]", 120), "premium_discount": ("range_pos - 0.5", 120),
    "dist_range_high_atr": ("(close - range high) / ATR", 120), "dist_range_low_atr": ("(close - range low) / ATR", 120),
    "ema20_dist_atr": ("(close - EMA20) / ATR", 400), "ema50_dist_atr": ("(close - EMA50) / ATR", 400), "ema200_dist_atr": ("(close - EMA200) / ATR", 800),
    "dist_day_high_atr": ("(close - highest high of last 96 bars) / ATR", 96), "dist_day_low_atr": ("(close - lowest low of last 96 bars) / ATR", 96),
    "dist_prev_day_high_atr": ("(close - high of the previous COMPLETE UTC day) / ATR", 192), "dist_prev_day_low_atr": ("(close - low of the previous COMPLETE UTC day) / ATR", 192),
    "bars_since_recent_high": ("bars since the highest high of the last 48", 48), "bars_since_recent_low": ("bars since the lowest low of the last 48", 48),
    "rsi": ("Wilder RSI(14)", 300), "rsi_slope": ("RSI - RSI 4 bars ago", 300), "macd_hist_atr": ("MACD(12,26,9) histogram / ATR", 400),
    "macd_hist_slope": ("histogram change over 4 bars / ATR", 400), "roc8_atr": ("(close - close 8 bars ago) / ATR", 8), "roc16_atr": ("(close - close 16 bars ago) / ATR", 16),
    "volume_ratio": ("volume / mean volume of the last 20 bars", 20), "body_atr_signed": ("(close - open) / ATR", 1), "range_atr": ("(high - low) / ATR", 1),
    "close_pos": ("close position in the candle range", 1), "disp_flag": ("body >= 1 ATR", 1), "disp_recent_sum_atr": ("sum of the last 4 bodies / ATR", 4),
    "consec_with": ("consecutive candles closing up (max 8)", 8), "fvg_with_atr": ("gap low[j] - high[j-2], in ATR (with the trade)", 3),
    "fvg_against_atr": ("gap low[j-2] - high[j], in ATR (against the trade)", 3),
    "atr_pct": ("ATR14 / |close| in %", 300), "atr_percentile": ("share of the previous 2880 ATR values below the current ATR (min 480)", 2880),
    "range_expansion": ("mean true range of the last 5 bars / ATR14", 300), "compression": ("range_expansion < 0.8", 300), "expansion": ("range_expansion > 1.25", 300),
    "vol_tercile": ("atr_percentile terciles: 0 low, 1 normal, 2 high (fixed 1/3 cuts, outcome-blind)", 2880), "adx": ("Wilder ADX(14)", 400),
    "trend_regime": ("ADX >= 20 (the threshold already used by the current Farmer code)", 400),
}


def inventory() -> dict:
    fam = {f: k for k, fs in st.FAMILIES.items() for f in fs}
    feats = {f: {"family": fam[f], "definition": DESC[f][0], "max_lookback_bars": DESC[f][1], "source": "closed 15m contract-price candles <= decision candle",
                 "uses_future_data": False} for f in st.ALL_FEATURES}
    return {"families": {k: list(v) for k, v in st.FAMILIES.items()}, "n_features": len(feats), "features": feats,
            "timing": "decision = close of 15m candle C; entry = open of C+1; feature_cutoff_ts = candle_close_ts; no C+1 data in any feature",
            "mirroring": "SHORT rows use the same engine on mirrored candles, so every feature is direction-aligned", "constants_fixed_a_priori": True}
