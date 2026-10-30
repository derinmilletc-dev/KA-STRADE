import asyncio
from unittest.mock import AsyncMock, patch

from app import v25_execution as v
from app.v25_execution import initial_state


def plan(**extra):
    base = {"id": "p1", "intent_id": "i1", "symbol": "BTCUSDT", "direction": "LONG", "source": "MANUAL",
            "stop_loss": "90", "initial_risk_usdt": "10"}
    base.update(extra)
    return base


def run(p, position):
    close = AsyncMock(return_value={"orderId": 1})
    with patch.object(v, "close_tracked_symbol", new=close), patch.object(v, "persist_state"):
        result = asyncio.run(v.manual_fill_risk_audit(object(), initial_state(), p, position))
    return result, close


def test_ok_fill_keeps_position():
    p = plan()
    result, close = run(p, {"entry_price": "100", "quantity": "1"})
    assert not result and close.await_count == 0 and p["manual_fill_audit"]["result"] == "OK"


def test_slippage_within_tolerance_keeps_position():
    # Gerçek risk planın 1,4 katı: %50 toleransın içinde, pozisyon kapanmaz.
    p = plan()
    result, close = run(p, {"entry_price": "100", "quantity": "1.4"})
    assert not result and close.await_count == 0 and p["manual_fill_audit"]["result"] == "OK"


def test_observed_slippage_case_keeps_position():
    # Gerçek vaka: planlanan risk 0,5215, gerçek risk ~0,661 (oran ~1,27) eskiden %25 sınırında kapanıyordu.
    p = plan(stop_loss="0.057860", initial_risk_usdt="0.5215")
    result, close = run(p, {"entry_price": "0.058633", "quantity": "855"})
    assert not result and close.await_count == 0 and p["manual_fill_audit"]["result"] == "OK"


def test_slippage_over_tolerance_closes():
    p = plan()
    result, close = run(p, {"entry_price": "100", "quantity": "1.6"})
    assert result and close.await_count == 1 and p["manual_fill_audit"]["result"] == "BREACH"
    assert p["close_reason"] == "FILL_RISK"


def test_stop_on_wrong_side_closes():
    p = plan(stop_loss="101")
    result, close = run(p, {"entry_price": "100", "quantity": "0.1"})
    assert result and close.await_count == 1


def test_other_sources_untouched_and_audit_runs_once():
    p = plan(source="ORIGINAL_V2")
    assert not run(p, {"entry_price": "100", "quantity": "9"})[0] and "manual_fill_audit" not in p
    p = plan()
    run(p, {"entry_price": "100", "quantity": "1"})
    result, close = run(p, {"entry_price": "100", "quantity": "9"})
    assert not result and close.await_count == 0


def test_unverifiable_input_keeps_stop_and_position():
    p = plan(initial_risk_usdt=None)
    result, close = run(p, {"entry_price": "100", "quantity": "1"})
    assert not result and close.await_count == 0 and p["manual_fill_audit"]["result"] == "UNVERIFIED"


def test_fillrisk_intent_has_its_own_close_reason():
    assert v.close_reason_for_intent("fillrisk-p1") == "FILL_RISK"
    assert v.close_reason_for_intent("protection-p1") == "STOP"
