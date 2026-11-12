"""Coinbase ES256 JWT: yapı, imza doğruluğu, anahtar kontrolü, saat kayması."""
import base64
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402

from app.coinbase import jwt_auth  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402

KEY_NAME = "organizations/org-123/apiKeys/key-456"


def _pem(private_key, fmt=serialization.PrivateFormat.TraditionalOpenSSL):
    return private_key.private_bytes(serialization.Encoding.PEM, fmt, serialization.NoEncryption()).decode()


def _b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class JwtBuildTests(unittest.TestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())

    def _token(self, **overrides):
        args = dict(key_name=KEY_NAME, private_key=self.key, method="get", host="api.coinbase.com",
                    path="/api/v3/brokerage/accounts", now=1_800_000_000, nonce="abc123")
        args.update(overrides)
        return jwt_auth.build_jwt(**args)

    def test_structure_matches_coinbase_rules(self):
        header, payload, _ = (json.loads(_b64d(p)) if i < 2 else None for i, p in enumerate(self._token().split(".")))
        self.assertEqual(header, {"alg": "ES256", "kid": KEY_NAME, "nonce": "abc123", "typ": "JWT"})
        self.assertEqual(payload["iss"], "cdp")
        self.assertEqual(payload["sub"], KEY_NAME)
        self.assertEqual(payload["nbf"], 1_800_000_000)
        self.assertEqual(payload["exp"] - payload["nbf"], 120)
        self.assertEqual(payload["uri"], "GET api.coinbase.com/api/v3/brokerage/accounts")

    def test_query_string_is_not_part_of_uri(self):
        payload = json.loads(_b64d(self._token(path="/api/v3/brokerage/accounts?limit=1").split(".")[1]))
        self.assertEqual(payload["uri"], "GET api.coinbase.com/api/v3/brokerage/accounts")

    def test_signature_verifies_with_public_key(self):
        token = self._token()
        head, body, signature = token.split(".")
        raw = _b64d(signature)
        self.assertEqual(len(raw), 64)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        self.key.public_key().verify(der, f"{head}.{body}".encode("ascii"), ec.ECDSA(hashes.SHA256()))

    def test_every_token_gets_a_fresh_nonce(self):
        first = json.loads(_b64d(self._token(nonce=None).split(".")[0]))["nonce"]
        second = json.loads(_b64d(self._token(nonce=None).split(".")[0]))["nonce"]
        self.assertNotEqual(first, second)


class KeyLoadingTests(unittest.TestCase):
    def test_accepts_pem_and_escaped_newlines(self):
        key = ec.generate_private_key(ec.SECP256R1())
        pem = _pem(key)
        self.assertIsInstance(jwt_auth.load_private_key(pem), ec.EllipticCurvePrivateKey)
        self.assertIsInstance(jwt_auth.load_private_key(pem.replace("\n", "\\n")), ec.EllipticCurvePrivateKey)
        self.assertIsInstance(jwt_auth.load_private_key(f'"{pem}"'), ec.EllipticCurvePrivateKey)

    def test_accepts_single_line_paste_without_newlines(self):
        # Tek satırlık (password) alana yapıştırınca satır sonları kaybolur; yine de okunmalı.
        for fmt in (serialization.PrivateFormat.TraditionalOpenSSL, serialization.PrivateFormat.PKCS8):
            key = ec.generate_private_key(ec.SECP256R1())
            flat = _pem(key, fmt).replace("\n", "")
            self.assertIsInstance(jwt_auth.load_private_key(flat), ec.EllipticCurvePrivateKey)
            spaced = _pem(key, fmt).replace("\n", " ")
            self.assertIsInstance(jwt_auth.load_private_key(spaced), ec.EllipticCurvePrivateKey)

    def test_accepts_pkcs8_ec_key(self):
        key = ec.generate_private_key(ec.SECP256R1())
        loaded = jwt_auth.load_private_key(_pem(key, serialization.PrivateFormat.PKCS8))
        self.assertIsInstance(loaded, ec.EllipticCurvePrivateKey)

    def test_rejects_ed25519_rsa_other_curve_and_garbage(self):
        bad = {
            "ed25519": _pem(ed25519.Ed25519PrivateKey.generate(), serialization.PrivateFormat.PKCS8),
            "rsa": _pem(rsa.generate_private_key(public_exponent=65537, key_size=2048)),
            "p384": _pem(ec.generate_private_key(ec.SECP384R1())),
            "garbage": "-----BEGIN EC PRIVATE KEY-----\nnot-a-real-key\n-----END EC PRIVATE KEY-----",
            "empty": "",
            "no-pem": "just some text",
        }
        for name, value in bad.items():
            with self.subTest(name), self.assertRaises(CoinbaseError) as caught:
                jwt_auth.load_private_key(value)
            self.assertEqual(caught.exception.code, "KEY_FORMAT")
            self.assertEqual(caught.exception.http_status, 422)

    def test_error_text_never_contains_key_material(self):
        secret_line = "SECRETMATERIALSECRETMATERIAL"
        with self.assertRaises(CoinbaseError) as caught:
            jwt_auth.load_private_key(f"-----BEGIN EC PRIVATE KEY-----\n{secret_line}\n-----END EC PRIVATE KEY-----")
        self.assertNotIn(secret_line, caught.exception.message)

    def test_key_name_validation(self):
        self.assertEqual(jwt_auth.validate_key_name(f"  {KEY_NAME} "), KEY_NAME)
        for bad in ("", "abc", "organizations/x/apiKeys/", "organizations//apiKeys/y", "apiKeys/y", KEY_NAME + "/extra"):
            with self.subTest(bad), self.assertRaises(CoinbaseError):
                jwt_auth.validate_key_name(bad)

    def test_hint_and_fingerprint_do_not_expose_full_name(self):
        self.assertEqual(jwt_auth.key_hint(KEY_NAME), "ey-456")
        self.assertEqual(len(jwt_auth.key_fingerprint(KEY_NAME)), 12)
        self.assertNotIn("org-123", jwt_auth.key_fingerprint(KEY_NAME))


class SkewTests(unittest.TestCase):
    def test_skew_sign_and_unreadable_header(self):
        # 2027-01-15 08:00:00 UTC = 1800000000
        header = "Fri, 15 Jan 2027 08:00:00 GMT"
        server = 1_800_000_000
        import email.utils
        self.assertAlmostEqual(jwt_auth.skew_seconds(server + 30, email.utils.formatdate(server, usegmt=True)), 30, places=0)
        self.assertAlmostEqual(jwt_auth.skew_seconds(server - 45, email.utils.formatdate(server, usegmt=True)), -45, places=0)
        self.assertIsNone(jwt_auth.skew_seconds(server, None))
        self.assertIsNone(jwt_auth.skew_seconds(server, "not a date"))
        self.assertIsNotNone(header)


if __name__ == "__main__":
    unittest.main()
