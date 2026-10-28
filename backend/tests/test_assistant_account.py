"""Kais'in üyenin KENDİ hesabını okuması: profil izni, yalnız canlı oturum sahibi, salt okunur, anahtar yok."""
import asyncio
import copy
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import original_v2_live
from app.assistant_response import own_levels, safe_response
from app.assistant_storage import AssistantStore
from app.assistant_tools import ACCOUNT_ACCESS_OFF, ACCOUNT_TOOLS, AssistantTools

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def iso(delta_hours: float = 0) -> str:
    return (NOW + timedelta(hours=delta_hours)).isoformat()


ACCOUNT = {
    "snapshot": {
        "wallet_balance": "136.15", "available_balance": "120.5", "unrealized_pnl": "-1.25",
        "positions": [
            {"symbol": "DOTUSDT", "direction": "LONG", "quantity": "12", "entry_price": "4.512", "mark_price": "4.48", "unrealized_pnl": "-0.38"},
            {"symbol": "XRPUSDT", "direction": "SHORT", "quantity": "0", "entry_price": "0", "mark_price": "0.5", "unrealized_pnl": "0"},
        ],
        "api_key": "SHOULD-NEVER-LEAK",
    },
    "connection": {"last_checked": iso(), "last_error": None},
    "connected": True, "recovery_ready": True, "recovery_error": None, "reconciliation_required": False,
    "plans": {
        "p-open": {"id": "p-open", "symbol": "DOTUSDT", "direction": "LONG", "status": "AÇIK", "stop_loss": "4.41",
                   "targets": [{"price": "4.6"}, "4.7", 4.8], "opened_at": iso(-2), "breakeven_at": None},
        "p-win": {"id": "p-win", "symbol": "SOLUSDT", "direction": "LONG", "status": "KAPANDI", "pnl_verified": True,
                  "realized_pnl": 2.5, "closed_at": iso(-24), "opened_at": iso(-26), "close_reason": "TP1",
                  "exit_price": 151.2, "entry_price": 149.0, "initial_risk_usdt": 1.0},
        "p-loss": {"id": "p-loss", "symbol": "DOTUSDT", "direction": "SHORT", "status": "KAPANDI", "pnl_verified": True,
                   "realized_pnl": -2.63, "closed_at": iso(-3), "opened_at": iso(-5), "close_reason": "STOP",
                   "exit_price": 4.6, "entry_price": 4.5, "initial_risk_usdt": 2.63},
        "p-old": {"id": "p-old", "symbol": "BTCUSDT", "direction": "LONG", "status": "KAPANDI", "pnl_verified": True,
                  "realized_pnl": 9.0, "closed_at": iso(-24 * 20), "opened_at": iso(-24 * 20 - 1), "close_reason": "TP3"},
        "p-unverified": {"id": "p-unverified", "symbol": "ETHUSDT", "direction": "LONG", "status": "KAPANDI", "pnl_verified": False,
                         "realized_pnl": 1.0, "closed_at": iso(-1)},
    },
}
CONFIG = {
    "enabled": True, "paused": False, "risk_percent": 2, "daily_loss_percent": 20,
    "margin_mode": "agresif",
    "margin_modes": [{"mode": "guvenli", "label": "Güvenli", "per_trade_percent": 30}, {"mode": "agresif", "label": "Agresif", "per_trade_percent": 65}],
    "smart_profit": {"settings": {"enabled": True, "emergency_off": False, "threshold": 70, "wait_minutes": 10, "protect_stop_enabled": True,
                                  "smart_exit_enabled": True, "direction_mismatch_action": "record_only", "secret": "x"}},
    "session": {"hours": 24, "remaining_seconds": 3600},
    "daily": {"loss": 2.63, "limit": 27.23, "locked": False, "opening_wallet": 138.0},
    "symbols": ["A", "B", "C"],
}
SMART = {"positions": [{"plan_id": "p-open", "mode": "KAR_KORUMA", "last_score": 64, "timer_end": 1_800_000_000, "temp_stop_price": "4.5"}]}
EVENTS = [
    {"created_at": iso(-0.1), "kind": "LIVE_POSITION_CLOSED", "symbol": "DOTUSDT", "message": "DOTUSDT kapandı", "recipient_user_id": "owner", "client_id": "c1"},
    {"created_at": iso(-0.2), "kind": "OTHER", "message": "başka üyenin olayı", "recipient_user_id": "someone-else"},
    {"created_at": iso(-0.3), "kind": "SCAN_FAILED", "message": "x" * 500},
]


