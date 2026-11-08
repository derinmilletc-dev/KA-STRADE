"""KAIS FARMER C10 — HTTP katmanı (işleyiciler doğrudan çağrılır). fastapi yoksa ATLANIR (CI'da gerçek bağımlılıkla çalışır)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

try:
    import fastapi as _fa
    HAS_FASTAPI = isinstance(getattr(_fa, "APIRouter", None), type)      # gerçek (ya da dekoratörleri geçiren) APIRouter; MagicMock taklidi değil
except ImportError:
    HAS_FASTAPI = False

if HAS_FASTAPI:
    from fastapi import HTTPException  # noqa: E402
    from app import farmer_api as api  # noqa: E402
    from app.farmer_engine import orchestrator as o  # noqa: E402
    from app.farmer_engine.store import FarmerStore  # noqa: E402
    from tests.test_farmer_orchestrator import Rig  # noqa: E402

ONE = {"BBBUSDT": {"confidence": 50}, "CCCUSDT": {"confidence": 50}}


def run(coro):
    return asyncio.run(coro)


def req(runtime=None, armed_until=0.0):
    state = SimpleNamespace(v25_execution={"armed_until": armed_until})
    if runtime is not None:
        state.live_farmer_runner = runtime
    return SimpleNamespace(app=SimpleNamespace(state=state))


@unittest.skipUnless(HAS_FASTAPI, "fastapi yok (REAL ortamda atlanır)")
class TestApi(unittest.TestCase):
    def setUp(self):
        self._owner = api._owner
        api._owner = lambda request: {"id": "u1", "role": "OWNER"}

    def tearDown(self):
        api._owner = self._owner

    def runtime(self):
        r = Rig(table=dict(ONE), store=FarmerStore(':memory:', check_same_thread=False))
        r.orch.recover()
        applied = []
        rt = SimpleNamespace(orchestrator=r.orch, apply_settings=lambda s: applied.append(s))
        return r, rt, applied

    def test_status_unavailable_when_no_runtime(self):
        s = run(api.farmer_status(req()))
        self.assertEqual((s["available"], s["state"]), (False, "OFF"))

    def test_commands_refused_without_runtime(self):
        for fn in (api.farmer_start, api.farmer_stop_bot, api.farmer_emergency_close, api.farmer_acknowledge):
            with self.assertRaises(HTTPException) as cm:
                run(fn(req()))
            self.assertEqual(cm.exception.status_code, 409)

    def test_start_refused_while_gates_closed(self):
        r, rt, _ = self.runtime()
        with self.assertRaises(HTTPException) as cm:
            run(api.farmer_start(req(rt)))
        self.assertEqual(cm.exception.status_code, 423)
        self.assertEqual(r.orch.state, o.OFF)

    def test_start_allowed_when_gates_open(self):
        r, rt, _ = self.runtime()
        original = api.live_entry_blockers
        api.live_entry_blockers = lambda: []
        try:
            self.assertEqual(run(api.farmer_start(req(rt)))["state"], o.RUNNING)
        finally:
            api.live_entry_blockers = original

    def test_start_cannot_leave_intervention(self):
        r, rt, _ = self.runtime()
        r.orch._intervene("x")
        original = api.live_entry_blockers
        api.live_entry_blockers = lambda: []
        try:
            self.assertEqual(run(api.farmer_start(req(rt)))["state"], o.INTERVENTION)
        finally:
            api.live_entry_blockers = original

    def test_stop_bot_keeps_position(self):
        r = Rig(table=dict(ONE), store=FarmerStore(':memory:', check_same_thread=False))
        r.run()
        out = run(api.farmer_stop_bot(req(SimpleNamespace(orchestrator=r.orch))))
        self.assertEqual(out["state"], o.STOPPING)
        self.assertEqual(r.orch.guard.slots().open, 1)

    def test_emergency_close_reports_complete(self):
        r = Rig(table=dict(ONE), store=FarmerStore(':memory:', check_same_thread=False))
        r.run()
        out = run(api.farmer_emergency_close(req(SimpleNamespace(orchestrator=r.orch))))
        self.assertEqual((out["closed"], out["failed"], out["complete"]), (1, 0, True))

    def test_emergency_close_failure_reported_not_complete(self):
        r = Rig(table=dict(ONE), store=FarmerStore(':memory:', check_same_thread=False))
        r.run()
        r.ex.fail_reads = True
        out = run(api.farmer_emergency_close(req(SimpleNamespace(orchestrator=r.orch))))
        self.assertFalse(out["complete"])
        self.assertEqual(r.orch.state, o.INTERVENTION)

    def test_preview_validates(self):
        with self.assertRaises(HTTPException) as cm:
            run(api.farmer_preview(req(), {"margin_usdt": 61}))
        self.assertEqual((cm.exception.status_code, cm.exception.detail["field"]), (422, "margin_usdt"))
        self.assertEqual(run(api.farmer_preview(req(), {}))["preview"]["effective_capacity"], 2)

    def test_settings_locked_while_active(self):
        r = Rig(table=dict(ONE), store=FarmerStore(':memory:', check_same_thread=False))
        r.run()
        with self.assertRaises(HTTPException) as cm:
            run(api.farmer_settings(req(SimpleNamespace(orchestrator=r.orch, apply_settings=lambda s: None)), {"target_usdt": 2}))
        self.assertEqual(cm.exception.status_code, 409)

    def test_settings_applied_when_off(self):
        r, rt, applied = self.runtime()
        out = run(api.farmer_settings(req(rt), {"target_usdt": 2.0}))
        self.assertTrue(out["ok"])
        self.assertEqual(applied[0].confirm.target_usdt, 2.0)

    def test_arm_info_from_existing_state(self):
        s = run(api.farmer_status(req(armed_until=4102444800.0)))
        self.assertTrue(s["arm"]["active"])
        s = run(api.farmer_status(req(armed_until=0.0)))
        self.assertFalse(s["arm"]["active"])


if __name__ == "__main__":
    unittest.main()
