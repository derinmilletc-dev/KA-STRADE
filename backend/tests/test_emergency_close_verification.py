"""Canlı acil durdurma: önce kapat, borsadan doğrula, korumayı SONRA iptal et.

Sahte borsa; gerçek emir yok. Bağlantı kopması, yanıt vermeyen API ve doğrulanamayan kapanışta
koruma (stop/hedef) emirlerinin yerinde kaldığını kanıtlar.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import v25_execution  # noqa: E402
from app.v25_execution import LiveExchangeError  # noqa: E402

SYMBOL = "BTCUSDT"


def make_plan(plan_id="p1", symbol=SYMBOL):
    return {
        "id": plan_id, "provenance_state": "CONFIRMED", "symbol": symbol, "status": "KORUMA AKTİF",
        "intent_id": f"intent-{plan_id}-0001", "stop_client_id": f"STOP_{plan_id}",
        "entry_client_order_id": None, "close_client_order_ids": [],
    }


def algo_row(plan, algo_id=11, symbol=SYMBOL):
    return {"symbol": symbol, "algo_id": algo_id, "client_algo_id": plan["stop_client_id"]}


class FakeExchange:
    def __init__(self, position_reads, close_error=None):
        self.calls = []
        self.position_reads = list(position_reads)  # her okuma: "flat" | "open" | "error"
        self.close_error = close_error

    async def signed(self, method, path, params=None):
        self.calls.append((method, path, dict(params or {})))
        if path == "/fapi/v3/positionRisk":
            kind = self.position_reads.pop(0) if len(self.position_reads) > 1 else self.position_reads[0]
            if kind == "error":
                raise LiveExchangeError("API yanıt vermedi", unknown_execution=True)
            return [{"symbol": SYMBOL, "positionAmt": "0" if kind == "flat" else "0.5"}]
        if path == "/fapi/v1/openAlgoOrders":
            return []
        return {}

    def deleted_algos(self):
        return [call for call in self.calls if call[0] == "DELETE" and call[1] == "/fapi/v1/algoOrder"]


def run_emergency(plan, exchange, *, close_result=None, close_error=None, algos=None, close_positions=True):
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    state["plans"] = {plan["id"]: plan}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = v25_execution.EmergencyRequest(confirmation="CANLI ACİL DURDUR", close_tracked_positions=close_positions)
    snapshot = {"positions": [], "open_orders": [], "open_algo_orders": algos if algos is not None else [algo_row(plan)]}
    if close_error is not None:
        close_mock = AsyncMock(side_effect=close_error)
    else:
        close_mock = AsyncMock(return_value=close_result)

    async def go():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "owner-1"}), \
                patch.object(v25_execution, "client_for", return_value=exchange), \
                patch.object(v25_execution, "account_snapshot", new=AsyncMock(return_value=snapshot)), \
                patch.object(v25_execution, "close_tracked_symbol", new=close_mock), \
                patch.object(v25_execution, "persist_state"), \
                patch.object(v25_execution, "EMERGENCY_VERIFY_DELAY_SECONDS", 0):
            return await v25_execution.v25_emergency(request, body)

    return state, go, close_mock


def test_verified_close_then_protection_cancelled():
    plan = make_plan()
    exchange = FakeExchange(["flat"])
    state, go, close_mock = run_emergency(plan, exchange, close_result={"orderId": 77})
    response = asyncio.run(go())
    assert response["ok"] is True and response["closed_tracked_positions"] == 1
    assert response["unverified_symbols"] == []
    assert len(exchange.deleted_algos()) == 1
    assert plan["status"] == "ACİL DURDURULDU"
    assert state["emergency"]["active"] is True
    assert 77 in plan["exchange_order_ids"]
    close_mock.assert_awaited_once()


def test_close_failure_keeps_protection_and_reports():
    plan = make_plan()
    exchange = FakeExchange(["open"])
    state, go, _ = run_emergency(plan, exchange, close_error=LiveExchangeError("Binance bağlantısı koptu"))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(go())
    assert caught.value.status_code == 502 and SYMBOL in caught.value.detail
    assert exchange.deleted_algos() == []              # stop ve hedefler yerinde
    assert plan["status"] == "KORUMA AKTİF"            # plan kapandı sayılmadı
    assert plan.get("emergency_close_unverified_at")
    assert state["emergency"]["active"] is True        # yeni girişler yine kilitli


def test_close_sent_but_position_still_open_keeps_protection():
    plan = make_plan()
    exchange = FakeExchange(["open"])
    _, go, _ = run_emergency(plan, exchange, close_result={"orderId": 5})
    with pytest.raises(HTTPException):
        asyncio.run(go())
    assert exchange.deleted_algos() == []
    assert plan["status"] == "KORUMA AKTİF"


def test_unreadable_position_after_close_is_never_assumed_closed():
    plan = make_plan()
    exchange = FakeExchange(["error"])
    _, go, _ = run_emergency(plan, exchange, close_result={"orderId": 5})
    with pytest.raises(HTTPException):
        asyncio.run(go())
    assert exchange.deleted_algos() == []
    assert plan["status"] == "KORUMA AKTİF"


def test_position_read_recovers_on_retry_then_protection_is_cancelled():
    plan = make_plan()
    exchange = FakeExchange(["error", "open", "flat"])
    _, go, _ = run_emergency(plan, exchange, close_result={"orderId": 5})
    response = asyncio.run(go())
    assert response["ok"] is True
    assert len(exchange.deleted_algos()) == 1
    assert plan["status"] == "ACİL DURDURULDU"


def test_already_flat_position_still_clears_leftover_protection():
    plan = make_plan()
    exchange = FakeExchange(["flat"])
    _, go, _ = run_emergency(plan, exchange, close_result=None)
    response = asyncio.run(go())
    assert response["closed_tracked_positions"] == 0
    assert len(exchange.deleted_algos()) == 1


def test_one_symbol_failing_does_not_block_the_other():
    plan_a, plan_b = make_plan("a", "BTCUSDT"), make_plan("b", "ETHUSDT")
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    state["plans"] = {"a": plan_a, "b": plan_b}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = v25_execution.EmergencyRequest(confirmation="CANLI ACİL DURDUR", close_tracked_positions=True)
    snapshot = {"positions": [], "open_orders": [], "open_algo_orders": [algo_row(plan_a, 1, "BTCUSDT"), algo_row(plan_b, 2, "ETHUSDT")]}
    calls = []

    class Exchange:
        async def signed(self, method, path, params=None):
            calls.append((method, path, dict(params or {})))
            if path == "/fapi/v3/positionRisk":
                amount = "0.5" if params["symbol"] == "BTCUSDT" else "0"
                return [{"symbol": params["symbol"], "positionAmt": amount}]
            return [] if path == "/fapi/v1/openAlgoOrders" else {}

    async def close(client, symbol, intent):
        if symbol == "BTCUSDT":
            raise LiveExchangeError("kapanış reddedildi")
        return {"orderId": 9}

    async def go():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "o"}), \
                patch.object(v25_execution, "client_for", return_value=Exchange()), \
                patch.object(v25_execution, "account_snapshot", new=AsyncMock(return_value=snapshot)), \
                patch.object(v25_execution, "close_tracked_symbol", new=close), \
                patch.object(v25_execution, "persist_state"), \
                patch.object(v25_execution, "EMERGENCY_VERIFY_DELAY_SECONDS", 0):
            return await v25_execution.v25_emergency(request, body)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(go())
    assert "BTCUSDT" in caught.value.detail and "ETHUSDT" not in caught.value.detail
    deleted = [c for c in calls if c[0] == "DELETE" and c[1] == "/fapi/v1/algoOrder"]
    assert [c[2]["symbol"] for c in deleted] == ["ETHUSDT"]
    assert plan_b["status"] == "ACİL DURDURULDU" and plan_a["status"] == "KORUMA AKTİF"


def test_without_close_flag_old_behaviour_cancels_protection_only():
    plan = make_plan()
    exchange = FakeExchange(["open"])
    _, go, close_mock = run_emergency(plan, exchange, close_positions=False)
    response = asyncio.run(go())
    assert response["ok"] is True
    close_mock.assert_not_awaited()
    assert len(exchange.deleted_algos()) == 1


def test_connection_lost_before_snapshot_changes_nothing():
    plan = make_plan()
    exchange = FakeExchange(["open"])
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    state["plans"] = {plan["id"]: plan}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = v25_execution.EmergencyRequest(confirmation="CANLI ACİL DURDUR", close_tracked_positions=True)

    async def go():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "o"}), \
                patch.object(v25_execution, "client_for", return_value=exchange), \
                patch.object(v25_execution, "account_snapshot", new=AsyncMock(side_effect=LiveExchangeError("bağlantı yok"))), \
                patch.object(v25_execution, "persist_state"):
            return await v25_execution.v25_emergency(request, body)

    with pytest.raises(HTTPException):
        asyncio.run(go())
    assert exchange.deleted_algos() == [] and plan["status"] == "KORUMA AKTİF"
