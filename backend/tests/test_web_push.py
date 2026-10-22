import unittest
import unittest.mock

from app import web_push


class WebPushTests(unittest.TestCase):
    def test_event_categories_match_settings_screen(self):
        expected = {
            "LIVE_ENTRY": "entry", "LIVE_ENTRY_RECOVERED": "entry", "LIVE_CLOSE": "close", "LIVE_ENTRY_CANCELLED": "close",
            "PROTECTION_FAIL": "protection", "LIVE_STREAM_STALE": "connection", "LIVE_DISARM": "arm", "LIVE_EMERGENCY": "emergency",
            "ORIGINAL_SESSION_EXPIRING": "arm", "ORIGINAL_LIVE_PAUSED": "arm",
            "LIVE_STREAM_RECOVERED": None, "SCAN_STARTED": None,
        }
        for kind, category in expected.items():
            self.assertEqual(web_push.category_of(kind), category, kind)

    def test_only_known_push_services_are_accepted(self):
        for url in ("https://fcm.googleapis.com/fcm/send/abc", "https://web.push.apple.com/Qabc",
                    "https://updates.push.services.mozilla.com/wpush/v2/x"):
            self.assertTrue(web_push.trusted_endpoint(url), url)
        for url in ("https://evil.example/x", "http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.example/x"):
            self.assertFalse(web_push.trusted_endpoint(url), url)

    def test_not_configured_without_vapid_environment(self):
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(web_push.configured())


if __name__ == "__main__":
    unittest.main()
