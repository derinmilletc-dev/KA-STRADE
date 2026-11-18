"""Açık işlem / belirsiz durumda hesap değişimi: onay yoksa 409, onay (force) varsa geçer."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import exchange_connections as ec


def _request(state):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v25_execution=state)))


class AccountSwitchAckTests(unittest.TestCase):
    def _run(self, force):
        state = {"live_session_authorization": {"user_id": "u1", "fingerprint": "AAAAAAAAAAAA", "trading_account_id": "old"},
                 "execution_state": "UNKNOWN", "reconciliation_required": True, "snapshot": {"positions": [{"s": "X"}]}, "plans": {}}
        with patch("app.v25_execution.current_user_id", return_value="u1"), \
             patch.object(ec, "account_identity", return_value="new"):
            return ec._live_credential_identity(_request(state), "BBBBBBBBBBBB", {}, force)

    def test_blocked_without_ack(self):
        with self.assertRaises(HTTPException) as ctx:
            self._run(False)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_allowed_with_ack(self):
        self.assertEqual(self._run(True), "new")


if __name__ == "__main__":
    unittest.main()