def make_tools(member="owner", owner="owner", *, premium=True, access=True):
    state = {"lock": asyncio.Lock(), "live_session_authorization": {"user_id": owner}, "events": copy.deepcopy(EVENTS)}
    tools = AssistantTools.__new__(AssistantTools)
    tools.request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    tools.config = SimpleNamespace(request_timeout_seconds=1, protection_stale_seconds=600)
    tools.clock = lambda: NOW.timestamp()
    tools.user = lambda user_id: {"id": member}
    tools.premium = lambda user_id: premium
    tools.reads = 0

    async def account_access(user_id):
        return access

    async def account_state(user_id):
        tools.reads += 1
        return copy.deepcopy(ACCOUNT) if member == owner else None

    tools.account_access = account_access
    tools.account_state = account_state
    return tools


def patched():
    return (patch.object(original_v2_live, "public_config", return_value=copy.deepcopy(CONFIG)),
            patch.object(original_v2_live, "smart_profit_public", return_value=copy.deepcopy(SMART)))


class AccountToolTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, tools, name, **arguments):
        first, second = patched()
        with first, second:
            return await getattr(AssistantTools, name)(tools, "owner", **arguments)

    async def test_access_off_blocks_every_account_tool_before_reading(self):
        tools = make_tools(access=False)
        for name in sorted(ACCOUNT_TOOLS):
            with self.subTest(name=name):
                result = await self.call(tools, name)
                self.assertEqual(result["data"].get("reason"), ACCOUNT_ACCESS_OFF)
                self.assertTrue(result["stale"])
        self.assertEqual(tools.reads, 0)

    async def test_other_member_gets_no_account_data(self):
        tools = make_tools(member="someone", owner="owner")
        for name in sorted(ACCOUNT_TOOLS):
            with self.subTest(name=name):
                result = await self.call(tools, name)
                self.assertIn(result["data"].get("reason"), {"ACCOUNT_NOT_VERIFIED", "LIVE_NOT_OWNER"})
                self.assertNotIn("136.15", str(result))
                self.assertNotIn("DOTUSDT kapandı", str(result))

    async def test_account_summary(self):
        data = (await self.call(make_tools(), "get_my_account_summary"))["data"]
        self.assertEqual(data["wallet_balance"], 136.15)
        self.assertEqual(data["available_balance"], 120.5)
        self.assertEqual(data["open_positions"], 1)
        self.assertEqual(data["daily_loss"], {"loss": 2.63, "limit": 27.23, "locked": False, "remaining": 24.6})
        self.assertNotIn("SHOULD-NEVER-LEAK", str(data))
        self.assertNotIn("opening_wallet", str(data))

    async def test_settings(self):
        data = (await self.call(make_tools(), "get_my_settings"))["data"]
        self.assertEqual(data["risk_percent"], 2)
        self.assertEqual(data["margin_mode"], {"mode": "agresif", "label": "Agresif", "per_trade_percent": 65})
        self.assertEqual(data["smart_protection"]["threshold"], 70)
        self.assertNotIn("secret", data["smart_protection"])
        self.assertEqual(data["coins"], 3)
        self.assertEqual(data["session"], {"hours": 24, "remaining_seconds": 3600})

    async def test_trades_only_verified_and_within_days(self):
        data = (await self.call(make_tools(), "get_my_trades", days=7))["data"]
        self.assertEqual([row["symbol"] for row in data["trades"]], ["DOTUSDT", "SOLUSDT"])
        self.assertEqual((data["count"], data["wins"], data["losses"]), (2, 1, 1))
        self.assertEqual(data["win_rate_percent"], 50.0)
        self.assertEqual(data["total_realized_pnl"], round(2.5 - 2.63, 4))
        self.assertEqual(data["trades"][0]["close_reason"], "STOP")
        wide = (await self.call(make_tools(), "get_my_trades", days=30))["data"]
        self.assertEqual(wide["count"], 3)

    async def test_events_filter_other_members_and_trim(self):
        data = (await self.call(make_tools(), "get_my_events", limit=5))["data"]
        messages = [row["message"] for row in data["events"]]
        self.assertEqual(len(messages), 2)
        self.assertNotIn("başka üyenin olayı", messages)
        self.assertEqual(len(messages[1]), 200)
        self.assertNotIn("client_id", str(data))
        self.assertNotIn("recipient_user_id", str(data))

    async def test_position_details_include_own_levels_and_guard(self):
        data = (await self.call(make_tools(), "get_my_position_details"))["data"]
        self.assertEqual(len(data["positions"]), 1)
        row = data["positions"][0]
        self.assertEqual((row["entry_price"], row["mark_price"], row["stop_loss"]), (4.512, 4.48, 4.41))
        self.assertEqual(row["targets"], [4.6, 4.7, 4.8])
        self.assertTrue(row["managed_by_bot"])
        self.assertEqual(row["smart_protection"]["mode"], "KAR_KORUMA")

    async def test_free_member_gets_no_levels(self):
        data = (await self.call(make_tools(premium=False), "get_my_position_details"))["data"]
        row = data["positions"][0]
        for key in ("entry_price", "stop_loss", "targets"):
            self.assertNotIn(key, row)


