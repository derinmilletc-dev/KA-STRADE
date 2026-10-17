import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app import premium_requests as pr, subscription_core as core
from app.moderator_access import ModeratorIdentity

MOD = ModeratorIdentity(user_id="mod-1", role="MODERATOR", permissions=("approvals.create",), auth_version=1)


def make_request():
    state = {
        "users": [{"id": "mod-1", "role": "MODERATOR", "email": "m@x.com"},
                  {"id": "cust-1", "role": "CUSTOMER", "email": "c@x.com"},
                  {"id": "own-1", "role": "OWNER", "email": "o@x.com"}],
        "subscriptions": [], "licenses": [], "audit": [], "premium_requests": [],
    }
    lock = asyncio.Lock()
    rt = {"state": state, "lock": lock}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(v22_commercial=rt)))
    return request, state


def run(coro):
    return asyncio.run(coro)


class PremiumRequestTests(unittest.TestCase):
    def setUp(self):
        for name in ("save_state", ):
            patcher = mock.patch.object(pr, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        owner = mock.patch.object(pr, "authenticated_user", lambda request, owner=False: {"id": "own-1"})
        owner.start()
        self.addCleanup(owner.stop)
        self.request, self.state = make_request()

    def create(self, days=30):
        return run(pr.create_request(pr.CreatePremiumRequest(target_user_id="cust-1", days=days, reason="Destek sorununu çözdü"), self.request, MOD))

    def test_request_alone_gives_no_premium(self):
        item = self.create()
        self.assertEqual(item["status"], "pending")
        self.assertFalse(core.entitlement_snapshot(self.state, "cust-1")["master_trade_access"])

    def test_owner_approval_grants_premium(self):
        item = self.create(14)
        done = run(pr.approve_request(self.request, None, item["id"]))
        self.assertEqual(done["status"], "approved")
        self.assertTrue(core.entitlement_snapshot(self.state, "cust-1")["master_trade_access"])

    def test_rejection_gives_nothing(self):
        item = self.create()
        done = run(pr.reject_request(self.request, pr.DecisionNote(note="Hayır"), item["id"]))
        self.assertEqual(done["status"], "rejected")
        self.assertFalse(core.entitlement_snapshot(self.state, "cust-1")["master_trade_access"])

    def test_second_pending_request_for_same_customer_is_refused(self):
        self.create()
        with self.assertRaises(Exception):
            self.create()

    def test_cannot_decide_twice(self):
        item = self.create()
        run(pr.reject_request(self.request, None, item["id"]))
        with self.assertRaises(Exception):
            run(pr.approve_request(self.request, None, item["id"]))
        self.assertFalse(core.entitlement_snapshot(self.state, "cust-1")["master_trade_access"])

    def test_expired_request_cannot_be_approved(self):
        item = self.create()
        self.state["premium_requests"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.assertRaises(Exception):
            run(pr.approve_request(self.request, None, item["id"]))
        self.assertEqual(self.state["premium_requests"][0]["status"], "expired")
        self.assertFalse(core.entitlement_snapshot(self.state, "cust-1")["master_trade_access"])

    def test_cancel_then_nothing_to_approve(self):
        item = self.create()
        run(pr.cancel_request(self.request, item["id"], MOD))
        with self.assertRaises(Exception):
            run(pr.approve_request(self.request, None, item["id"]))

    def test_only_customers_can_be_targeted(self):
        for target in ("own-1", "mod-1", "nobody"):
            with self.assertRaises(Exception):
                run(pr.create_request(pr.CreatePremiumRequest(target_user_id=target, days=7, reason="Uzun bir gerekçe"), self.request, MOD))

    def test_days_are_capped(self):
        with self.assertRaises(Exception):
            pr.CreatePremiumRequest(target_user_id="cust-1", days=365, reason="Uzun bir gerekçe")


if __name__ == "__main__":
    unittest.main()
