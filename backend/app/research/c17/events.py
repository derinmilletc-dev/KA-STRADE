"""C17 streaming event detectors (leakage-safe). Each `step(eng)` is called right after `eng.push(candle j)` and looks only at candles <= j.
Orientation: LONG logic on the engine series; SHORT = the same code on the mirrored series. Returns completed setups (decision = close of j)."""
from __future__ import annotations

from collections import Counter

from app.research.c16 import structure as st
from app.research.c17.hypotheses import CONFIG

K = st.K


def _loc(eng, i: int) -> float:
    rng = eng.h[i] - eng.l[i]
    return (eng.c[i] - eng.l[i]) / rng if rng > 0 else 0.5


def _known(swings: list, j: int, window: int):
    """Confirmed swings usable at candle j for a level that must be known BEFORE j: confirmed_at <= j-1, pivot within the last `window` candles."""
    out = []
    for pivot, conf, price in reversed(swings):
        if pivot < j - window:
            break
        if conf <= j - 1:
            out.append((pivot, conf, price))
    return out


class Base:
    name = ""

    def __init__(self) -> None:
        self.pending: list[dict] = []
        self.used: set[int] = set()
        self.inv: Counter = Counter()

    def fail(self, reason: str) -> None:
        self.inv[f"fail:{reason}"] += 1


class H1(Base):
    name = "H1_BREAKOUT_RETEST_RECLAIM"

    def __init__(self) -> None:
        super().__init__()
        self.c = CONFIG[self.name]

    def step(self, eng) -> list[dict]:
        j, c, done = eng.j, self.c, []
        keep = []
        for p in self.pending:                                     # events started on an earlier candle
            lvl, atr_b = p["level"], p["atr_b"]
            if p["retest"] is None:
                if eng.c[j] < lvl:
                    self.fail("wrong_side_close_before_retest")
                elif eng.l[j] <= lvl + c["retest_zone_atr"] * atr_b:
                    p["retest"] = j
                    keep.append(p)
                elif j - p["b"] >= c["max_retest_bars"]:
                    self.fail("no_retest_in_3_bars")
                else:
                    keep.append(p)
            else:                                                  # j is the candle right after the retest candle
                body = (eng.c[j] - eng.o[j]) / eng.atr
                if eng.c[j] > lvl and eng.c[j] > eng.o[j] and body >= c["confirmation_body_atr"]:
                    done.append({"hyp": self.name, "level_known_idx": p["conf"], "setup_idx": p["b"], "retest_idx": p["retest"], "confirm_idx": j,
                                 "level": lvl, "pivot": p["pivot"]})
                    self.inv["completed"] += 1
                else:
                    self.fail("no_confirmation" if eng.c[j] >= lvl else "wrong_side_close_before_confirmation")
        self.pending = keep
        # new breakout candle at j
        if eng.atr is None or j < 40:
            return done
        known = [s for s in _known(eng.sh, j, c["range_window"])]
        if not known:
            return done
        pivot, conf, lvl = max(known, key=lambda s: (s[2], s[0]))
        if eng.c[j] > lvl >= eng.c[j - 1]:
            body = (eng.c[j] - eng.o[j]) / eng.atr
            if eng.c[j] > eng.o[j] and body >= c["breakout_body_atr"] and _loc(eng, j) >= c["breakout_close_location"]:
                if pivot in self.used:
                    self.fail("duplicate_event_same_level")
                else:
                    self.used.add(pivot)
                    self.inv["breakouts"] += 1
                    self.pending.append({"level": lvl, "pivot": pivot, "conf": conf, "b": j, "atr_b": eng.atr, "retest": None})
            else:
                self.inv["cross_without_quality"] += 1
        return done


