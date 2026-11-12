"""Coinbase salt-okunur istemci: yol duvarı, hata çevirisi, hız sınırı, saat kayması. Ağ yok (sahte aktarım)."""
import asyncio
import base64
import email.utils
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from app.coinbase import client as cb  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402

KEY_NAME = "organizations/org-123/apiKeys/key-456"
NOW = 1_800_000_000.0
ACCOUNTS = "/api/v3/brokerage/accounts"


def run(coro):
    return asyncio.run(coro)


def date_header(offset: float = 0.0) -> dict:
    """Coinbase saati = NOW - offset ⇒ bizim saat 'offset' sn ileride."""
    return {"date": email.utils.formatdate(NOW - offset, usegmt=True)}


class Harness:
    def __init__(self, *responses, min_interval=0.0):
        self.responses = list(responses)
        self.calls = []
        self.sleeps = []
        self.mono = 1000.0
        self.key = ec.generate_private_key(ec.SECP256R1())

        async def transport(method, url, headers, params, timeout):
            self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "timeout": timeout})
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def sleep(seconds):
            self.sleeps.append(seconds)

        self.client = cb.CoinbaseClient(
            KEY_NAME, self.key, transport=transport, clock=lambda: NOW, monotonic=lambda: self.mono,
            sleep=sleep, min_interval=min_interval,
        )


def ok(payload=None, headers=None):
    return 200, headers or date_header(), json.dumps(payload if payload is not None else {"ok": True}).encode()


def fail(status, payload=None, headers=None):
    return status, headers or date_header(), json.dumps(payload).encode() if payload is not None else b""


class ClientTestCase(unittest.TestCase):
    def setUp(self):
        cb.reset_clock()
        cb.reset_limiters()


class ReadOnlyWallTests(ClientTestCase):
    def test_client_has_no_write_methods(self):
        h = Harness()
        for name in ("post", "put", "patch", "delete", "request", "send"):
            self.assertFalse(hasattr(h.client, name), name)

    def test_only_listed_read_paths_reach_the_network(self):
        h = Harness()
        forbidden = (
            "/api/v3/brokerage/orders", "/api/v3/brokerage/orders/close_position", "/api/v3/brokerage/orders/edit",
            "/api/v3/brokerage/orders/batch_cancel", "/api/v3/brokerage/cfm/sweeps/schedule",
            "/api/v3/brokerage/cfm/intraday/margin_setting", "/api/v3/brokerage/products/..",
            "/api/v3/brokerage/products/BTC/../../orders", "/api/v3/brokerage/accounts/extra",
            "/v2/accounts", "", "/",
        )
        for path in forbidden:
            with self.subTest(path), self.assertRaises(cb.ReadOnlyViolation):
                run(h.client.get(path))
        self.assertEqual(h.calls, [])

    def test_listed_paths_are_accepted(self):
        paths = (
            ACCOUNTS, "/api/v3/brokerage/key_permissions", "/api/v3/brokerage/cfm/balance_summary",
            "/api/v3/brokerage/cfm/positions", "/api/v3/brokerage/cfm/intraday/current_margin_window",
            "/api/v3/brokerage/products/BIT-28JUL23-CDE",
        )
        h = Harness(*[ok() for _ in paths])
        for path in paths:
            self.assertEqual(run(h.client.get(path)), {"ok": True})
        self.assertEqual([c["method"] for c in h.calls], ["GET"] * len(paths))


class RequestShapeTests(ClientTestCase):
    def test_request_is_signed_and_secret_free(self):
        h = Harness(ok({"accounts": []}))
        result = run(h.client.get(ACCOUNTS + "?limit=1", params={"limit": 1}))
        self.assertEqual(result, {"accounts": []})
        call = h.calls[0]
        self.assertEqual(call["url"], "https://api.coinbase.com" + ACCOUNTS)
        self.assertEqual(call["params"], {"limit": 1})
        self.assertTrue(call["headers"]["Authorization"].startswith("Bearer "))
        token = call["headers"]["Authorization"][7:]
        payload = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        self.assertEqual(payload["uri"], "GET api.coinbase.com" + ACCOUNTS)
        self.assertEqual(payload["exp"] - payload["nbf"], 120)

    def test_each_request_gets_its_own_token(self):
        h = Harness(ok(), ok())
        run(h.client.get(ACCOUNTS))
        run(h.client.get(ACCOUNTS))
        tokens = [c["headers"]["Authorization"] for c in h.calls]
        self.assertNotEqual(tokens[0], tokens[1])


