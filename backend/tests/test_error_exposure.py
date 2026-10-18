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

    def test_assistant_chat_keeps_only_safe_reply_fields(self):
        payload = {"reply": "Şu an yoğunuz, biraz sonra tekrar dene.", "language": "tr", "sources": ["get_my_access", "Bad Source!"],
                   "error_code": "budget", "detail": "asyncpg timeout 10.0.0.5", "trace": "Traceback"}
        result = exposure.safe_body(503, body(payload), "/api/assistant/chat", "ref-4")
        self.assertEqual(result, {"reply": payload["reply"], "language": "tr", "sources": ["get_my_access"], "error_code": "budget", "request_id": "ref-4"})

    def test_assistant_chat_without_a_reply_or_with_bad_fields_falls_back_to_safe_message(self):
        for payload in ({"detail": "Connection refused 10.0.0.5"}, {"reply": "  "}, {"reply": 5}, ["x"]):
            result = exposure.safe_body(502, body(payload), "/api/assistant/chat", "ref-5")
            self.assertEqual(result["code"], "SERVER_ERROR")
            self.assertNotIn("10.0.0.5", json.dumps(result))
        result = exposure.safe_body(503, body({"reply": "x", "language": "de", "error_code": "Budget; DROP", "sources": "get_my_access"}), "/api/assistant/chat", "r")
        self.assertEqual(result, {"reply": "x", "language": "tr", "sources": [], "request_id": "r"})

    def test_other_assistant_paths_keep_the_generic_message(self):
        result = exposure.safe_body(503, body({"detail": "Help knowledge base is unavailable"}), "/api/assistant/tools/search_help", "ref-6")
        self.assertEqual(result["code"], "SERVER_ERROR")
        self.assertNotIn("knowledge base", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
