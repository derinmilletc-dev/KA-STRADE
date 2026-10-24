import asyncio
from unittest.mock import AsyncMock, patch

from app import v25_execution as v
from app.v25_execution import initial_state


def plan(**extra):
    base = {"id": "p1", "intent_id": "i1", "symbol": "BTCUSDT", "direction": "LONG", "source": "MANUAL",
            "quantity": "1", "stop_loss": "90", "stop_algo_id": 11, "tp12_algo_id": 22,
            "protection_state": "MATCHED"}
    base.update(extra)
    return base


POSITION = {"quantity": "0.4", "entry_price": "100", "mark_price": "110"}


class FakeClient:
    def __init__(self, filled=True):
        self.calls = []
        self.filled = filled

    async def signed(self, method, path, params=None):
        self.calls.append((method, path, params))
        if path == "/fapi/v1/algoOrder" and method == "GET":
            return {"algoId": 22, "actualOrderId": 5 if self.filled else 0}
        if path == "/fapi/v1/order":
            return {"orderId": 5, "symbol": "BTCUSDT", "status": "FILLED", "reduceOnly": True}
        return {}


def run(p, client, position=POSITION, open_algos=None, stop_ok=True):
    state = initial_state()
    rules = {"tick": v.Decimal("0.1"), "step": v.Decimal("0.001")}
    stop = AsyncMock(return_value={"algoId": 33}) if stop_ok else AsyncMock(side_effect=v.LiveExchangeError("x", unknown_execution=True))
    close = AsyncMock(return_value={"orderId": 9})
    with patch.object(v, "live_symbol_rules", new=AsyncMock(return_value=rules)), \
            patch.object(v, "install_verified_stop", new=stop), \
            patch.object(v, "close_tracked_symbol", new=close), \
            patch.object(v, "persist_state"):
        result = asyncio.run(v.manual_breakeven_after_tp1(client, state, p, position, open_algos or []))
    return result, stop, close


def test_moves_stop_to_entry_after_tp1_fill():
    p, c = plan(), FakeClient()
    result, stop, close = run(p, c)
    assert result and p["breakeven_done"] and p["stop_loss"] in {"100", "100.0"}
    assert p["stop_algo_id"] == 33 and stop.await_count == 1
    assert ("DELETE", "/fapi/v1/algoOrder", {"symbol": "BTCUSDT", "algoId": 11}) in c.calls
    assert close.await_count == 0


def test_never_touches_non_manual_plans():
    p, c = plan(source="ORIGINAL_V2"), FakeClient()
    result, stop, _ = run(p, c)
    assert not result and c.calls == [] and p["stop_loss"] == "90"


def test_waits_when_tp1_still_open_or_not_filled():
    p, c = plan(), FakeClient()
    assert not run(p, c, open_algos=[{"algo_id": 22}])[0] and c.calls == []
    p, c = plan(), FakeClient(filled=False)
    assert not run(p, c)[0] and not any(m == "DELETE" for m, *_ in c.calls)


def test_does_not_move_stop_when_price_already_below_entry():
    p, c = plan(), FakeClient()
    result, *_ = run(p, c, position={**POSITION, "mark_price": "99"})
    assert not result and not any(m == "DELETE" for m, *_ in c.calls) and p["stop_loss"] == "90"


def test_closes_remainder_when_new_stop_cannot_be_verified():
    p, c = plan(), FakeClient()
    result, _, close = run(p, c, stop_ok=False)
    assert result and close.await_count == 1 and not p.get("breakeven_done")
