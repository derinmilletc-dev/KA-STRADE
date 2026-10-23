from app import v25_execution
from app.v25_execution import LIVE_CLIENT_PREFIX, initial_state


def snapshot(positions, algos=()):
    return {
        "wallet_balance": 100.0, "available_balance": 100.0,
        "positions": [{"symbol": s, "position_side": "BOTH", "direction": "SHORT", "quantity": 1.0} for s in positions],
        "open_orders": [],
        "open_algo_orders": [
            {"symbol": s, "algo_id": 1, "client_algo_id": LIVE_CLIENT_PREFIX + "STOP_x", "side": "BUY", "type": "STOP_MARKET", "status": "NEW"}
            for s in algos
        ],
        "open_algo_orders_available": True, "algo_orders_quality": "VALID_ORDERS",
    }


def locked_state(**plan_overrides):
    state = initial_state()
    state["emergency"].update({"active": True, "reason": "RECONCILIATION_FAILURE"})
    state["execution_state"] = "UNKNOWN"
    state["reconciliation_required"] = True
    state["events"].insert(0, {"kind": "LIVE_UNKNOWN_EXECUTION", "reason": "RECONCILIATION_FAILURE", "symbol": None})
    plan = {
        "symbol": "FILUSDT", "status": "KORUMA AKTİF", "provenance_state": "CONFIRMED",
        "protection_state": "MATCHED", "protection_match_reason": "EXACT_IDENTITY",
    }
    plan.update(plan_overrides)
    state["plans"] = {"p1": plan}
    return state


def test_confirmed_protected_positions_clear_transient_lock():
    state = locked_state()
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT"])) is True
    assert state["reconciliation_required"] is False
    assert state["real_trading_locked"] is True  # yeniden başlatma gerekir
    assert state["emergency"]["active"] is False


def test_unprotected_plan_keeps_lock():
    state = locked_state(protection_state="MISSING")
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT"])) is False
    assert state["reconciliation_required"] is True


def test_position_without_plan_keeps_lock():
    state = locked_state()
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT", "UNIUSDT"], ["FILUSDT"])) is False


def test_orphan_owned_order_keeps_lock():
    state = locked_state()
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT", "UNIUSDT"])) is False


def test_unknown_order_event_keeps_lock():
    state = locked_state()
    state["events"].insert(0, {"kind": "UNKNOWN_ORDER_STATE"})
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT"])) is False


def test_other_emergency_reason_unchanged():
    state = locked_state()
    state["emergency"]["reason"] = "Kullanıcı canlı acil durdurma komutu"
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT"])) is False
