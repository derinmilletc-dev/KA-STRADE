import unittest
from datetime import datetime, timedelta, timezone

from app import original_v2_live as original


def day_text(offset=0):
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


class DailyPublicTests(unittest.TestCase):
    def test_yesterdays_lock_is_not_shown_as_locked(self):
        result = original.daily_public({"daily": {"day": day_text(-1), "locked": True, "loss": 9, "limit": 5}})
        self.assertFalse(result["locked"])
        self.assertTrue(result["stale_previous_day"])

    def test_todays_lock_stays_locked(self):
        result = original.daily_public({"daily": {"day": day_text(), "locked": True, "loss": 9, "limit": 5}})
        self.assertTrue(result["locked"])
        self.assertNotIn("stale_previous_day", result)

    def test_source_state_not_mutated(self):
        config = {"daily": {"day": day_text(-1), "locked": True}}
        original.daily_public(config)
        self.assertTrue(config["daily"]["locked"])

    def test_missing_daily(self):
        self.assertIsNone(original.daily_public({}))


if __name__ == "__main__":
    unittest.main()