class OwnLevelReplyTests(unittest.TestCase):
    RESULTS = {"get_my_position_details": {"stale": False, "data": {"positions": [{"entry_price": 4.512, "stop_loss": 4.41, "targets": [4.6, 4.7]}]}}}

    def test_own_levels_pass_for_premium(self):
        reply = "DOT pozisyonunda giriş 4.512, stop 4,41 ve TP1 4.6."
        self.assertEqual(safe_response(reply, "tr", free=False, allowed_levels=own_levels(self.RESULTS)), reply)

    def test_foreign_level_is_still_blocked(self):
        reply = "Giriş 4.512, stop 3.90."
        self.assertNotEqual(safe_response(reply, "tr", free=False, allowed_levels=own_levels(self.RESULTS)), reply)

    def test_levels_blocked_without_own_data_or_for_free_members(self):
        reply = "Stop 4.41."
        self.assertNotEqual(safe_response(reply, "tr", free=False), reply)
        self.assertNotEqual(safe_response(reply, "tr", free=True, allowed_levels=own_levels(self.RESULTS)), reply)

    def test_stale_results_do_not_unlock_levels(self):
        stale = {"get_my_position_details": {**self.RESULTS["get_my_position_details"], "stale": True}}
        self.assertEqual(own_levels(stale), frozenset())


class AccountAccessStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "assistant.sqlite3"
        self.env = patch.dict(os.environ, {"PROTREBOT_DURABLE_AUTH_REQUIRED": ""})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        self.folder.cleanup()

    def tools(self, store):
        tools = make_tools()
        tools.request.app.state.assistant_service = SimpleNamespace(store=store)
        return tools

    async def test_default_on_and_can_be_turned_off(self):
        store = AssistantStore(SimpleNamespace(state=SimpleNamespace()), path=self.path)
        tools = self.tools(store)
        self.assertTrue(await AssistantTools.account_access(tools, "owner"))
        async with store.proactive_transaction("owner", 1) as transaction:
            await transaction.execute("UPDATE assistant_proactive_state SET account_access=$1 WHERE user_id=$2", False, "owner")
        self.assertFalse(await AssistantTools.account_access(tools, "owner"))

    async def test_existing_table_is_migrated(self):
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE assistant_proactive_state (user_id TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE, "
                           "last_seen_at DOUBLE PRECISION, last_checkin_at DOUBLE PRECISION)")
        connection.execute("INSERT INTO assistant_proactive_state (user_id) VALUES ('owner')")
        connection.commit()
        connection.close()
        store = AssistantStore(SimpleNamespace(state=SimpleNamespace()), path=self.path)
        self.assertTrue(await AssistantTools.account_access(self.tools(store), "owner"))

    async def test_unreadable_preference_fails_closed(self):
        class Broken:
            def connection(self, timeout):
                raise OSError("down")
        self.assertFalse(await AssistantTools.account_access(self.tools(Broken()), "owner"))


if __name__ == "__main__":
    unittest.main()
