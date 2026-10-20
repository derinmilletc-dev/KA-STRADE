import unittest
from datetime import datetime, timedelta, timezone

from app import closed_account_retention as retention

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def closed(days, **extra):
    return {"active": False, "closed_at": (NOW - timedelta(days=days)).isoformat(), **extra}


class ClosedAccountRetentionTests(unittest.TestCase):
    def test_due_only_after_retention(self):
        self.assertTrue(retention.is_due(closed(1827), NOW, 1826))
        self.assertFalse(retention.is_due(closed(1825), NOW, 1826))

    def test_never_due_when_active_owner_or_unclear(self):
        self.assertFalse(retention.is_due({**closed(2000), "active": True}, NOW, 1826))
        self.assertFalse(retention.is_due(closed(2000, role="OWNER"), NOW, 1826))
        self.assertFalse(retention.is_due({"active": False}, NOW, 1826))
        self.assertFalse(retention.is_due({"active": False, "closed_at": "bozuk"}, NOW, 1826))
        self.assertFalse(retention.is_due("{bozuk json", NOW, 1826))
        self.assertFalse(retention.is_due(None, NOW, 1826))

    def test_accepts_json_string_and_z_suffix(self):
        text = '{"active": false, "closed_at": "2019-01-01T00:00:00Z"}'
        self.assertTrue(retention.is_due(text, NOW, 1826))

    def test_safe_defaults(self):
        self.assertFalse(retention.retention_enabled({}))
        self.assertTrue(retention.dry_run({}))
        self.assertFalse(retention.dry_run({retention.DRY_RUN_ENV: "false"}))
        self.assertEqual(retention.retention_days({}), 1826)
        self.assertEqual(retention.retention_days({retention.DAYS_ENV: "10"}), 365)
        self.assertEqual(retention.retention_days({retention.DAYS_ENV: "abc"}), 1826)

    def test_due_user_ids_respects_limit(self):
        class Pool:
            async def fetch(self, _query):
                return [{"user_id": f"u{i}", "security": closed(2000)} for i in range(15)]
        import asyncio
        ids = asyncio.run(retention.due_user_ids(Pool(), NOW, 1826, limit=10))
        self.assertEqual(len(ids), 10)


if __name__ == "__main__":
    unittest.main()
