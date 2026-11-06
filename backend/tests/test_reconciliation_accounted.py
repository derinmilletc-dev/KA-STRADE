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


def test_stale_intents_cleared_even_when_other_mismatch_keeps_lock():
    state = locked_state()
    for coin in ("PYTHUSDT", "APRUSDT"):
        state["intents"][f"i-{coin}"] = {"symbol": coin, "client_order_id": v25_execution.client_id_for("ENTRY", f"i-{coin}")}
    # UNIUSDT'de planı olmayan, sisteme ait emir: kilit kalmalı, ama sonuçsuz niyetler temizlenmeli ve sebep yazılmalı.
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT", "UNIUSDT"])) is False
    assert not state["intents"]
    assert "UNIUSDT" in state["clean_reconciliation_block"]
    assert any("UNIUSDT" in item for item in v25_execution.reconciliation_blockers(state))


def test_intent_with_open_order_on_symbol_is_not_stale():
    state = locked_state()
    state["intents"]["i-uni"] = {"symbol": "UNIUSDT", "client_order_id": v25_execution.client_id_for("ENTRY", "i-uni")}
    v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT", "UNIUSDT"]))
    assert "i-uni" in state["intents"]


def test_stale_intents_with_accounted_position_clear_lock():
    state = locked_state()
    state["intents"]["i-q"] = {"symbol": "QUSDT", "client_order_id": v25_execution.client_id_for("ENTRY", "i-q")}
    assert v25_execution.clear_clean_reconciliation_state(state, snapshot(["FILUSDT"], ["FILUSDT"])) is True
    assert state["emergency"]["active"] is False and not state["intents"]