class H2(Base):
    name = "H2_LIQUIDITY_SWEEP_REVERSAL"

    def __init__(self) -> None:
        super().__init__()
        self.c = CONFIG[self.name]

    def step(self, eng) -> list[dict]:
        j, c, done, keep = eng.j, self.c, [], []
        for p in self.pending:
            age = j - p["s"]
            if eng.c[j] < p["level"]:
                self.fail("wrong_side_close_after_sweep")
                continue
            bullish = eng.c[j] > eng.o[j]
            strong = bullish and _loc(eng, j) >= c["confirmation_close_location"] and eng.c[j] > p["level"]
            if eng.c[j] > p["sweep_high"] or strong:
                done.append({"hyp": self.name, "level_known_idx": p["conf"], "setup_idx": p["s"], "retest_idx": None, "confirm_idx": j, "level": p["level"], "pivot": p["pivot"]})
                self.inv["completed"] += 1
            elif age >= c["max_confirmation_bars"]:
                self.fail("no_confirmation_in_2_bars")
            else:
                keep.append(p)
        self.pending = keep
        if eng.atr is None or j < 40:
            return done
        known = _known(eng.sl, j, c["swing_window"])
        if not known:
            return done
        pivot, conf, lvl = min(known, key=lambda s: (s[2], -s[0]))
        wick = (min(eng.o[j], eng.c[j]) - eng.l[j]) / eng.atr
        depth = (lvl - eng.l[j]) / eng.atr
        if eng.l[j] < lvl and eng.c[j] > lvl:
            if depth >= c["minimum_sweep_atr"] and wick >= c["minimum_sweep_atr"] and _loc(eng, j) >= c["sweep_close_location"]:
                if pivot in self.used:
                    self.fail("duplicate_sweep_same_level")
                else:
                    self.used.add(pivot)
                    self.inv["sweeps"] += 1
                    self.pending.append({"level": lvl, "pivot": pivot, "conf": conf, "s": j, "sweep_high": eng.h[j]})
            else:
                self.fail("sweep_below_minimum_structure")
        return done


class H3(Base):
    name = "H3_COMPRESSION_DISPLACEMENT_CONTINUATION"

    def __init__(self) -> None:
        super().__init__()
        self.c = CONFIG[self.name]
        self.locked_until = -1
        self.ratio_max = 0.70                                      # fixed a priori (see INTERPRETATIONS)

    def step(self, eng) -> list[dict]:
        j, c, done, keep = eng.j, self.c, [], []
        for p in self.pending:
            age = j - p["d"]
            if eng.l[j] < p["low_d"]:
                self.fail("displacement_low_broken")
            elif eng.c[j] <= p["comp_high"]:
                self.fail("returned_into_compression_range")
            elif eng.c[j] > eng.o[j]:
                done.append({"hyp": self.name, "level_known_idx": p["d"] - 1, "setup_idx": p["d"], "retest_idx": None, "confirm_idx": j,
                             "level": p["comp_high"], "pivot": p["d"]})
                self.inv["completed"] += 1
            elif age >= c["max_continuation_bars"]:
                self.fail("no_confirmation_in_2_bars")
            else:
                keep.append(p)
        self.pending = keep
        w, lb = c["compression_window"], c["compression_lookback"]
        if eng.atr is None or j < w + lb + 1:
            return done
        rng_now = sum(eng.h[i] - eng.l[i] for i in range(j - w, j)) / w
        rng_old = sum(eng.h[i] - eng.l[i] for i in range(j - w - lb, j - w)) / lb
        compressed = rng_old > 0 and rng_now / rng_old <= self.ratio_max
        self.inv["compressed_candles"] += int(compressed)
        if not compressed or j <= self.locked_until:
            return done
        comp_high = max(eng.h[j - w:j])
        body, rng = (eng.c[j] - eng.o[j]) / eng.atr, (eng.h[j] - eng.l[j]) / eng.atr
        if eng.c[j] > eng.o[j] and body >= c["displacement_body_atr"] and rng >= c["displacement_range_atr"] and _loc(eng, j) >= c["displacement_close_location"]:
            if eng.c[j] > comp_high:
                self.locked_until = j + w
                self.inv["displacements"] += 1
                self.pending.append({"d": j, "comp_high": comp_high, "low_d": eng.l[j]})
            else:
                self.fail("displacement_inside_compression_range")
        elif body >= c["displacement_body_atr"] * 0.5:
            self.fail("displacement_too_small")
        return done


DETECTORS = (H1, H2, H3)
