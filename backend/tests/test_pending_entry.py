import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import v25_execution as v
from app.v25_execution import initial_state

D = v.Decimal


def pending(**extra):
    base = {"id": "p1", "intent_id": "i1", "symbol": "BTCUSDT", "direction": "LONG", "source": "MANUAL",
            "order_type": "LIMIT", "entry_price": "100", "quantity": "1", "stop_loss": "95",
            "targets": ["105", "110", "120"], "status": "DOLUM BEKLİYOR", "provenance_state": "PROVISIONAL",
            "entry_client_order_id": f"{v.LIVE_CLIENT_PREFIX}abc12345"}
    base.update(extra)
    return base


RULES = {"tick": D("0.1"), "step": D("0.001")}


def update(plan, *, stop=None, targets=None, profit=None, position=None, state=None):
    if state is None:
        state = initial_state()
        state["policy"] = {"max_loss_per_trade": 10}
    with patch.object(v, "live_symbol_rules", new=AsyncMock(return_value=RULES)), \
            patch.object(v, "live_plan_position", new=AsyncMock(return_value=position)):
        return asyncio.run(v.update_pending_entry(object(), state, plan, stop_loss=stop, targets=targets or {}, profit_usdt=profit))


def test_updates_stop_and_target_while_waiting():
    p = pending()
    result = update(p, stop=96.5, targets={1: 104})
    assert p["stop_loss"] == "96.5" and p["targets"][0] == "104" and result["stop_loss"] == "96.5"
    assert p["targets"][1:] == ["110", "120"] and p.get("pending_edited_at")


def test_profit_usdt_sets_all_targets_to_one_price_for_long_and_short():
    p = pending(quantity="2")
    update(p, profit=10)
    assert p["targets"] == ["105", "105", "105"] and p["profit_target_usdt"] == "10"
    s = pending(direction="SHORT", stop_loss="105", targets=["95", "90", "80"], quantity="2")
    update(s, profit=10)
    assert s["targets"] == ["95", "95", "95"]


def test_profit_and_explicit_target_cannot_be_combined():
    p = pending()
    with pytest.raises(v.ManualPlanActionError):
        update(p, profit=5, targets={1: 104})
    assert p["targets"] == ["105", "110", "120"]


@pytest.mark.parametrize("kwargs", [
    {"stop": 101},                 # stop kâr tarafında
    {"targets": {1: 99}},          # hedef zarar tarafında
    {"targets": {1: 111}},         # sıra bozuk (TP1 > TP2)
    {"stop": 50},                  # risk sınırını aşar
])
def test_rejects_bad_levels_without_mutating(kwargs):
    p = pending()
    state = initial_state()
    state["policy"] = {"max_loss_per_trade": 10}
    with pytest.raises(v.ManualPlanActionError):
        update(p, state=state, **kwargs)
    assert p["stop_loss"] == "95" and p["targets"] == ["105", "110", "120"]


def test_short_side_validation():
    p = pending(direction="SHORT", stop_loss="105", targets=["95", "90", "80"])
    update(p, stop=104, targets={1: 96})
    assert p["stop_loss"] == "104" and p["targets"][0] == "96"
    with pytest.raises(v.ManualPlanActionError):
        update(p, stop=99)


def test_refuses_when_filled_or_not_pending_or_not_manual():
    with pytest.raises(v.ManualPlanActionError) as exc:
        update(pending(), stop=96, position={"direction": "LONG"})
    assert exc.value.http_status == 409
    for extra in ({"status": "KORUMA AKTİF"}, {"source": "ORIGINAL_V2"}, {"order_type": "MARKET"}, {"provenance_state": "BROKEN"}):
        with pytest.raises(v.ManualPlanActionError):
            update(pending(**extra), stop=96)


class CancelClient:
    def __init__(self, status="NEW", after="CANCELED"):
        self.status, self.after, self.deleted = status, after, False

    async def signed(self, method, path, params=None):
        if method == "DELETE":
            self.deleted = True
        return {}


def cancel(plan, client, orders):
    queue = list(orders)
    with patch.object(v, "find_order", new=AsyncMock(side_effect=lambda *a, **k: queue.pop(0))), \
            patch.object(v, "order_matches_spec", return_value=True):
        return asyncio.run(v.cancel_pending_entry(client, initial_state(), plan))


def test_cancel_marks_plan_only_after_verified():
    p, c = pending(), CancelClient()
    result = cancel(p, c, [{"status": "NEW", "orderId": 7}, {"status": "CANCELED", "orderId": 7}])
    assert c.deleted and result["cancelled"] and p["status"] == "İPTAL"


def test_cancel_unverified_keeps_plan_waiting():
    p, c = pending(), CancelClient()
    with pytest.raises(v.ManualPlanActionError):
        cancel(p, c, [{"status": "NEW", "orderId": 7}, {"status": "NEW", "orderId": 7}])
    assert p["status"] == "DOLUM BEKLİYOR"


def test_cancel_refuses_partial_fill_and_handles_missing_order():
    p, c = pending(), CancelClient()
    with pytest.raises(v.ManualPlanActionError):
        cancel(p, c, [{"status": "PARTIALLY_FILLED", "orderId": 7}])
    assert not c.deleted and p["status"] == "DOLUM BEKLİYOR"
    gone = pending()
    assert cancel(gone, CancelClient(), [None])["state"] == "NOT_FOUND" and gone["status"] == "İPTAL"
