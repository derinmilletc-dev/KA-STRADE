"""Kais Koç (asistan) — KAIS FARMER özeti: yalnız üyenin kendi Farmer'ı, salt okunur, fiyat seviyesi yok, çalışma zamanı kurmaz."""
import asyncio
import unittest
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

from app import farmer_view
from app.assistant_tools import AssistantTools

NOW = 1_800_000_000.0
STATUS = {
    "state": "RUNNING", "available": True, "configured": True, "blockers": [], "effective_capacity": 2,
    "config": {"target_usdt": 3.0, "max_loss_usdt": 5.0, "total_margin_cap": 200.0, "leverage": 10, "margin_usdt": 50.0,
               "max_positions": 5, "min_score": 92, "dead_score": 75},
    "positions": [{"symbol": "SOLUSDT", "side": "LONG", "age_s": 120.0, "target_usdt": 3.0, "max_loss_usdt": 5.0, "unrealized_net_usdt": 0.8,
                   "entry_price": 150.1, "tp_price": 151.2, "stop_price": 148.9, "grid_id": "farmer-x"}],
    "why": {"code": "NO_CANDIDATES", "details": []}, "today": {"verified_net_pnl": 2.4, "verified_count": 1},
    "scanner": [{"symbol": "BTCUSDT", "direction": "LONG", "score": 90, "status": "SCORE_TOO_LOW", "reason": "SCORE_TOO_LOW"},
                {"symbol": "ETHUSDT", "direction": "SHORT", "score": 95, "status": "QUALIFIED", "reason": None}],
}


def tools(member_id, *, single=None, registry=None, armed_until=NOW + 600):
    state = SimpleNamespace(v25_execution={"armed_until": armed_until})
    if single is not None:
        state.live_farmer_runner = single
    if registry is not None:
        state.live_farmer_registry = registry
    fake = SimpleNamespace(request=SimpleNamespace(app=SimpleNamespace(state=state)), user=lambda user_id: {"id": member_id}, clock=lambda: NOW)
    fake.result = partial(AssistantTools.result, fake)
    return fake


class Registry:
    def __init__(self, runtimes):
        self.runtimes, self.created = runtimes, []

    def get(self, uid):
        return self.runtimes.get(uid)

    def for_member(self, uid):                       # asistan bunu ÇAĞIRMAMALI (kurulum yapmaz)
        self.created.append(uid)
        raise AssertionError("assistant must not create runtimes")


class FarmerToolTests(unittest.TestCase):
    def call(self, fake, uid):
        with patch.object(farmer_view, "build_status", return_value=STATUS):
            return asyncio.run(AssistantTools.get_farmer_status(fake, uid))

    def test_member_reads_own_farmer_without_price_levels(self):
        runtime = SimpleNamespace(owner_id="u1")
        data = self.call(tools("u1", registry=Registry({"u1": runtime})), "u1")["data"]
        self.assertEqual(data["state"], "RUNNING")
        self.assertEqual(data["settings"]["leverage"], 10)
        self.assertEqual(data["positions"], [{"symbol": "SOLUSDT", "side": "LONG", "age_s": 120.0, "target_usdt": 3.0, "max_loss_usdt": 5.0,
                                              "unrealized_net_usdt": 0.8}])
        self.assertEqual(data["scanner"]["qualified"], 1)
        self.assertTrue(data["session_active"])
        for secret in ("entry_price", "tp_price", "stop_price", "grid_id", "150.1"):
            self.assertNotIn(secret, str(data))

    def test_other_member_and_missing_farmer_get_no_data(self):
        reg = Registry({"u1": SimpleNamespace(owner_id="u1")})
        self.assertEqual(self.call(tools("u2", registry=reg), "u2")["data"], {"reason": "FARMER_NOT_SET_UP"})
        self.assertEqual(reg.created, [])
        single = SimpleNamespace(owner_id="owner")
        result = self.call(tools("someone", single=single), "someone")
        self.assertEqual(result["data"], {"reason": "FARMER_NOT_OWNER"})
        self.assertTrue(result["stale"])
        self.assertEqual(self.call(tools("u1"), "u1")["data"], {"reason": "FARMER_NOT_SET_UP"})


if __name__ == "__main__":
    unittest.main()
