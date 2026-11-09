import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from app import assistant_tools
from app import readonly_demo_service as service
from app.assistant_tools import (
    ARGUMENT_MODELS,
    TOOL_DEFINITIONS,
    AssistantTools,
    PlanScanArguments,
)


def row(symbol, direction, confidence, entry=100.0, stop=98.0, tp=(103.0, 105.0, 108.0), opportunity=80, **extra):
    return {"symbol": symbol, "direction": direction, "confidence_value": confidence, "opportunity_score": opportunity, "analysis_score": 80,
            "entry": entry, "stop_loss": stop, "tp1": tp[0], "tp2": tp[1], "tp3": tp[2], **extra}


def request(rows, scanned_at):
    member = {"id": "u1"}
    return SimpleNamespace(state=SimpleNamespace(member=member)), {"scanner": {"all_candidates": rows, "last_scan_at": scanned_at, "last_scan_timeframe": "15m", "coins_scanned": 50}, "settings": {}}


class ReadPlanCandidatesTests(unittest.TestCase):
    def run_read(self, rows, minimum=95, count=3):
        req, state = request(rows, "2026-11-09T07:00:00+00:00")
        with patch.object(service.v21_demo, "state_for", return_value=state), patch.object(service.v21_demo, "candidate_is_tradeable", return_value=True):
            return service.read_plan_candidates(req, minimum, count)

    def test_filters_by_confidence_sorts_and_limits(self):
        result = self.run_read([row("AAAUSDT", "LONG", 96), row("BBBUSDT", "SHORT", 99, entry=100, stop=102, tp=(97, 95, 92)), row("CCCUSDT", "LONG", 90), row("DDDUSDT", "LONG", 95)], 95, 2)
        self.assertEqual([item["symbol"] for item in result["picks"]], ["BBBUSDT", "AAAUSDT"])
        self.assertEqual(result["matching_count"], 3)
        self.assertEqual(set(result["picks"][0]), {"symbol", "direction", "confidence_value", "opportunity_score", "analysis_score", "entry", "stop_loss", "tp1", "tp2", "tp3"})

    def test_inconsistent_levels_are_skipped_never_repaired(self):
        rows = [row("BADLONG", "LONG", 99, stop=101), row("BADSHORT", "SHORT", 99, entry=100, stop=98, tp=(97, 95, 92)),
                row("ZERO", "LONG", 99, entry=0), row("NAN", "LONG", 99, stop=float("nan")), row("NEUTRAL", "NEUTRAL", 99), row("OKUSDT", "LONG", 99)]
        self.assertEqual([item["symbol"] for item in self.run_read(rows)["picks"]], ["OKUSDT"])

    def test_requires_member_session(self):
        with self.assertRaises(PermissionError):
            service.read_plan_candidates(SimpleNamespace(state=SimpleNamespace(member={})), 95, 3)

    def test_ineligible_rows_are_excluded(self):
        req, state = request([row("AAAUSDT", "LONG", 99)], "2026-11-09T07:00:00+00:00")
        with patch.object(service.v21_demo, "state_for", return_value=state), patch.object(service.v21_demo, "candidate_is_tradeable", return_value=False):
            self.assertEqual(service.read_plan_candidates(req, 95, 3)["picks"], [])


class PlanScanGateTests(unittest.TestCase):
    NOW = datetime(2026, 11, 9, 7, 5, tzinfo=timezone.utc).timestamp()

    def tools(self, access, scanned_at="2026-11-09T07:04:00+00:00", rows=None):
        req, state = request(rows if rows is not None else [row("AAAUSDT", "LONG", 99)], scanned_at)
        tools = AssistantTools.__new__(AssistantTools)
        tools.request, tools.config, tools.clock = req, SimpleNamespace(), lambda: self.NOW
        patches = [patch.object(AssistantTools, "user", return_value={"id": "u1"}),
                   patch.object(assistant_tools, "access_for_request", return_value=access),
                   patch.object(service.v21_demo, "state_for", return_value=state),
                   patch.object(service.v21_demo, "candidate_is_tradeable", return_value=True)]
        return tools, patches

    def call(self, access, **kwargs):
        tools, patches = self.tools(access, **kwargs)
        for item in patches:
            item.start()
        try:
            return asyncio.run(tools.plan_scan("u1", PlanScanArguments(min_confidence=95, count=3)))
        finally:
            for item in patches:
                item.stop()

    def test_premium_gets_levels(self):
        result = self.call({"isPremium": True})
        self.assertEqual(result["data"]["status"], "fresh")
        self.assertEqual(result["data"]["picks"][0]["stop_loss"], 98.0)

    def test_non_premium_and_free_trial_get_no_levels(self):
        for access in ({"isPremium": False}, {"isPremium": True, "freeTrial": True}):
            result = self.call(access)
            self.assertEqual(result["data"], {"reason": "PREMIUM_REQUIRED"})
            self.assertTrue(result["stale"])

    def test_stale_or_missing_scan_invents_nothing(self):
        self.assertEqual(self.call({"isPremium": True}, scanned_at="2026-11-09T04:00:00+00:00")["data"]["picks"], [])
        self.assertEqual(self.call({"isPremium": True}, scanned_at="2026-11-09T04:00:00+00:00")["data"]["status"], "stale")
        self.assertEqual(self.call({"isPremium": True}, scanned_at=None)["data"]["status"], "no_scan")
        self.assertEqual(self.call({"isPremium": True}, rows=[])["data"]["status"], "no_scan")

    def test_model_cannot_call_plan_scan(self):
        self.assertNotIn("plan_scan", ARGUMENT_MODELS)
        self.assertNotIn("plan_scan", {tool["name"] for tool in TOOL_DEFINITIONS})

    def test_argument_bounds(self):
        for bad in ({"min_confidence": 49, "count": 3}, {"min_confidence": 101, "count": 3}, {"min_confidence": 95, "count": 0}, {"min_confidence": 95, "count": 6}, {"min_confidence": 95, "count": 3, "x": 1}):
            with self.assertRaises(ValidationError):
                PlanScanArguments.model_validate(bad)


if __name__ == "__main__":
    unittest.main()
