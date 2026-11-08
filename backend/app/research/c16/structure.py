"""KAIS FARMER C16: leakage-safe, deterministic structure engine. Research only: no order, no exchange client, no live gate.

`SideEngine.push(o,h,l,c,v)` consumes ONE closed 15m candle at a time and returns the feature dict for the decision at that
candle's close (decision = close of candle C, entry = open of C+1). Nothing after the pushed candle can influence the output:
state is only ever extended with the candle just pushed. Swings are confirmed fractals (K bars to the right must have closed),
so a swing found at bar j has pivot index j-K and `confirmed_at` = j. The SHORT view is the same engine fed with the
mirrored series (o,h,l,c -> -o,-l,-h,-c; volume unchanged), so every feature is already "in the direction of the trade".
All constants are fixed a priori (below) and are never tuned on outcomes.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

BAR = 900
K = 3                  # swing fractal width (bars on each side)
RANGE_SWINGS = 3       # range = last 3 confirmed swing highs / lows
W = 16                 # breakout stays "active" for W bars
ZONE = 0.25            # retest touch zone / failure margin, in ATR of the breakout bar
ATR_N, RSI_N, ADX_N = 14, 14, 14
SLOPE = 4              # slope lag (bars)
RECENT = 48            # "recent" window (bars) = 12 h
PCT_WIN, PCT_MIN = 2880, 480
DISP = 1.0             # displacement candle: body >= 1 ATR
COMPRESSION, EXPANSION = 0.8, 1.25
WARMUP = 300           # bars after a reset before rows are emitted
NAN = float("nan")

FAMILIES = {
    "A_STRUCTURE": ["struct_score", "hh", "hl", "bars_since_swing_high", "bars_since_swing_low", "dist_last_sh_atr", "dist_last_sl_atr",
                    "swing_amp_atr", "range_width_atr", "above_last_sh", "below_last_sl", "bos_with", "choch_with", "swing_count_recent"],
    "B_BREAKOUT_RETEST": ["bo_fresh", "bo_dist_atr", "bo_body_ratio", "bo_close_pos", "bo_vol_ratio", "bo_age", "bo_active", "rt_state",
                          "rt_depth_atr", "rt_bars_to_touch", "rt_failed_recent", "rt_touched", "rt_confirmed", "rt_fail_active"],
    "C_LOCATION": ["range_pos", "premium_discount", "dist_range_high_atr", "dist_range_low_atr", "ema20_dist_atr", "ema50_dist_atr",
                   "ema200_dist_atr", "dist_day_high_atr", "dist_day_low_atr", "dist_prev_day_high_atr", "dist_prev_day_low_atr",
                   "bars_since_recent_high", "bars_since_recent_low"],
    "D_MOMENTUM": ["rsi", "rsi_slope", "macd_hist_atr", "macd_hist_slope", "roc8_atr", "roc16_atr", "volume_ratio", "body_atr_signed",
                   "range_atr", "close_pos", "disp_flag", "disp_recent_sum_atr", "consec_with", "fvg_with_atr", "fvg_against_atr"],
    "E_VOLATILITY_REGIME": ["atr_pct", "atr_percentile", "range_expansion", "compression", "expansion", "vol_tercile", "adx", "trend_regime"],
}
ALL_FEATURES = [f for fs in FAMILIES.values() for f in fs]
CUTOFF_FIELDS = ["candle_close_ts", "feature_cutoff_ts", "swing_max_confirmed_ts", "level_known_ts"]


def _ema(prev: float | None, x: float, n: int) -> float:
    return x if prev is None else prev + (2.0 / (n + 1)) * (x - prev)


class SideEngine:
    """Streaming engine for one orientation. `open_time` of every pushed candle must be exactly 15m after the previous one."""

    def __init__(self) -> None:
        self.j = -1
        self.o: list[float] = []
        self.h: list[float] = []
        self.l: list[float] = []
        self.c: list[float] = []
        self.v: list[float] = []
        self.t: list[int] = []
        self.sh: list[tuple[int, int, float]] = []     # (pivot_idx, confirmed_at, price)
        self.sl: list[tuple[int, int, float]] = []
        self.atr: float | None = None
        self.atr_hist: deque[float] = deque(maxlen=PCT_WIN)
        self.tr_hist: deque[float] = deque(maxlen=5)
        self.rsi_ag = self.rsi_al = None
        self.rsi_hist: list[float] = []
        self.ema = {20: None, 50: None, 200: None}
        self.macd_f = self.macd_s = self.macd_sig = None
        self.hist_hist: list[float] = []
        self.adx_sm = [None, None, None]               # smoothed +DM, -DM, TR
        self.dx_hist: list[float] = []
        self.adx: float | None = None
        self.prev_range: tuple[float, float] | None = None   # range (high, low) known at the previous bar
        self.bo: dict | None = None                    # active breakout state
        self.fail_idx = -10_000
        self.day = None
        self.day_hl = [NAN, NAN]
        self.prev_day_hl = [NAN, NAN]
        self.tr_list: list[float] = []

    # -------------------------------------------------------------------------------------------- indicators
    def _indicators(self, o: float, h: float, l: float, c: float, v: float) -> None:
        j = self.j
        pc = self.c[j - 1] if j > 0 else c
        tr = max(h - l, abs(h - pc), abs(l - pc))
        self.tr_list.append(tr)
        self.tr_hist.append(tr)
        if len(self.tr_list) == ATR_N:
            self.atr = sum(self.tr_list) / ATR_N
        elif len(self.tr_list) > ATR_N:
            self.atr = (self.atr * (ATR_N - 1) + tr) / ATR_N
        if self.atr is not None:
            self.atr_hist.append(self.atr)
        # RSI (Wilder)
        if j > 0:
            ch = c - pc
            g, ls = max(ch, 0.0), max(-ch, 0.0)
            if j <= RSI_N:
                self.rsi_ag = (self.rsi_ag or 0.0) + g / RSI_N
                self.rsi_al = (self.rsi_al or 0.0) + ls / RSI_N
            else:
                self.rsi_ag = (self.rsi_ag * (RSI_N - 1) + g) / RSI_N
                self.rsi_al = (self.rsi_al * (RSI_N - 1) + ls) / RSI_N
        if j >= RSI_N and self.rsi_al is not None:
            self.rsi_hist.append(100.0 if self.rsi_al == 0 else 100.0 - 100.0 / (1.0 + self.rsi_ag / self.rsi_al))
        # EMAs, MACD
        for n in self.ema:
            self.ema[n] = _ema(self.ema[n], c, n)
        self.macd_f, self.macd_s = _ema(self.macd_f, c, 12), _ema(self.macd_s, c, 26)
        macd = self.macd_f - self.macd_s
        self.macd_sig = _ema(self.macd_sig, macd, 9)
        self.hist_hist.append(macd - self.macd_sig)
        # ADX (Wilder)
        if j > 0:
            up, dn = h - self.h[j - 1], self.l[j - 1] - l
            pdm, mdm = (up if up > dn and up > 0 else 0.0), (dn if dn > up and dn > 0 else 0.0)
            if j <= ADX_N:
                self.adx_sm = [(self.adx_sm[0] or 0.0) + pdm, (self.adx_sm[1] or 0.0) + mdm, (self.adx_sm[2] or 0.0) + tr]
            else:
                self.adx_sm = [self.adx_sm[0] - self.adx_sm[0] / ADX_N + pdm, self.adx_sm[1] - self.adx_sm[1] / ADX_N + mdm,
                               self.adx_sm[2] - self.adx_sm[2] / ADX_N + tr]
            if j >= ADX_N and self.adx_sm[2] > 0:
                pdi, mdi = 100 * self.adx_sm[0] / self.adx_sm[2], 100 * self.adx_sm[1] / self.adx_sm[2]
                dx = 0.0 if pdi + mdi == 0 else 100 * abs(pdi - mdi) / (pdi + mdi)
                self.dx_hist.append(dx)
                if len(self.dx_hist) == ADX_N:
                    self.adx = sum(self.dx_hist) / ADX_N
                elif len(self.dx_hist) > ADX_N:
                    self.adx = (self.adx * (ADX_N - 1) + dx) / ADX_N

    # -------------------------------------------------------------------------------------------- swings
    def _confirm_swings(self) -> None:
        j, p = self.j, self.j - K
        if p < K:
            return
        hp, lp = self.h[p], self.l[p]
        if hp > max(self.h[p - K:p]) and hp >= max(self.h[p + 1:j + 1]):
            self.sh.append((p, j, hp))
        if lp < min(self.l[p - K:p]) and lp <= min(self.l[p + 1:j + 1]):
            self.sl.append((p, j, lp))

    def _range(self) -> tuple[float, float] | None:
        if len(self.sh) < RANGE_SWINGS or len(self.sl) < RANGE_SWINGS:
            return None
        return max(s[2] for s in self.sh[-RANGE_SWINGS:]), min(s[2] for s in self.sl[-RANGE_SWINGS:])

    # -------------------------------------------------------------------------------------------- push
    def push(self, t: int, o: float, h: float, l: float, c: float, v: float) -> dict | None:
        """t = open time of the candle (epoch s). Returns features at the CLOSE of this candle, or None while warming up."""
        if self.t and t != self.t[-1] + BAR:
            raise ValueError("non-contiguous candle")
        self.j += 1
        j = self.j
        for arr, x in ((self.o, o), (self.h, h), (self.l, l), (self.c, c), (self.v, v), (self.t, t)):
            arr.append(x)
        self._indicators(o, h, l, c, v)
        self._confirm_swings()
        # day levels (previous COMPLETE UTC day only)
        day = t // 86400
        if day != self.day:
            self.prev_day_hl = list(self.day_hl) if self.day is not None else [NAN, NAN]
            self.day, self.day_hl = day, [h, l]
        else:
            self.day_hl = [max(self.day_hl[0], h), min(self.day_hl[1], l)]
        rng_prev, rng_now = self.prev_range, self._range()
        out = self._features(o, h, l, c, v, rng_prev, rng_now) if j >= WARMUP and self.atr else None
        self.prev_range = rng_now          # level known at j; used as the "previously known" level for bar j+1
        return out

    def _features(self, o: float, h: float, l: float, c: float, v: float, rng_prev, rng_now) -> dict | None:
        j, atr = self.j, self.atr
        if rng_now is None or self.adx is None or len(self.rsi_hist) <= SLOPE or len(self.sh) < 2 or len(self.sl) < 2:
            return None
        f: dict[str, float] = {}
        sh1, sh0, sl1, sl0 = self.sh[-1], self.sh[-2], self.sl[-1], self.sl[-2]
        hh, hl = sh1[2] > sh0[2], sl1[2] > sl0[2]
        f["hh"], f["hl"] = float(hh), float(hl)
        f["struct_score"] = float((1 if hh else -1) + (1 if hl else -1))
        f["bars_since_swing_high"], f["bars_since_swing_low"] = float(j - sh1[1]), float(j - sl1[1])
        f["dist_last_sh_atr"], f["dist_last_sl_atr"] = (c - sh1[2]) / atr, (c - sl1[2]) / atr
        f["swing_amp_atr"] = abs(sh1[2] - sl1[2]) / atr
        rh, rl = rng_now
        f["range_width_atr"] = (rh - rl) / atr
        f["above_last_sh"], f["below_last_sl"] = float(c > sh1[2]), float(c < sl1[2])
        f["bos_with"] = float(f["struct_score"] > 0 and c > sh1[2])
        f["choch_with"] = float(f["struct_score"] < 0 and c > sh1[2])
        f["swing_count_recent"] = float(sum(1 for s in self.sh[-6:] + self.sl[-6:] if j - s[1] <= RECENT))
        # breakout (fresh close-cross of the range high KNOWN at j-1) and retest state machine
        level_known_idx = j - 1
        fresh = bool(rng_prev is not None and c > rng_prev[0] and self.c[j - 1] <= rng_prev[0])
        if fresh:
            self.bo = {"idx": j, "level": rng_prev[0], "atr": atr, "touch": None, "state": 1, "min_low": l, "held_after_touch": False}
        elif self.bo is not None:
            bo = self.bo
            age = j - bo["idx"]
            bo["min_low"] = min(bo["min_low"], l)
            zone = ZONE * bo["atr"]
            if bo["state"] >= 1 and bo["state"] != -1 and c < bo["level"] - zone:
                bo["state"] = -1
                self.fail_idx = j
            elif bo["state"] == 1 and l <= bo["level"] + zone and c >= bo["level"] - zone:
                bo["state"], bo["touch"] = 2, j
            elif bo["state"] == 2 and j > bo["touch"] and c > self.h[bo["touch"]] and c > bo["level"]:
                bo["state"] = 3
            if age > W:
                self.bo = None
        bo = self.bo
        active = bo is not None and bo["state"] != -1 and (j - bo["idx"]) <= W
        f["bo_fresh"] = float(fresh)
        rng_bar = max(h - l, 1e-12)
        if fresh:
            f["bo_dist_atr"] = (c - bo["level"]) / atr
            f["bo_body_ratio"] = abs(c - o) / rng_bar
            f["bo_close_pos"] = (c - l) / rng_bar
            vm = float(np.mean(self.v[j - 20:j])) if j >= 20 else NAN
            f["bo_vol_ratio"] = v / vm if vm else NAN
        else:
            f["bo_dist_atr"] = f["bo_body_ratio"] = f["bo_close_pos"] = f["bo_vol_ratio"] = NAN
        f["bo_age"] = float(j - bo["idx"]) if bo is not None else NAN
        f["bo_active"] = float(active)
        f["rt_state"] = float(bo["state"]) if bo is not None else 0.0
        f["rt_depth_atr"] = (bo["level"] - bo["min_low"]) / bo["atr"] if bo is not None else NAN
        f["rt_bars_to_touch"] = float(bo["touch"] - bo["idx"]) if bo is not None and bo["touch"] is not None else NAN
        f["rt_failed_recent"] = float(j - self.fail_idx <= W)
        state = bo["state"] if bo is not None else 0
        f["rt_touched"], f["rt_confirmed"], f["rt_fail_active"] = float(state in (2, 3)), float(state == 3), float(state == -1)
        # location
        span = max(rh - rl, 1e-12)
        f["range_pos"] = min(1.5, max(-0.5, (c - rl) / span))
        f["premium_discount"] = f["range_pos"] - 0.5
        f["dist_range_high_atr"], f["dist_range_low_atr"] = (c - rh) / atr, (c - rl) / atr
        for n in (20, 50, 200):
            f[f"ema{n}_dist_atr"] = (c - self.ema[n]) / atr
        hi96, lo96 = max(self.h[j - 95:j + 1]), min(self.l[j - 95:j + 1])
        f["dist_day_high_atr"], f["dist_day_low_atr"] = (c - hi96) / atr, (c - lo96) / atr
        f["dist_prev_day_high_atr"] = (c - self.prev_day_hl[0]) / atr
        f["dist_prev_day_low_atr"] = (c - self.prev_day_hl[1]) / atr
        win_h, win_l = self.h[j - RECENT + 1:j + 1], self.l[j - RECENT + 1:j + 1]
        f["bars_since_recent_high"] = float(RECENT - 1 - int(np.argmax(win_h)))
        f["bars_since_recent_low"] = float(RECENT - 1 - int(np.argmin(win_l)))
        # momentum / displacement
        f["rsi"], f["rsi_slope"] = self.rsi_hist[-1], self.rsi_hist[-1] - self.rsi_hist[-1 - SLOPE]
        f["macd_hist_atr"] = self.hist_hist[-1] / atr
        f["macd_hist_slope"] = (self.hist_hist[-1] - self.hist_hist[-1 - SLOPE]) / atr
        f["roc8_atr"], f["roc16_atr"] = (c - self.c[j - 8]) / atr, (c - self.c[j - 16]) / atr
        vm20 = float(np.mean(self.v[j - 19:j + 1]))
        f["volume_ratio"] = v / vm20 if vm20 else NAN
        f["body_atr_signed"] = (c - o) / atr
        f["range_atr"] = (h - l) / atr
        f["close_pos"] = (c - l) / rng_bar
        f["disp_flag"] = float(abs(c - o) >= DISP * atr)
        f["disp_recent_sum_atr"] = sum((self.c[i] - self.o[i]) for i in range(j - 3, j + 1)) / atr
        run = 0
        for i in range(j, max(j - 8, -1), -1):
            if self.c[i] > self.o[i]:
                run += 1
            else:
                break
        f["consec_with"] = float(run)
        f["fvg_with_atr"] = max(0.0, l - self.h[j - 2]) / atr
        f["fvg_against_atr"] = max(0.0, self.l[j - 2] - h) / atr
        # volatility / regime
        f["atr_abs"] = atr                                   # raw ATR: label scale only, never a model feature
        f["atr_pct"] = atr / abs(c) * 100 if c else NAN
        hist = np.asarray(self.atr_hist)[:-1]
        f["atr_percentile"] = float((hist < atr).mean()) if len(hist) >= PCT_MIN else NAN
        f["range_expansion"] = (sum(self.tr_hist) / len(self.tr_hist)) / atr
        f["compression"], f["expansion"] = float(f["range_expansion"] < COMPRESSION), float(f["range_expansion"] > EXPANSION)
        p = f["atr_percentile"]
        f["vol_tercile"] = NAN if math.isnan(p) else (0.0 if p < 1 / 3 else (1.0 if p < 2 / 3 else 2.0))
        f["adx"] = self.adx
        f["trend_regime"] = float(self.adx >= 20)
        # leakage metadata (all in epoch seconds; every value is <= the decision timestamp)
        cutoff = self.t[j] + BAR
        f["candle_close_ts"], f["feature_cutoff_ts"] = float(cutoff), float(cutoff)
        newest = max(sh1[1], sl1[1], sh0[1], sl0[1])
        f["swing_max_confirmed_ts"] = float(self.t[newest] + BAR)
        f["level_known_ts"] = float(self.t[level_known_idx] + BAR)
        return f


def mirror(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray):
    """SHORT view: mirrored prices, same volume."""
    return -o, -l, -h, -c, v


def run_series(t: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray, side: str,
               until: int | None = None) -> dict[int, dict]:
    """Run the engine over candles [0, until] and return {index: features}. Index j = decision at the close of candle j."""
    if side == "SHORT":
        o, h, l, c, v = mirror(o, h, l, c, v)
    eng, out = SideEngine(), {}
    end = len(t) if until is None else until + 1
    for j in range(end):
        f = eng.push(int(t[j]), float(o[j]), float(h[j]), float(l[j]), float(c[j]), float(v[j]))
        if f is not None:
            out[j] = f
    return out
