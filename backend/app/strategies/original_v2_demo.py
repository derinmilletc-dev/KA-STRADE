"""Inert stage-A Original v2 Demo decisions; no runtime or order integration.

The fixed file pins the measurement policy, not production defaults. Canonical
eligibility, offline risk previews and account gates are separate from exchange
authorization. Inputs are already selected closed windows of at most 259 bars.
Stage B must separately preserve ownership, confirmation and protection gates.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

from ..execution_core import configured_min_confidence, evaluate_entry_gates
from .contracts import StrategyInput, StrategyProvenance, StrategyResult
from .kais_original import evaluate as evaluate_original
from .original_gap_risk import PROFILE
from .original_offline_risk import OfflineRiskError
from .provenance import parameter_hash

STRATEGY_ID = "kais-original-v2-demo-v1"
FEATURE_FLAG = "PROTREBOT_BINANCE_DEMO_KAIS_ORIGINAL_V2_ENABLED"
POLICY_PATH = Path(__file__).with_name("original_v2_demo_policy.json")
DOCUMENT_SHA256 = "be0178adeccb6e8bbe0974cae511a6809b80d8de1f7822ed60ab4cba973a1130"
EXECUTION_POLICY_SHA256 = "8196421755c55ec40b1f180eda31813f9a6d19f539b9058a46456c69b2a7bc48"
INTERVALS = ("15m", "1h", "4h")
CANDLE_FETCH_LIMIT = 260
# Yeni SHORT giriş filtresi: YALNIZ canlı girişte (evaluate_live_universe). Demo, backtest ve araştırma yolları eski davranışta kalır.
LIVE_SHORT_FILTER_RULES = {"extension_atr_min": 2.0, "rsi_min": 25.0, "support_atr_max": 0.5}
logger = logging.getLogger(__name__)


class DemoDecisionError(ValueError):
    """Explicit configuration/input failure, never an execution fallback."""


def _fail(code: str) -> NoReturn:
    logger.error("ORIGINAL_DEMO_STAGE_A_REJECTED code=%s", code)
    raise DemoDecisionError(code)


def fixed_profile() -> dict[str, Any]:
    try:
        document = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.error("ORIGINAL_DEMO_POLICY_UNREADABLE: %s", exc)
        raise DemoDecisionError("POLICY_UNREADABLE") from exc
    if not isinstance(document, dict) or parameter_hash(document) != DOCUMENT_SHA256:
        _fail("POLICY_DOCUMENT_CHANGED")
    if parameter_hash(document["execution_policy"]) != EXECUTION_POLICY_SHA256:
        _fail("REFERENCE_POLICY_CHANGED")
    if PROFILE.profile_hash != document["source_profile_sha256"]:
        _fail("SOURCE_PROFILE_CHANGED")
    return document


def feature_enabled() -> bool:
    value = os.getenv(FEATURE_FLAG, "false").strip().lower()
    if value in {"false", "0", "no", "off"}:
        return False
    if value in {"true", "1", "yes", "on"}:
        return True
    _fail("INVALID_FEATURE_FLAG")


def _require_enabled(enabled: bool | None) -> None:
    active = feature_enabled() if enabled is None else enabled
    if type(active) is not bool:
        _fail("INVALID_FEATURE_SELECTION")
    if not active:
        _fail("FEATURE_DISABLED")


def evaluate(request: StrategyInput, *, enabled: bool | None = None) -> StrategyResult:
    _require_enabled(enabled)
    profile = fixed_profile()
    if request.symbol not in profile["execution_policy"]["allowed_symbols"]:
        _fail("SYMBOL_OUTSIDE_FIXED_UNIVERSE")
    return _evaluate_profiled(request, profile)


def evaluate_live_universe(
    request: StrategyInput,
    *,
    allowed_symbols: list[str],
    enabled: bool | None = None,
) -> StrategyResult:
    _require_enabled(enabled)
    profile = fixed_profile()
    if (
        not isinstance(allowed_symbols, list)
        or not 1 <= len(allowed_symbols) <= 100
        or any(not isinstance(symbol, str) for symbol in allowed_symbols)
        or len(set(allowed_symbols)) != len(allowed_symbols)
        or any(not symbol.isascii() or not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT") for symbol in allowed_symbols)
    ):
        _fail("INVALID_LIVE_SYMBOL_UNIVERSE")
    if request.symbol not in allowed_symbols:
        _fail("SYMBOL_OUTSIDE_LIVE_UNIVERSE")
    # Canlıda güven eşiği sunucu ayarına (PROTREBOT_MIN_CONFIDENCE) uyar; yalnız yükseltebilir, sabit %80'in altına inmez.
    live_threshold = max(int(profile["canonical_policy"]["confidence_threshold"]), configured_min_confidence())
    return _evaluate_profiled(request, profile, short_filter_rules=LIVE_SHORT_FILTER_RULES, confidence_threshold=live_threshold)


def log_short_filter(symbol: str, legacy: dict[str, Any]) -> None:
    """Giriş taramasında her SHORT kararı için açık log (yalnız yeni filtre karar verdiğinde). Açık pozisyon izleme çağırmaz."""
    mtf = legacy.get("mtf") or {}
    v2 = mtf.get("short_filter_v2")
    if not isinstance(v2, dict) or not v2.get("gates_before_passed"):
        return  # yalnız filtrenin gerçekten karar verdiği (diğer kapıları geçmiş) SHORT adayları
    logger.info(
        "ORIGINAL_V2_SHORT_FILTER symbol=%s decision=%s reason=%s alignment=%s dir15m=%s dir1h=%s dir4h=%s rsi=%s "
        "extension_atr=%s support_distance_atr=%s legacy_alignment_would_block=%s version=%s",
        symbol, v2.get("decision"), v2.get("reason") or "-", v2.get("alignment"),
        *(v2["directions"].get(interval) for interval in ("15m", "1h", "4h")),
        v2.get("rsi"), v2.get("extension_atr"), v2.get("support_distance_atr"),
        mtf.get("legacy_short_alignment_would_block"), v2.get("version"),
    )
    if v2.get("missing_fields"):  # eksik veri, desteğe yakınlıktan ayrı ve daha görünür loglanır
        logger.warning(
            "ORIGINAL_V2_SHORT_FILTER_DATA_MISSING symbol=%s missing=%s reasons=%s",
            symbol, ",".join(v2["missing_fields"]), ",".join(v2.get("reasons") or ()),
        )


def short_filter_scan_counts(results: dict[str, StrategyResult]) -> dict[str, Any]:
    """Tarama başına: filtrenin karar verdiği SHORT sayısı, kabul/ret ve ret nedenleri (eksik veri ayrı)."""
    counts: dict[str, Any] = {"evaluated": 0, "accepted": 0, "rejected": 0, "by_reason": {},
                              "support_unavailable": 0, "support_too_close": 0, "data_unavailable": 0}
    for result in results.values():
        v2 = ((result.legacy or {}).get("mtf") or {}).get("short_filter_v2")
        if not isinstance(v2, dict) or not v2.get("gates_before_passed"):
            continue
        counts["evaluated"] += 1
        if v2.get("accepted"):
            counts["accepted"] += 1
            continue
        counts["rejected"] += 1
        for reason in v2.get("reasons") or ():
            counts["by_reason"][reason] = counts["by_reason"].get(reason, 0) + 1
        reasons = set(v2.get("reasons") or ())
        counts["support_unavailable"] += "SHORT_SUPPORT_UNAVAILABLE" in reasons
        counts["support_too_close"] += "SHORT_SUPPORT_TOO_CLOSE" in reasons
        counts["data_unavailable"] += "SHORT_FILTER_DATA_UNAVAILABLE" in reasons
    return counts


def _evaluate_profiled(request: StrategyInput, profile: dict[str, Any], *,
                       short_filter_rules: dict[str, Any] | None = None,
                       confidence_threshold: int | None = None) -> StrategyResult:
    if request.historical_policy_override is not None:
        _fail("CALLER_POLICY_OVERRIDE_DENIED")
    if request.short_filter_rules is not None:
        _fail("CALLER_SHORT_FILTER_OVERRIDE_DENIED")
    for interval in INTERVALS:
        rows = request.candles_by_timeframe.get(interval, [])
        if isinstance(rows, list) and len(rows) > profile["closed_window"]:
            _fail("CLOSED_WINDOW_EXCEEDED")
    canonical = deepcopy(profile["canonical_policy"])
    if confidence_threshold is not None:
        canonical["confidence_threshold"] = int(confidence_threshold)
    native = evaluate_original(replace(
        request,
        required_intervals=INTERVALS,
        historical_policy_override=canonical,
        market_type="BINANCE_DEMO_MODEL_ONLY",
        exit_policy_id=profile["model"]["exit_policy_id"],
        short_filter_rules=deepcopy(short_filter_rules),
    ))
    return StrategyResult(
        signal=replace(native.signal, provenance=StrategyProvenance(
            strategy_id=STRATEGY_ID,
            strategy_version="stage-a-v1",
            parameter_hash=DOCUMENT_SHA256,
        )),
        legacy=native.legacy,
    )


def risk_preview(entry: float, stop: float, *, enabled: bool | None = None) -> dict[str, Any]:
    _require_enabled(enabled)
    fixed_profile()
    try:
        return PROFILE.size(entry, stop)
    except OfflineRiskError as exc:
        logger.error("ORIGINAL_DEMO_RISK_PREVIEW_REJECTED code=%s", exc.reason)
        raise DemoDecisionError(exc.reason) from exc


def account_gates(
    result: StrategyResult,
    *,
    snapshot: dict[str, Any],
    daily: dict[str, Any],
    candidate_notional_usdt: float = 0,
    active_plans: list[dict[str, Any]] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    _require_enabled(enabled)
    profile = fixed_profile()
    if result.signal.provenance.strategy_id != STRATEGY_ID:
        _fail("STRATEGY_ID_MISMATCH")
    if result.signal.provenance.parameter_hash != DOCUMENT_SHA256:
        _fail("STRATEGY_PARAMETERS_MISMATCH")
    analysis = result.legacy.get("analysis")
    if not isinstance(analysis, dict):
        _fail("CANONICAL_ANALYSIS_UNAVAILABLE")
    return evaluate_entry_gates(
        symbol=result.signal.symbol,
        signal=deepcopy(analysis),
        snapshot=deepcopy(snapshot),
        policy=profile["execution_policy"],
        daily=deepcopy(daily),
        spread_bps=profile["model"]["spread_bps"],
        armed=True,
        allowed_symbols=profile["execution_policy"]["allowed_symbols"],
        active_plans=deepcopy(active_plans),
        candidate_notional_usdt=candidate_notional_usdt,
    )


def decision_record(result: StrategyResult) -> dict[str, Any]:
    """Return metadata only; do not persist or authorize an order."""
    profile = fixed_profile()
    if (
        result.signal.provenance.strategy_id != STRATEGY_ID
        or result.signal.provenance.parameter_hash != DOCUMENT_SHA256
    ):
        _fail("STRATEGY_PARAMETERS_MISMATCH")
    signal = result.signal
    return {
        "stage": "A_DECISION_ONLY",
        "strategy_id": STRATEGY_ID,
        "source_profile_id": profile["source_profile_id"],
        "source_profile_sha256": profile["source_profile_sha256"],
        "parameter_sha256": DOCUMENT_SHA256,
        "resolved_policy_sha256": EXECUTION_POLICY_SHA256,
        "execution_policy_id": signal.exit_policy_id,
        "symbol": signal.symbol,
        "decision_time": signal.decision_time,
        "signal_timestamp": signal.signal_open_time,
        "decision": signal.decision,
        "direction": signal.direction,
        "entry": signal.entry,
        "stop": signal.stop,
        "targets": [signal.tp1, signal.tp2, signal.tp3],
        "reason": signal.reason,
        "reasons": list(signal.reasons),
        "strategy_eligible": signal.strategy_eligible,
        "order_authorized": False,
        "execution_connected": False,
    }
