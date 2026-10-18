import json
import unittest

from app import error_exposure as exposure


def body(value):
    return json.dumps(value).encode()


class ErrorExposureTests(unittest.TestCase):
    def test_client_errors_and_success_are_untouched(self):
        self.assertIsNone(exposure.safe_body(200, body({}), "/api/x", "r"))
        self.assertIsNone(exposure.safe_body(422, body({"detail": "Geçersiz"}), "/api/x", "r"))

    def test_server_errors_hide_internal_detail_but_keep_reference(self):
        result = exposure.safe_body(502, body({"detail": "Connection refused 10.0.0.5"}), "/api/markets", "ref-1")
        self.assertNotIn("10.0.0.5", json.dumps(result))
        self.assertIn("ref-1", result["detail"])
        self.assertEqual(result["code"], "SERVER_ERROR")

    def test_exchange_connection_keeps_only_safe_fields(self):
        result = exposure.safe_body(
            502, body({"detail": {"message": "Key reddedildi", "code": "X", "endpoint": "/sapi", "credential_fingerprint": "abc", "error_type": "ConnectError"}}),
            "/api/exchange-connections/test", "ref-2",
        )
        self.assertEqual(result, {"message": "Key reddedildi", "code": "X", "request_id": "ref-2"})

    def test_unreadable_body_is_replaced(self):
        result = exposure.safe_body(503, b"garbage", "/api/exchange-connections/status", "ref-3")
        self.assertIn("ref-3", result["detail"])


if __name__ == "__main__":
    unittest.main()
