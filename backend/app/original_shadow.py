"""Original V2 canlı akışı için GÖLGE KAYIT: yalnız gözlem, hiçbir emir göndermez, hiçbir kararı değiştirmez.

Ne kaydeder
* FILTER_BLOCKED_SHORT: yalnız SHORT alignment filtresinin (alignment >= 80) reddettiği, diğer bütün sinyal kapılarını
  (yön, güven, trap, breakout) geçmiş SHORT sinyalleri.
* LIVE_ELIGIBLE: canlı taramanın girişe uygun bulduğu adaylar. Karşılaştırma aynı sanal modelle yapılsın diye (gerçek işlem
  başka kurallar/sınırlar yüzünden farklı olabilir) bunlar da sanal olarak izlenir.

Her sinyal için puanlar, göstergeler, fiyat konumu, HTF uyumu, trap/breakout değerleri saklanır. Her sinyal için SANAL işlem,
canlı V2'nin çıkış kurallarıyla izlenir: TP1 sinyal TP1 mesafesinin %80'inde ve %70 kapanır, kalan %30 ilk stop ve TP3 ile
devam eder; aynı komisyon ve kayma (execution_policy). Aynı mumda hem stop hem hedef değerse STOP ÖNCE (çevrimdışı modelle aynı).

Güvenlik
* Bu modül borsa istemcisi, emir, anahtar, hesap bilgisi İÇE AKTARMAZ ve kullanmaz.
* Canlı akıştan yalnız bounded, bloklamayan bir kuyruğa düz veri bırakılır; işlem ayrı bir iş parçacığında yapılır.
* Her hata yakalanır ve sayılır; hiçbir hata canlı akışa yayılmaz. `ORIGINAL_V2_SHADOW_ENABLED=0` ile kapatılır.

Bilinen sınırlamalar (kayıtlarda da yazılıdır)
* Sanal işlemler portföy/teminat/günlük limit/minimum miktar kapılarına tabi DEĞİLDİR; yalnız (grup, sembol) başına en fazla
  bir açık sanal işlem vardır (canlıdaki "sembol doluysa girme" kuralına karşılık).
* Giriş referansı sinyal mumunun kapanışıdır (canlı piyasa emri yeni mumun başında dolar); tick yuvarlaması ve fonlama yoktur.
* Tetikleme son fiyat mumlarının high/low değerleriyle yapılır (çevrimdışı model mark fiyatı kullanır).
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA = 1
ENV_ENABLED = "ORIGINAL_V2_SHADOW_ENABLED"
ENV_DIR = "ORIGINAL_V2_SHADOW_DIR"
# Canlı V2 sabitleriyle AYNI olmak zorunda (testle canlı kaynak dosyasından doğrulanır).
TP1_PRICE_RATIO = 0.80
TP1_FRACTION = 0.70
INTRABAR = "STOP_FIRST"
GROUP_BLOCKED = "FILTER_BLOCKED_SHORT"
GROUP_LIVE = "LIVE_ELIGIBLE"
GROUPS = (GROUP_BLOCKED, GROUP_LIVE)
QUALITY_KEYS = ("direction", "confidence", "trap", "breakout")
CANDLE_SECONDS = 900
MAX_OPEN_SECONDS = 45 * 86400
MAX_FILE_BYTES = 256 * 1024 * 1024
SEEN_LIMIT = 20000
MODEL_NOTES = [
    "sanal işlem: portföy/teminat/günlük limit/minimum miktar kapıları yok; (grup, sembol) başına tek açık işlem",
    "giriş referansı sinyal mumunun kapanışı; tick yuvarlaması ve fonlama yok",
    "tetikleme son fiyat mumu high/low; aynı mumda stop ve hedef birlikte değerse STOP ÖNCE",
]


def enabled() -> bool:
    value = str(os.getenv(ENV_ENABLED, "")).strip().lower()
    return value not in {"0", "false", "no", "off", "disabled"}


def directory() -> Path:
    configured = os.getenv(ENV_DIR)
    if configured:
        return Path(configured)
    from .local_storage import DATA_DIR
    return Path(DATA_DIR) / "original-v2-shadow"


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def sanitize(value: Any) -> Any:
    """JSON'a güvenli: sonlu olmayan sayılar None olur; iç içe yapılar gezilir."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    return value


# ---------------------------------------------------------------- saf hesaplar (birim testli)

