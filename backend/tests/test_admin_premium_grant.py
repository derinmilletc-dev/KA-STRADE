import unittest
from datetime import datetime, timedelta, timezone

from app import member_entitlements, subscription_core as core

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
USER = {"id": "customer-1", "role": "CUSTOMER"}


def state_with(*rows):
    return {"subscriptions": list(rows)}


class AdminPremiumGrantTests(unittest.TestCase):
    def test_old_demo_rows_never_gave_access(self):
        end = (NOW + timedelta(days=30)).isoformat()
        for plan in ("PRO", "STARTER", "ELITE", "TRIAL"):
            row = {"id": "x", "user_id": USER["id"], "plan": plan, "status": "TEST_ACTIVE", "period_end": end, "provider": "MANUAL_DEMO"}
            self.assertFalse(core.entitlement_snapshot(state_with(row), USER["id"])["master_trade_access"], plan)

    def test_master_mode_grant_gives_real_premium(self):
        state = state_with()
        core.grant_admin_access(state, USER["id"], "MASTER_MODE", 30, NOW)
        snapshot = core.entitlement_snapshot(state, USER["id"])
        self.assertTrue(snapshot["master_trade_access"])
        self.assertEqual((snapshot["plan"], snapshot["status"]), ("MASTER_MODE", "ACTIVE"))
        access = member_entitlements.access(snapshot, USER)
        self.assertTrue(access["isPremium"])
        self.assertTrue(access["canExecuteMasterTrade"])

    def test_grant_expires_after_the_given_days(self):
        state = state_with()
        row = core.grant_admin_access(state, USER["id"], "MASTER_MODE", 1, datetime.now(timezone.utc) - timedelta(days=2))
        self.assertFalse(core.entitlement_snapshot(state, USER["id"])["master_trade_access"])
        self.assertEqual(row["provider"], core.ADMIN_PROVIDER)

    def test_regranting_replaces_the_previous_manual_grant(self):
        state = state_with()
        core.grant_admin_access(state, USER["id"], "MASTER_MODE", 30, NOW)
        core.grant_admin_access(state, USER["id"], "MASTER_MODE", 60, NOW)
        active = [row for row in state["subscriptions"] if row["status"] == "ACTIVE"]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(state["subscriptions"]), 2)

    def test_revoke_removes_access_but_keeps_paid_rows(self):
        end = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
        stripe_row = {"id": "s", "user_id": USER["id"], "plan": "MASTER_MODE", "status": "ACTIVE", "current_period_end": end, "provider": "STRIPE"}
        other_user = {"id": "o", "user_id": "someone-else", "plan": "MASTER_MODE", "status": "ACTIVE", "currentPeriodEnd": end, "provider": core.ADMIN_PROVIDER}
        state = state_with(stripe_row, other_user)
        core.grant_admin_access(state, USER["id"], "MASTER_MODE", 30)
        self.assertEqual(core.revoke_admin_access(state, USER["id"]), 1)
        self.assertEqual(stripe_row["status"], "ACTIVE")
        self.assertEqual(other_user["status"], "ACTIVE")
        # the customer still has premium only because of the real Stripe row
        self.assertTrue(core.entitlement_snapshot(state, USER["id"])["master_trade_access"])
        stripe_row["status"] = "CANCELLED"
        self.assertFalse(core.entitlement_snapshot(state, USER["id"])["master_trade_access"])

    def test_trial_grant_opens_master_trade_but_not_paid_status(self):
        state = state_with()
        core.grant_admin_access(state, USER["id"], "TRIAL", 7, NOW)
        snapshot = core.entitlement_snapshot(state, USER["id"])
        self.assertTrue(snapshot["master_trade_access"])
        self.assertEqual((snapshot["plan"], snapshot["status"]), ("TRIAL", "TRIALING"))

    def test_unknown_plan_is_rejected(self):
        with self.assertRaises(ValueError):
            core.grant_admin_access(state_with(), USER["id"], "PRO", 30, NOW)


if __name__ == "__main__":
    unittest.main()
