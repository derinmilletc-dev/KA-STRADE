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


def test_slippage_over_tolerance_closes():
    p = plan()
    result, close = run(p, {"entry_price": "100", "quantity": "1.5"})
    assert result and close.await_count == 1 and p["manual_fill_audit"]["result"] == "BREACH"


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