class ErrorTranslationTests(ClientTestCase):
    def assertError(self, response, code, status=424):
        h = Harness(response)
        with self.assertRaises(CoinbaseError) as caught:
            run(h.client.get(ACCOUNTS))
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.http_status, status)
        return caught.exception

    def test_401_is_never_returned_as_401(self):
        exc = self.assertError(fail(401, {"message": "Unauthorized"}), "AUTH_FAILED")
        self.assertIn("401", exc.message)
        self.assertNotIn("saati Coinbase", exc.message)  # saat normal: saat uyarısı yok

    def test_401_with_clock_skew_says_so(self):
        exc = self.assertError(fail(401, {"message": "bad"}, date_header(offset=95)), "AUTH_FAILED")
        self.assertIn("95 sn ileride", exc.message)

    def test_401_with_slow_clock_says_behind(self):
        exc = self.assertError(fail(401, {}, date_header(offset=-70)), "AUTH_FAILED")
        self.assertIn("70 sn geride", exc.message)

    def test_403_404_5xx_and_other_4xx(self):
        self.assertError(fail(403, {"message": "no view permission"}), "PERMISSION_DENIED")
        exc = self.assertError(fail(404, {"message": "not found"}), "NOT_FOUND")
        self.assertIn("CFM", exc.message)
        self.assertError(fail(500), "UPSTREAM")
        self.assertError(fail(503, {"message": "down"}), "UPSTREAM")
        self.assertError(fail(418, {"message": "teapot"}), "UPSTREAM_REJECTED")

    def test_remote_message_is_shown_but_cleaned_and_truncated(self):
        exc = self.assertError(fail(403, {"message": "x\x00y\n" + "a" * 500}), "PERMISSION_DENIED")
        self.assertIn("Coinbase mesajı: x y", exc.message)
        self.assertNotIn("\x00", exc.message)
        self.assertLess(len(exc.message), 500)

    def test_bad_bodies_are_explicit_errors(self):
        self.assertError((200, date_header(), b"not json"), "BAD_RESPONSE")
        self.assertError((200, date_header(), b""), "BAD_RESPONSE")
        self.assertError((200, date_header(), b"[1,2]"), "BAD_RESPONSE")
        self.assertError((200, date_header(), b"null"), "BAD_RESPONSE")

    def test_network_failure_from_transport_is_passed_on(self):
        boom = CoinbaseError("NETWORK", "Coinbase'e bağlanılamadı.")
        h = Harness(boom)
        with self.assertRaises(CoinbaseError) as caught:
            run(h.client.get(ACCOUNTS))
        self.assertEqual(caught.exception.code, "NETWORK")


class RateLimitTests(ClientTestCase):
    def test_429_blocks_further_calls_without_touching_network(self):
        h = Harness(fail(429, {"message": "slow down"}, {**date_header(), "retry-after": "12"}), ok())
        with self.assertRaises(CoinbaseError) as first:
            run(h.client.get(ACCOUNTS))
        self.assertEqual((first.exception.code, first.exception.http_status, first.exception.retry_after_seconds), ("RATE_LIMITED", 429, 12))
        with self.assertRaises(CoinbaseError) as second:
            run(h.client.get(ACCOUNTS))
        self.assertEqual(second.exception.code, "RATE_LIMITED")
        self.assertEqual(len(h.calls), 1)
        h.mono += 13
        self.assertEqual(run(h.client.get(ACCOUNTS)), {"ok": True})
        self.assertEqual(len(h.calls), 2)

    def test_429_without_header_uses_safe_default_and_clamps(self):
        h = Harness(fail(429, {}), fail(429, {}, {**date_header(), "retry-after": "99999"}))
        with self.assertRaises(CoinbaseError) as a:
            run(h.client.get(ACCOUNTS))
        self.assertEqual(a.exception.retry_after_seconds, 30)
        h.mono += 31
        with self.assertRaises(CoinbaseError) as b:
            run(h.client.get(ACCOUNTS))
        self.assertEqual(b.exception.retry_after_seconds, 300)

    def test_requests_are_spaced(self):
        h = Harness(ok(), ok(), ok(), min_interval=0.5)
        for _ in range(3):
            run(h.client.get(ACCOUNTS))
        self.assertEqual(h.sleeps, [0.5, 1.0])


class ClockTests(ClientTestCase):
    def test_skew_is_recorded_from_every_response(self):
        h = Harness(ok(headers=date_header(offset=7)))
        run(h.client.get(ACCOUNTS))
        status = cb.clock_status()
        self.assertAlmostEqual(status["skew_seconds"], 7, delta=1)
        self.assertTrue(status["ok"])

    def test_big_skew_blocks_next_call_until_remeasure_window_passes(self):
        h = Harness(ok(headers=date_header(offset=100)), ok())
        run(h.client.get(ACCOUNTS))  # cevap geldi ama saat bozuk olarak ölçüldü
        self.assertFalse(cb.clock_status()["ok"])
        with self.assertRaises(CoinbaseError) as caught:
            run(h.client.get(ACCOUNTS))
        self.assertEqual(caught.exception.code, "CLOCK_SKEW")
        self.assertIn("100 sn ileride", caught.exception.message)
        self.assertEqual(len(h.calls), 1)
        h.mono += 61  # ölçüm bayatladı: saat düzelmiş olabilir, yeniden dene
        run(h.client.get(ACCOUNTS))
        self.assertEqual(len(h.calls), 2)

    def test_missing_date_header_does_not_invent_a_skew(self):
        h = Harness((200, {}, b"{}"))
        run(h.client.get(ACCOUNTS))
        self.assertIsNone(cb.clock_status()["skew_seconds"])
        self.assertIsNone(cb.clock_status()["ok"])


if __name__ == "__main__":
    unittest.main()
