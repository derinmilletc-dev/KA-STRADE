import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from fastapi import HTTPException  # noqa: E402

from app import v25_execution as live  # noqa: E402


def _request(state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))


class AutoAuthorizeManualTests(unittest.TestCase):
    def _run(self, state, ready=True, consent_active=False):
        release = {"ready": ready, "gates": [{"label": "Risk limitleri onayı", "passed": ready}]}
        with patch.object(live, "execution_owner", return_value={"id": "owner"}), \
                patch.object(live, "require_live_understanding", AsyncMock()), \
                patch.object(live, "consent_status", return_value={"active": consent_active}), \
                patch.object(live, "live_credentials_status", return_value=("k" * 20, "s" * 20, "SHA256:ABC")), \
                patch.object(live, "current_trading_account_id", return_value="acct"), \
                patch.object(live, "live_owner_binding", return_value="owner:owner"), \
                patch.object(live, "readiness", return_value=release), \
                patch.object(live, "persist_state", lambda s: None):
            asyncio.run(live.auto_authorize_manual(_request(state)))

    def _state(self):
        return {**live.initial_state(), "lock": asyncio.Lock(), "recovery_ready": True}

    def test_grants_consent_and_arms(self):
        state = self._state()
        self._run(state)
        self.assertTrue(live.is_armed(state))
        self.assertFalse(state["real_trading_locked"])
        self.assertEqual(state["web_consent"]["key_fingerprint"], "SHA256:ABC")
        kinds = [event.get("kind") for event in state["events"]]
        self.assertIn("LIVE_AUTO_ARM", kinds)
        self.assertIn("LIVE_AUTO_CONSENT", kinds)

    def test_readiness_gate_reported(self):
        state = self._state()
        with self.assertRaises(HTTPException) as ctx:
            self._run(state, ready=False)
        self.assertEqual(ctx.exception.status_code, 423)
        self.assertIn("Risk limitleri onayı", ctx.exception.detail)
        self.assertFalse(live.is_armed(state))

    def test_blocked_state_is_not_armed(self):
        state = self._state()
        state["recovery_ready"] = False
        self._run(state)
        self.assertFalse(live.is_armed(state))


if __name__ == "__main__":
    unittest.main()
