import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException

from app import member_entitlements as me


def request_for(pool=None, proof_user="owner-1"):
    state = {"subscriptions": []}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(v22_commercial={"state": state}, db_pool=pool)),
        # The request proof belongs to the signed-in OWNER, never to the inspected user.
        state=SimpleNamespace(member_entitlement_proof=me.EntitlementProof(proof_user, 1, {"master_trade_access": False})),
    )
    return request


class AdminTargetSubscriptionTests(unittest.TestCase):
    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def test_caller_proof_still_rejects_another_user(self):
        request = request_for()
        with self.assertRaises(HTTPException) as caught:
            me.subscription_for_request(request, {"id": "customer-1", "role": "CUSTOMER"})
        self.assertEqual(caught.exception.status_code, 503)

    def test_admin_lookup_for_another_user_does_not_use_the_caller_proof(self):
        request = request_for()
        with mock.patch.dict("os.environ", {"BINANCE_MANUAL_ACCESS_ENABLED": "false", "FREE_TRIAL_ENABLED": "false"}):
            result = self.run_async(me.admin_target_subscription(request, {"id": "customer-1", "role": "CUSTOMER"}))
        self.assertFalse(result["master_trade_access"])

    def test_owner_target_never_needs_the_database(self):
        request = request_for(pool=None)
        with mock.patch.dict("os.environ", {"BINANCE_MANUAL_ACCESS_ENABLED": "true"}):
            result = self.run_async(me.admin_target_subscription(request, {"id": "owner-2", "role": "OWNER"}))
        self.assertFalse(result["master_trade_access"])

    def test_enabled_without_database_fails_closed(self):
        request = request_for(pool=None)
        with mock.patch.dict("os.environ", {"BINANCE_MANUAL_ACCESS_ENABLED": "true"}):
            with self.assertRaises(HTTPException) as caught:
                self.run_async(me.admin_target_subscription(request, {"id": "customer-1", "role": "CUSTOMER"}))
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
