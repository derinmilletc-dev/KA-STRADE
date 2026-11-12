"""Coinbase özellik anahtarı ve izin listesi: varsayılan KAPALI, boş liste = kimse yok."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.coinbase import config, include_coinbase_if_enabled  # noqa: E402


def env(**values):
    base = {"COINBASE_ENABLED": "", "COINBASE_ALLOWED_EMAILS": "", "COINBASE_MIN_REQUEST_INTERVAL_SECONDS": ""}
    base.update(values)
    return patch.dict(os.environ, base, clear=False)


class FlagTests(unittest.TestCase):
    def test_default_is_off(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COINBASE_ENABLED", None)
            self.assertFalse(config.coinbase_enabled())

    def test_only_explicit_true_values_turn_it_on(self):
        for value, expected in (("true", True), ("TRUE", True), ("1", True), ("yes", True),
                                ("", False), ("false", False), ("0", False), ("on", False), ("enabled", False)):
            with self.subTest(value), env(COINBASE_ENABLED=value):
                self.assertEqual(config.coinbase_enabled(), expected)

    def test_disabled_flag_mounts_nothing(self):
        class App:
            state = type("S", (), {})()
            routers = []

            def include_router(self, router):
                self.routers.append(router)

        app = App()
        with env(COINBASE_ENABLED="false"):
            self.assertFalse(include_coinbase_if_enabled(app))
        self.assertEqual(app.routers, [])
        self.assertFalse(hasattr(app.state, "coinbase_enabled"))
        self.assertFalse(hasattr(app.state, "coinbase_load_error"))


class AllowlistTests(unittest.TestCase):
    def test_empty_list_allows_nobody(self):
        with env(COINBASE_ALLOWED_EMAILS=""):
            ok, reason = config.user_allowed({"id": "1", "role": "OWNER", "email": "a@b.com"})
        self.assertFalse(ok)
        self.assertIn("COINBASE_ALLOWED_EMAILS", reason)

    def test_owner_in_list_is_allowed_case_insensitive(self):
        with env(COINBASE_ALLOWED_EMAILS=" Me@Example.com ; other@x.com"):
            self.assertTrue(config.user_allowed({"id": "1", "role": "OWNER", "email": "me@example.COM"})[0])
            self.assertTrue(config.user_allowed({"id": "1", "role": "OWNER", "email": "other@x.com"})[0])

    def test_not_owner_not_listed_or_missing_email_is_denied(self):
        with env(COINBASE_ALLOWED_EMAILS="me@example.com"):
            self.assertFalse(config.user_allowed({"id": "1", "role": "CUSTOMER", "email": "me@example.com"})[0])
            self.assertFalse(config.user_allowed({"id": "1", "role": "OWNER", "email": "x@example.com"})[0])
            self.assertFalse(config.user_allowed({"id": "WEB_OWNER", "role": "OWNER"})[0])
            self.assertFalse(config.user_allowed(None)[0])

    def test_min_interval_is_bounded_and_tolerates_garbage(self):
        with env(COINBASE_MIN_REQUEST_INTERVAL_SECONDS="abc"):
            self.assertEqual(config.min_request_interval(), 0.25)
        with env(COINBASE_MIN_REQUEST_INTERVAL_SECONDS="99"):
            self.assertEqual(config.min_request_interval(), 5.0)
        with env(COINBASE_MIN_REQUEST_INTERVAL_SECONDS="-3"):
            self.assertEqual(config.min_request_interval(), 0.0)


if __name__ == "__main__":
    unittest.main()


class PublicModeTests(unittest.TestCase):
    def setUp(self):
        os.environ["COINBASE_ENABLED"] = "true"
        os.environ["COINBASE_PUBLIC"] = "true"
        os.environ.pop("COINBASE_ALLOWED_EMAILS", None)

    def tearDown(self):
        os.environ.pop("COINBASE_ENABLED", None)
        os.environ.pop("COINBASE_PUBLIC", None)

    def test_premium_member_allowed_without_allowlist(self):
        self.assertTrue(config.user_allowed({"id": "2", "role": "CUSTOMER", "email": "x@y.com"}, True)[0])

    def test_non_premium_member_denied_with_clear_reason(self):
        ok, reason = config.user_allowed({"id": "2", "role": "CUSTOMER", "email": "x@y.com"}, False)
        self.assertFalse(ok)
        self.assertIn("Premium", reason)
        self.assertFalse(config.user_allowed({"id": "2", "role": "CUSTOMER"}, None)[0])

    def test_owner_allowed_and_no_session_denied(self):
        self.assertTrue(config.user_allowed({"id": "1", "role": "OWNER"}, False)[0])
        self.assertFalse(config.user_allowed(None, True)[0])

    def test_public_needs_master_flag(self):
        os.environ.pop("COINBASE_ENABLED")
        self.assertFalse(config.public_enabled())