def classify(item: dict[str, Any]) -> str | None:
    """LIVE_ELIGIBLE, FILTER_BLOCKED_SHORT ya da None (kaydedilmez)."""
    if item.get("eligible") is True:
        return GROUP_LIVE
    analysis, mtf, checks = item.get("analysis") or {}, item.get("mtf") or {}, item.get("checks") or {}
    if (item.get("ranked") is True and analysis.get("direction") == "SHORT"
            and mtf.get("blocked_by_short_filter") is True
            and all(checks.get(key) is True for key in QUALITY_KEYS)):
        return GROUP_BLOCKED
    return None


def reconstruct_scores(analysis: dict[str, Any]) -> dict[str, Any]:
    """analysis.analyze içindeki 8 puan bileşenini çıktı alanlarından yeniden kurar (analyze ile birebir)."""
    price = analysis["entry"]
    e20, e50, e200 = (analysis["ema"][key] for key in ("ema20", "ema50", "ema200"))
    macd, rsi, adx, volume = analysis["macd"], analysis["rsi"], analysis["adx"], analysis["volume_ratio"]
    middle = analysis["bollinger"]["middle"]
    long_parts = {
        "price_above_ema20": (price > e20, 10), "ema20_above_ema50": (e20 > e50, 12),
        "ema50_above_ema200": (e50 > e200, 14), "macd_positive": (macd > 0, 10),
        "rsi_52_72": (52 <= rsi <= 72, 9), "price_above_bb_mid": (price > middle, 6),
        "adx20_with_ema_up": (adx >= 20 and e20 > e50, 8), "volume_ratio_1_05": (volume >= 1.05, 6),
    }
    short_parts = {
        "price_below_ema20": (price < e20, 10), "ema20_below_ema50": (e20 < e50, 12),
        "ema50_below_ema200": (e50 < e200, 14), "macd_negative": (macd < 0, 10),
        "rsi_28_48": (28 <= rsi <= 48, 9), "price_below_bb_mid": (price < middle, 6),
        "adx20_with_ema_down": (adx >= 20 and e20 < e50, 8), "volume_ratio_1_05": (volume >= 1.05, 6),
    }
    return {
        "long_score": 25 + sum(points for on, points in long_parts.values() if on),
        "short_score": 25 + sum(points for on, points in short_parts.values() if on),
        "long_parts": {name: bool(on) for name, (on, _) in long_parts.items()},
        "short_parts": {name: bool(on) for name, (on, _) in short_parts.items()},
    }


def wick_ratios(recent: list[dict[str, Any]], atr: float) -> dict[str, float | None]:
    """Son 8 mum: üst/alt fitil toplamı / gövde toplamı (analysis.analyze ile aynı formül)."""
    rows = recent[-8:]
    if not rows:
        return {"upper_ratio": None, "lower_ratio": None}
    body_total = max(sum(abs(c["close"] - c["open"]) for c in rows), atr * 0.05, 0.00000001)
    return {"upper_ratio": sum(c["high"] - max(c["open"], c["close"]) for c in rows) / body_total,
            "lower_ratio": sum(min(c["open"], c["close"]) - c["low"] for c in rows) / body_total}


