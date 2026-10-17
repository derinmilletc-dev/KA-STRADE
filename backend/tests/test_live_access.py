import unittest
from unittest import mock

from app import live_access

ON = {"MULTI_USER_LIVE": "1", "DATABASE_URL": "postgres://x"}
OFF = {"MULTI_USER_LIVE": "", "DATABASE_URL": "postgres://x"}
PAID = {"master_trade_access": True, "plan": "MASTER_MODE", "status": "ACTIVE", "mode": "STRIPE"}
PAST_DUE = {**PAID, "status": "PAST_DUE"}
TRIAL = {"master_trade_access": True, "plan": "TRIAL", "status": "TRIALING", "mode": "FREE_TRIAL"}
TRIAL_ENDED = {"master_trade_access": False, "plan": "TRIAL", "status": "EXPIRED", "mode": "FREE_TRIAL"}
FREE = {"master_trade_access": False, "plan": "NONE", "status": "NONE", "mode": "NONE"}


class LiveAccessTests(unittest.TestCase):
    def test_paid_member_is_entitled_with_or_without_flag(self):
        for env in (ON, OFF):
            with mock.patch.dict("os.environ", env):
                self.assertTrue(live_access.entitled(PAID))
                self.assertTrue(live_access.entitled(PAST_DUE))

    def test_trial_counts_only_when_multi_user_live_is_on(self):
        with mock.patch.dict("os.environ", ON):
            self.assertTrue(live_access.entitled(TRIAL))
        with mock.patch.dict("os.environ", OFF):
            self.assertFalse(live_access.entitled(TRIAL))

    def test_nobody_else_is_entitled(self):
        with mock.patch.dict("os.environ", ON):
            for subscription in (TRIAL_ENDED, FREE, {}, {"master_trade_access": True, "plan": "MASTER_MODE", "status": "CANCELLED"}):
                self.assertFalse(live_access.entitled(subscription), subscription)

    def test_roles(self):
        with mock.patch.dict("os.environ", ON):
            self.assertTrue(live_access.role_ok("OWNER"))
            self.assertTrue(live_access.role_ok("CUSTOMER"))
            self.assertFalse(live_access.role_ok("MODERATOR"))
            self.assertFalse(live_access.role_ok(None))
        with mock.patch.dict("os.environ", OFF):
            self.assertTrue(live_access.role_ok("OWNER"))
            self.assertFalse(live_access.role_ok("CUSTOMER"))


if __name__ == "__main__":
    unittest.main()
