"""Tekli pozisyon kapatma, uzlaştırma/koruma döngüsüyle AYNI kilit altında çalışır (çift emir / iç içe geçme yok)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution  # noqa: E402


def test_manual_close_waits_for_the_shared_state_lock():
    plan = {"id": "plan-123456", "provenance_state": "CONFIRMED", "symbol": "BTCUSDT", "status": "KORUMA AKTİF",
            "close_client_order_ids": []}
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    state["plans"] = {plan["id"]: plan}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = v25_execution.CloseRequest(plan_id=plan["id"], confirmation="CANLI POZİSYONU KAPAT")
    close = AsyncMock(return_value={"orderId": 3})

    async def scenario():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}), \
                patch.object(v25_execution, "client_for", return_value=object()), \
                patch.object(v25_execution, "close_tracked_symbol", new=close), \
                patch.object(v25_execution, "persist_state"):
            await state["lock"].acquire()          # uzlaştırma (reconcile) çalışıyormuş gibi
            task = asyncio.create_task(v25_execution.v25_close(request, body))
            await asyncio.sleep(0.05)
            waited = close.await_count == 0 and not task.done()
            state["lock"].release()
            result = await task
            return waited, result

    waited, result = asyncio.run(scenario())
    assert waited, "kapatma, kilit doluyken emir göndermemeli"
    assert close.await_count == 1
    assert result["ok"] is True and plan["status"] == "KAPATMA EMRİ GÖNDERİLDİ"


def test_two_simultaneous_closes_never_overlap():
    plan = {"id": "plan-123456", "provenance_state": "CONFIRMED", "symbol": "BTCUSDT", "status": "KORUMA AKTİF",
            "close_client_order_ids": []}
    state = v25_execution.initial_state()
    state["lock"] = asyncio.Lock()
    state["plans"] = {plan["id"]: plan}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    body = v25_execution.CloseRequest(plan_id=plan["id"], confirmation="CANLI POZİSYONU KAPAT")
    running = 0
    peak = 0

    async def slow_close(client, symbol, intent):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return {"orderId": 3}

    async def scenario():
        with patch.object(v25_execution, "execution_owner", return_value={"id": "owner"}), \
                patch.object(v25_execution, "client_for", return_value=object()), \
                patch.object(v25_execution, "close_tracked_symbol", new=slow_close), \
                patch.object(v25_execution, "persist_state"):
            await asyncio.gather(v25_execution.v25_close(request, body), v25_execution.v25_close(request, body))

    asyncio.run(scenario())
    assert peak == 1
