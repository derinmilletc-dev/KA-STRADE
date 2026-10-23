import time

from app import original_v2_live as original
from app import v25_execution
from app.v25_execution import LIVE_CLIENT_PREFIX, initial_state


def snapshot(positions=(), algos=()):
    return {
        "wallet_balance": 100.0, "available_balance": 100.0,
        "positions": [{"symbol": s, "position_side": "BOTH", "direction": "SHORT", "quantity": 1.0} for s in positions],
        "open_orders": [],
        "open_algo_orders": [
            {"symbol": s, "algo_id": 1, "client_algo_id": LIVE_CLIENT_PREFIX + "STOP_x", "side": "BUY", "type": "STOP_MARKET", "status": "NEW"}
            for s in algos
        ],
        "open_algo_orders_available": True,
        "algo_orders_quality": "VALID_ORDERS" if algos else "VALID_EMPTY",
    }


def paused_state(reason=None, age=30.0, expires_in=3600.0):
    state = initial_state()
    state["execution_state"] = "LOCKED"
    state["reconciliation_required"] = False
    state["emergency"].update({"active": False, "reason": "TRANSIENT_MARKET_DATA_RECOVERED"})
    state["real_trading_locked"] = True
    state["auto_authorization"] = {"user_id": "owner", "session_id": "s", "fingerprint": "f"}
    state["original_v2_live"] = {
        "owner_id": "owner", "enabled": False, "paused": True,
        "pause_reason": reason or (original.TRANSIENT_PAUSE_REASON_PREFIX + "."),
        "paused_at_epoch": time.time() - age,
        "session_expires_at_epoch": time.time() + expires_in,
    }
    return state


def test_resumes_after_clean_transient_pause():
    state = paused_state()
    assert original.resume_after_transient_pause(state, snapshot()) is True
    config = state["original_v2_live"]
    assert config["enabled"] is True and config["paused"] is False
    assert state["real_trading_locked"] is False and state["live_auto_trade"] is True
    assert state["auto"]["enabled"] is True
    assert any(e["kind"] == "ORIGINAL_LIVE_RESUMED" for e in state["events"])


def test_other_pause_reason_never_resumes():
    state = paused_state(reason="Original V2 oturum süresi doldu; yeni oturum için ARM ve güncel 2FA gerekli.")
    assert original.resume_after_transient_pause(state, snapshot()) is False
    assert state["original_v2_live"]["paused"] is True


def test_old_pause_never_resumes():
    state = paused_state(age=original.TRANSIENT_RESUME_WINDOW_SECONDS + 5)
    assert original.resume_after_transient_pause(state, snapshot()) is False


def test_expired_session_never_resumes():
    state = paused_state(expires_in=-1.0)
    assert original.resume_after_transient_pause(state, snapshot()) is False


def test_active_emergency_never_resumes():
    state = paused_state()
    state["emergency"].update({"active": True, "reason": "RECONCILIATION_FAILURE"})
    assert original.resume_after_transient_pause(state, snapshot()) is False


def test_unexplained_position_never_resumes():
    state = paused_state()
    assert original.resume_after_transient_pause(state, snapshot(["FILUSDT"], ["FILUSDT"])) is False


def test_resumes_with_confirmed_protected_position():
    state = paused_state()
    state["plans"] = {"p1": {
        "symbol": "FILUSDT", "status": "KORUMA AKTİF", "provenance_state": "CONFIRMED",
        "protection_state": "MATCHED", "protection_match_reason": "EXACT_IDENTITY",
    }}
    assert original.resume_after_transient_pause(state, snapshot(["FILUSDT"], ["FILUSDT"])) is True


def test_unknown_order_evidence_blocks_resume():
    state = paused_state()
    state["events"].insert(0, {"kind": "UNKNOWN_ORDER_STATE"})
    assert original.resume_after_transient_pause(state, snapshot()) is False