def signal_record(item: dict[str, Any], group: str, recorded_at: float, costs: dict[str, Any] | None) -> dict[str, Any]:
    analysis, mtf = item["analysis"], item.get("mtf") or {}
    price = float(analysis["entry"])
    atr = max(float(analysis["atr"]), 0.00000001)
    ema, boll, radar = analysis["ema"], analysis["bollinger"], analysis.get("radar") or {}
    scores = reconstruct_scores(analysis)
    record = {
        "schema": SCHEMA, "id": f"{group}|{item['symbol']}|{item['signal_time']}", "group": group,
        "symbol": item["symbol"], "direction": analysis["direction"], "signal_time": item["signal_time"],
        "decision_time": item["decision_time"], "recorded_at": recorded_at, "price": price,
        "confidence": analysis.get("confidence"),
        "scores": {**scores, "reconstruction_matches_confidence": min(95, max(scores["long_score"], scores["short_score"])) == analysis.get("confidence")},
        "indicators": {
            "rsi": analysis["rsi"], "macd_hist": analysis["macd"], "adx": analysis["adx"], "atr": analysis["atr"],
            "atr_pct": float(analysis["atr"]) / price * 100, "volume_ratio": analysis["volume_ratio"],
            "ema20": ema["ema20"], "ema50": ema["ema50"], "ema200": ema["ema200"],
            "bb_upper": boll["upper"], "bb_middle": boll["middle"], "bb_lower": boll["lower"],
            "support": analysis["support"], "resistance": analysis["resistance"], "trend": analysis.get("trend"),
            "momentum": analysis.get("momentum"),
        },
        "price_position": {
            "vs_ema20_atr": (price - ema["ema20"]) / atr, "vs_ema50_atr": (price - ema["ema50"]) / atr,
            "vs_ema200_atr": (price - ema["ema200"]) / atr, "vs_bb_middle_atr": (price - boll["middle"]) / atr,
            "support_distance_atr": (price - analysis["support"]) / atr,
            "resistance_distance_atr": (analysis["resistance"] - price) / atr,
            "extension_atr": abs(price - ema["ema20"]) / atr,
        },
        "wicks": wick_ratios(item.get("recent") or [], float(analysis["atr"])),
        "htf": {"alignment": mtf.get("alignment"), "verdict": mtf.get("verdict"),
                "entry_permission": mtf.get("entry_permission"),
                "blocked_by_short_filter": mtf.get("blocked_by_short_filter"),
                "higher_timeframe_confirmation": mtf.get("higher_timeframe_confirmation"),
                "timeframes": mtf.get("timeframes")},
        "radar": {key: radar.get(key) for key in ("trap_score", "trap_level", "breakout_quality", "entry_timing",
                                                  "squeeze", "fomo_risk", "wick_signal")},
        "quality_checks": item.get("checks"),
        "levels": {"stop": analysis["stop_loss"], "tp1_signal": analysis["tp1"], "tp2": analysis["tp2"],
                   "tp3": analysis["tp3"], "stop_pct": abs(price - analysis["stop_loss"]) / price * 100},
        "market_ranked": item.get("ranked"), "profile_cap_pass": item.get("profile_cap_pass"),
        "model_costs": costs, "model_notes": MODEL_NOTES,
    }
    return sanitize(record)


