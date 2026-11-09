"""24 saatlik canlı risk izni bir kez verilir: geçerliyken tekrar verilmez (ARM sıfırlanmaz) ve sistem engeli izni 'bitmiş' göstermez."""
import asyncio
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import v25_execution as live

FP = "SHA256:ABC"


def _state(expires_in: float, **extra):
    state = {**live.initial_state(), "lock": asyncio.Lock(), "recovery_ready": True, "execution_state": "READY"}
    digest = live.policy_digest(state["policy"])
    state["web_consent"] = {"accepted_at": "x", "expires_at_epoch": time.time() + expires_in, "key_fingerprint": FP, "user_id": "owner",
                            "trading_account_id": "acct", "policy_digest": digest}
    state.update(extra)
    return state


def _status(state):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
    with patch.object(live, "live_credentials_status", return_value=("k" * 20, "s" * 20, FP)), \
            patch.object(live, "load_live_consent", return_value={}), \
            patch.object(live, "current_user_id", return_value="owner"), \
            patch.object(live, "live_owner_binding", return_value="owner:owner"), \
            patch.object(live, "current_trading_account_id", return_value="acct"):
        return live.consent_status(state, request)


class ConsentOncePer24hTests(unittest.TestCase):
    def test_valid_consent_is_active_and_held(self):
        s = _status(_state(20 * 3600))
        self.assertTrue(s["active"] and s["held"])

    def test_system_blocker_keeps_consent_held(self):
        for key, value in (("reconciliation_required", True), ("recovery_ready", False)):
            with self.subTest(key=key):
                s = _status(_state(20 * 3600, **{key: value}))
                self.assertFalse(s["active"])
                self.assertTrue(s["held"])                 # izin geçerli; yeniden "izin ver" gerekmez

    def test_expired_consent_is_not_held(self):
        s = _status(_state(-60))
        self.assertFalse(s["held"] or s["active"])

    def test_valid_consent_is_not_granted_again(self):
        self.assertTrue(self._still_valid(_state(20 * 3600)))

    def test_renewal_window_allows_renew(self):
        self.assertFalse(self._still_valid(_state(10 * 60)))

    def test_missing_or_expired_consent_is_granted(self):
        self.assertFalse(self._still_valid(_state(-60)))

    def test_system_blocker_does_not_count_as_active(self):
        self.assertFalse(self._still_valid(_state(20 * 3600, reconciliation_required=True)))

    def _still_valid(self, state):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))
        with patch.object(live, "live_credentials_status", return_value=("k" * 20, "s" * 20, FP)), \
                patch.object(live, "load_live_consent", return_value={}), \
                patch.object(live, "current_user_id", return_value="owner"), \
                patch.object(live, "live_owner_binding", return_value="owner:owner"), \
                patch.object(live, "current_trading_account_id", return_value="acct"):
            return live.consent_still_valid(state, request)


if __name__ == "__main__":
    unittest.main()
