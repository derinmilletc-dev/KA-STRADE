"""Asistanın canlı strateji özeti: yalnız canlı oturum sahibine, salt okunur ve seviye/anahtar içermeden."""
import asyncio
import unittest
from datetime import datetime, timezone
from functools import partial
from types import SimpleNamespace
from unittest.mock import patch

from app import original_v2_live
from app.assistant_tools import AssistantTools

NOW = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
CONFIG = {
    "enabled": True, "paused": False, "pause_reason": None, "risk_percent": 1, "daily_loss_percent": 3,
    "session": {"remaining_seconds": 5400}, "daily": {"loss": 1.2, "limit": 4.2, "locked": False, "opening_wallet": 141.0},
    "fill_lock": None,
    "scanner": {"stage": "SCAN_COMPLETE", "last_scan_at": "2026-10-22T09:59:00Z", "scanned_symbol_count": 40, "candidate_count": 0,
                "last_24h": {"scans": 90, "candidates": 3, "trades": 1},
                "rejection_counts": {"signal_conditions": 30, "confidence_low": 6, "market_volume": 4, "spread_wide": 0},
                "near_misses": [{"symbol": "SOLUSDT", "reason_code": "confidence_low", "confidence": 81, "confidence_threshold": 85,
                                 "entry": 1, "stop_percent": 2.1}]},
}


def tools_for(member_id, owner_id):
    state = {"lock": asyncio.Lock(), "live_session_authorization": {"user_id": owner_id}}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    fake = SimpleNamespace(request=request, user=lambda user_id: {"id": member_id}, clock=lambda: NOW.timestamp(),
                           config=SimpleNamespace(request_timeout_seconds=1))
    fake.result = partial(AssistantTools.result, fake)
    return fake


class LiveStrategyToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_gets_scan_reasons_without_levels(self):
        with patch.object(original_v2_live, "public_config", return_value=CONFIG):
            result = await AssistantTools.get_live_strategy(tools_for("owner", "owner"), "owner")
        data = result["data"]
        self.assertTrue(data["enabled"])
        self.assertEqual(data["scanner"]["top_rejections"][0], {"reason_code": "signal_conditions", "count": 30})
        self.assertEqual(len(data["scanner"]["top_rejections"]), 3)
        self.assertEqual(data["scanner"]["near_misses"][0], {"symbol": "SOLUSDT", "reason_code": "confidence_low", "confidence": 81, "confidence_threshold": 85})
        self.assertEqual(data["daily"], {"loss": 1.2, "limit": 4.2, "locked": False})
        self.assertNotIn("entry", str(data))
        self.assertNotIn("opening_wallet", str(data))

    async def test_other_members_and_missing_state_get_no_data(self):
        with patch.object(original_v2_live, "public_config", return_value=CONFIG) as read:
            other = await AssistantTools.get_live_strategy(tools_for("someone", "owner"), "someone")
        self.assertEqual(other["data"], {"reason": "LIVE_NOT_OWNER"})
        self.assertTrue(other["stale"])
        read.assert_not_called()
        empty = tools_for("owner", "owner")
        empty.request.app.state.v25_execution = None
        self.assertEqual((await AssistantTools.get_live_strategy(empty, "owner"))["data"], {"reason": "LIVE_NOT_VERIFIED"})