def open_virtual(record: dict[str, Any], costs: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Sanal işlemi canlı V2'nin seviyeleriyle kurar. (işlem, None) ya da (None, red nedeni)."""
    direction = record["direction"]
    sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
    levels = record["levels"]
    entry, stop, signal_tp1, tp3 = (_num(v) for v in (record["price"], levels["stop"], levels["tp1_signal"], levels["tp3"]))
    if not sign or None in (entry, stop, signal_tp1, tp3) or entry <= 0:
        return None, "invalid_levels"
    fee_bps, slip_bps = _num(costs.get("fee_bps")), _num(costs.get("slip_bps"))
    if fee_bps is None or slip_bps is None or fee_bps < 0 or slip_bps < 0:
        return None, "costs_unavailable"
    if sign == 1:
        valid = stop < entry < signal_tp1 <= tp3
    else:
        valid = tp3 <= signal_tp1 < entry < stop
    if not valid:
        return None, "invalid_levels"
    tp1 = entry + (signal_tp1 - entry) * TP1_PRICE_RATIO
    if not (entry < tp1 <= tp3 if sign == 1 else tp3 <= tp1 < entry):
        return None, "invalid_levels"
    fee, slip = fee_bps / 10_000, slip_bps / 10_000
    actual_entry = entry * (1 + sign * slip)
    risk_unit = abs(entry - stop) + entry * (fee_bps + slip_bps) * 2 / 10_000  # canlı estimated_stop_loss ile aynı biçim
    return {
        "schema": SCHEMA, "id": record["id"], "group": record["group"], "symbol": record["symbol"],
        "direction": direction, "sign": sign, "signal_time": record["signal_time"],
        "entry_time": record["decision_time"], "last_time": record["decision_time"] - CANDLE_SECONDS,
        "entry_reference": entry, "entry_actual": actual_entry, "stop": stop, "tp1": tp1, "tp3": tp3,
        "tp1_signal": signal_tp1, "tp1_fraction": TP1_FRACTION, "fee_rate": fee, "slip_rate": slip,
        "fee_bps": fee_bps, "slip_bps": slip_bps, "risk_unit": risk_unit,
        "remaining": 1.0, "tp1_done": False, "gross": 0.0, "fees": actual_entry * fee,
        "exits": [], "ambiguous_bars": 0, "status": "OPEN", "resolved": True, "close_reason": None,
        "closed_at": None,
    }, None


def _fill(trade: dict[str, Any], expected: float, fraction: float, at: int, reason: str) -> None:
    sign = trade["sign"]
    actual = expected * (1 - sign * trade["slip_rate"])
    fee = fraction * actual * trade["fee_rate"]
    trade["gross"] += fraction * sign * (actual - trade["entry_actual"])
    trade["fees"] += fee
    trade["remaining"] = max(0.0, trade["remaining"] - fraction)
    trade["exits"].append({"time": at, "reason": reason, "fraction": fraction, "expected_price": expected,
                           "actual_price": actual, "fee_per_unit": fee})
    if trade["remaining"] <= 1e-12:
        trade["remaining"] = 0.0


def _finish(trade: dict[str, Any], at: int, reason: str, resolved: bool = True) -> None:
    trade["status"], trade["closed_at"], trade["resolved"] = "CLOSED", at, resolved
    reasons = [exit_["reason"] for exit_ in trade["exits"]]
    trade["close_reason"] = reason if not resolved else "+".join(reasons)
    net = trade["gross"] - trade["fees"]
    trade["net_per_unit"] = net
    trade["net_r"] = net / trade["risk_unit"] if trade["risk_unit"] > 0 else None
    trade["gross_r"] = trade["gross"] / trade["risk_unit"] if trade["risk_unit"] > 0 else None
    trade["fee_r"] = trade["fees"] / trade["risk_unit"] if trade["risk_unit"] > 0 else None
    trade["duration_seconds"] = at - trade["entry_time"]


def advance(trade: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Yeni KAPANMIŞ 15m mumlarını sırayla işler (çevrimdışı Position.advance, STOP_FIRST ile aynı mantık)."""
    sign = trade["sign"]
    for candle in sorted(candles, key=lambda c: c["time"]):
        if trade["status"] != "OPEN":
            break
        opened = int(candle["time"])
        if opened <= trade["last_time"] or opened < trade["entry_time"]:
            continue
        if opened > trade["last_time"] + CANDLE_SECONDS:  # eksik mum: sonuç doğrulanamaz
            _finish(trade, trade["last_time"] + CANDLE_SECONDS, "DATA_GAP", resolved=False)
            break
        trade["last_time"] = opened
        closed_at = opened + CANDLE_SECONDS
        adverse = candle["low"] if sign == 1 else candle["high"]
        favorable = candle["high"] if sign == 1 else candle["low"]
        stop_hit = sign * (adverse - trade["stop"]) <= 0
        tp1_hit = not trade["tp1_done"] and sign * (favorable - trade["tp1"]) >= 0
        tp3_hit = sign * (favorable - trade["tp3"]) >= 0
        if stop_hit and (tp1_hit or tp3_hit):
            trade["ambiguous_bars"] += 1
        if stop_hit:  # STOP_FIRST: belirsiz mumda da stop önce; yalnız hedef değdiyse aşağıdaki kollar çalışır
            _fill(trade, trade["stop"], trade["remaining"], closed_at, "STOP")
            _finish(trade, closed_at, "STOP")
            break
        if tp1_hit:
            _fill(trade, trade["tp1"], min(trade["tp1_fraction"], trade["remaining"]), closed_at, "TP1")
            trade["tp1_done"] = True
        if tp3_hit and trade["remaining"] > 0:
            _fill(trade, trade["tp3"], trade["remaining"], closed_at, "TP3")
        if trade["remaining"] <= 0:
            _finish(trade, closed_at, "TP")
            break
        if closed_at - trade["entry_time"] > MAX_OPEN_SECONDS:
            _fill(trade, candle["close"], trade["remaining"], closed_at, "EXPIRED")
            _finish(trade, closed_at, "EXPIRED", resolved=False)
            break
    return trade


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Grup başına kapanmış ve çözülmüş sanal işlemlerin özeti (rapor aracı için)."""
    result: dict[str, Any] = {}
    for group in GROUPS:
        rows = [t for t in trades if t.get("group") == group and t.get("resolved") and _num(t.get("net_r")) is not None]
        unresolved = sum(1 for t in trades if t.get("group") == group and not t.get("resolved"))
        r = [float(t["net_r"]) for t in rows]
        gains = sum(x for x in r if x > 0)
        losses = -sum(x for x in r if x < 0)
        result[group] = {"N": len(r), "unresolved": unresolved, "mean_net_r": sum(r) / len(r) if r else None,
                         "win_rate": sum(1 for x in r if x > 0) / len(r) if r else None,
                         "profit_factor_r": gains / losses if losses > 0 else None,
                         "ambiguous_bars": sum(int(t.get("ambiguous_bars") or 0) for t in rows)}
    return result


# ---------------------------------------------------------------- kayıt (çalışan iş parçacığına ait)

class Recorder:
    """Durumu ve dosyaları yalnız tek bir iş parçacığı kullanır (ShadowWorker)."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = self._load()
        self.cap_reached = False

    # -- dosyalar
    def _load(self) -> dict[str, Any]:
        path = self.folder / "state.json"
        fresh = {"schema": SCHEMA, "seen": {}, "open": {}, "counters": {
            "scans": 0, "signals": {group: 0 for group in GROUPS}, "virtual_opened": 0, "virtual_closed": 0,
            "virtual_skipped": {}}}
        if not path.exists():
            return fresh
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("schema") != SCHEMA or not isinstance(loaded.get("seen"), dict) or not isinstance(loaded.get("open"), dict):
                raise ValueError("bad state")
            loaded.setdefault("counters", fresh["counters"])
            return loaded
        except (OSError, ValueError, TypeError):
            try:
                path.rename(path.with_name(f"state.corrupt-{int(time.time())}.json"))
            except OSError:
                pass
            logger.error("ORIGINAL_SHADOW_STATE_CORRUPT")
            return fresh

    def _append(self, name: str, row: dict[str, Any]) -> bool:
        path = self.folder / name
        if path.exists() and path.stat().st_size > MAX_FILE_BYTES:
            self.cap_reached = True
            return False
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(sanitize(row), allow_nan=False, sort_keys=True) + "\n")
        return True

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        path = self.folder / name
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(sanitize(payload), allow_nan=False, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    # -- ana akış
    def process(self, scan: dict[str, Any]) -> None:
        at, symbols, costs = int(scan["at"]), scan["symbols"], scan.get("costs")
        counters = self.state["counters"]
        open_trades: dict[str, dict[str, Any]] = self.state["open"]
        for key, trade in list(open_trades.items()):
            data = symbols.get(trade["symbol"])
            if data is None:
                continue
            advance(trade, data.get("candles") or [])
            if trade["status"] == "CLOSED":
                self._append("trades.jsonl", {**trade, "model_notes": MODEL_NOTES})
                del open_trades[key]
                counters["virtual_closed"] += 1
        seen: dict[str, int] = self.state["seen"]
        for symbol, data in symbols.items():
            item = data.get("item")
            if not item:
                continue
            group = classify(item)
            if group is None:
                continue
            identifier = f"{group}|{symbol}|{item['signal_time']}"
            if identifier in seen:  # aynı mum tekrar tekrar yeni sinyal sayılmaz
                continue
            record = signal_record(item, group, time.time(), costs)
            key = f"{group}|{symbol}"
            trade, reason = None, None
            if costs is None:
                reason = "costs_unavailable"
            elif group == GROUP_BLOCKED and item.get("profile_cap_pass") is False:
                reason = "profile_cap"
            elif key in open_trades:
                reason = "virtual_position_open"
            else:
                trade, reason = open_virtual(record, costs)
            record["virtual"] = {"opened": trade is not None, "skip_reason": reason}
            self._append("signals.jsonl", record)
            seen[identifier] = at
            counters["signals"][group] = counters["signals"].get(group, 0) + 1
            if trade is not None:
                open_trades[key] = trade
                counters["virtual_opened"] += 1
            else:
                counters["virtual_skipped"][reason] = counters["virtual_skipped"].get(reason, 0) + 1
        if len(seen) > SEEN_LIMIT:
            for identifier, _ in sorted(seen.items(), key=lambda pair: pair[1])[: len(seen) - SEEN_LIMIT]:
                del seen[identifier]
        counters["scans"] += 1
        self._write_json("state.json", self.state)
        self._write_json("status.json", {"schema": SCHEMA, "updated_at": time.time(), "last_scan_decision_time": at,
                                         "open_virtual": len(open_trades), "counters": counters,
                                         "file_cap_reached": self.cap_reached, "model_notes": MODEL_NOTES})


class ShadowWorker:
    """Bounded, bloklamayan kuyruk + tek arka plan iş parçacığı. Hiçbir hata dışarı çıkmaz."""

    def __init__(self, folder: Path | None = None, capacity: int = 8) -> None:
        self.folder = folder
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue(capacity)
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.dropped = 0
        self.failures = 0
        self.last_error: str | None = None
        self.recorder: Recorder | None = None

    def publish(self, scan: dict[str, Any]) -> bool:
        try:
            if self.thread is None or not self.thread.is_alive():
                if not self.lock.acquire(blocking=False):
                    self.dropped += 1
                    return False
                try:
                    if self.thread is None or not self.thread.is_alive():
                        self.thread = threading.Thread(target=self.run, name="original-v2-shadow", daemon=True)
                        self.thread.start()
                finally:
                    self.lock.release()
            self.queue.put_nowait(scan)
            return True
        except Exception as exc:  # kuyruk dolu dahil: say, devam et
            self.dropped += 1
            self.last_error = type(exc).__name__
            return False

    def run(self) -> None:
        while True:
            scan = self.queue.get()
            try:
                if self.recorder is None:
                    self.recorder = Recorder(self.folder or directory())
                self.recorder.process(scan)
            except Exception as exc:  # noqa: BLE001 - hiçbir hata canlı akışa yayılmaz
                self.failures += 1
                self.last_error = type(exc).__name__
                try:
                    logger.error("ORIGINAL_SHADOW_FAILED error_type=%s", type(exc).__name__)
                except Exception:
                    pass
                self.recorder = None  # sonraki turda temiz yeniden aç
            finally:
                self.queue.task_done()


_worker: ShadowWorker | None = None
_costs_cache: dict[str, Any] | None = None


def _get_worker() -> ShadowWorker:
    global _worker
    if _worker is None:
        _worker = ShadowWorker()
    return _worker


def _costs() -> dict[str, Any] | None:
    """Komisyon/kayma: canlı V2'nin sabit execution_policy değerleri (canlı bunların yalnız 'en az' olanını kullanır)."""
    global _costs_cache
    if _costs_cache is None:
        try:
            from .strategies.original_v2_demo import fixed_profile
            policy = fixed_profile()["execution_policy"]
            _costs_cache = {"fee_bps": float(policy["fee_bps_per_side"]), "slip_bps": float(policy["slippage_bps_per_side"]),
                            "source": "fixed_profile.execution_policy"}
        except Exception:  # noqa: BLE001
            return None
    return _costs_cache


def _profile_cap_pass(entry: Any, stop: Any) -> bool | None:
    try:
        from .strategies.original_gap_risk import PROFILE
        return bool(PROFILE.accepts(float(entry), float(stop)))
    except Exception:  # noqa: BLE001
        return None


def build_scan(at: int, observed: dict[str, Any], ranked: set[str], eligible_symbols: set[str],
               costs: dict[str, Any] | None, cap_check: Any = None) -> dict[str, Any]:
    """Canlı tarama sonuçlarını düz veriye çevirir (StrategyResult benzeri nesneleri okur, değiştirmez)."""
    symbols: dict[str, Any] = {}
    for symbol, data in observed.items():
        candles = data["candles"]
        result = data.get("result")
        entry: dict[str, Any] = {"candles": candles, "item": None}
        legacy = getattr(result, "legacy", None) or {}
        analysis = legacy.get("analysis")
        if isinstance(analysis, dict) and analysis.get("direction") in {"LONG", "SHORT"} and legacy.get("signal_timestamp") is not None:
            checks = {check.key: bool(check.passed) for check in getattr(getattr(result, "signal", None), "quality_checks", ()) or ()}
            item = {
                "symbol": symbol, "signal_time": int(legacy["signal_timestamp"]), "decision_time": int(at),
                "analysis": {key: copy.deepcopy(value) for key, value in analysis.items() if key != "series"},
                "mtf": copy.deepcopy(legacy.get("mtf") or {}), "checks": checks,
                "ranked": symbol in ranked, "eligible": symbol in eligible_symbols,
                "recent": [dict(row) for row in candles[-8:]],
            }
            if analysis.get("direction") == "SHORT" and (cap_check is not None):
                item["profile_cap_pass"] = cap_check(analysis.get("entry"), analysis.get("stop_loss"))
            entry["item"] = item
        symbols[symbol] = entry
    return {"at": int(at), "symbols": symbols, "costs": costs}


def observe_scan(at: int, observed: dict[str, Any], ranked: set[str], eligible_symbols: set[str]) -> None:
    """Canlı tarama sonunda çağrılır. Hiçbir koşulda istisna fırlatmaz."""
    try:
        if not enabled():
            return
        scan = build_scan(at, observed, ranked, eligible_symbols, _costs(), _profile_cap_pass)
        _get_worker().publish(scan)
    except Exception as exc:  # noqa: BLE001
        try:
            if _worker is not None:
                _worker.failures += 1
                _worker.last_error = type(exc).__name__
            logger.error("ORIGINAL_SHADOW_OBSERVE_FAILED error_type=%s", type(exc).__name__)
        except Exception:
            pass
