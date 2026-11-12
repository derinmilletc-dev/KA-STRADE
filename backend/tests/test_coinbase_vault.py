"""Coinbase kasası: ayrı anahtar, ayrı tablo, düz metin yok."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from app.coinbase import vault  # noqa: E402
from app.coinbase.errors import CoinbaseError  # noqa: E402

MASTER = "coinbase-vault-master-key-0123456789"
OTHER = "another-master-key-9876543210abcd"
KEY_NAME = "organizations/org-123/apiKeys/key-456"
PEM = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
).decode()


def env(**values):
    base = {"COINBASE_VAULT_MASTER_KEY": "", "PROTREBOT_VAULT_MASTER_KEY": "", "PROTREBOT_WEB_ACCESS_TOKEN": ""}
    base.update(values)
    return patch.dict(os.environ, base, clear=False)


class FakePool:
    """asyncpg benzeri; yalnızca bu modülün kullandığı sorgu şekillerini tanır."""

    def __init__(self):
        self.rows = {}
        self.statements = []

    async def execute(self, sql, *args):
        self.statements.append(sql)
        if "CREATE TABLE" in sql:
            return "CREATE TABLE"
        if "INSERT INTO" in sql:
            user_id, blob, fingerprint, hint, permissions = args
            previous = self.rows.get(user_id, {})
            self.rows[user_id] = {
                "encrypted_payload": blob, "fingerprint": fingerprint, "key_hint": hint, "permissions": permissions,
                "last_test_at": "2026-11-12T12:00:00+00:00", "created_at": previous.get("created_at", "2026-11-12T12:00:00+00:00"),
                "updated_at": "2026-11-12T12:00:00+00:00",
            }
            return "INSERT 0 1"
        if sql.strip().startswith("DELETE"):
            return "DELETE 1" if self.rows.pop(args[0], None) is not None else "DELETE 0"
        raise AssertionError(f"beklenmeyen sorgu: {sql}")

    async def fetchrow(self, sql, *args):
        self.statements.append(sql)
        return self.rows.get(args[0])


def run(coro):
    return asyncio.run(coro)


class CryptoTests(unittest.TestCase):
    def setUp(self):
        vault.reset_schema_cache()

    def test_roundtrip_and_no_plaintext_in_blob(self):
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            blob = vault.encrypt_key(KEY_NAME, PEM)
            self.assertEqual(vault.decrypt_key(blob), (KEY_NAME, PEM))
        for needle in (PEM.encode(), KEY_NAME.encode(), b"BEGIN EC PRIVATE KEY", b"key-456"):
            self.assertNotIn(needle, blob)

    def test_wrong_master_key_gives_clear_error(self):
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            blob = vault.encrypt_key(KEY_NAME, PEM)
        with env(COINBASE_VAULT_MASTER_KEY=OTHER), self.assertRaises(CoinbaseError) as caught:
            vault.decrypt_key(blob)
        self.assertEqual(caught.exception.code, "VAULT_DECRYPT")
        self.assertIn("COINBASE_VAULT_MASTER_KEY", caught.exception.message)

    def test_missing_or_short_master_key_is_not_ready_and_never_falls_back_to_binance(self):
        with env(COINBASE_VAULT_MASTER_KEY="", PROTREBOT_VAULT_MASTER_KEY=MASTER, PROTREBOT_WEB_ACCESS_TOKEN=MASTER):
            self.assertFalse(vault.master_key_ready()[0])
            with self.assertRaises(CoinbaseError) as caught:
                vault.encrypt_key(KEY_NAME, PEM)
            self.assertEqual(caught.exception.code, "VAULT_NOT_READY")
        with env(COINBASE_VAULT_MASTER_KEY="short"):
            self.assertFalse(vault.master_key_ready()[0])
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            ok, reason = vault.master_key_ready()
            self.assertTrue(ok)
            self.assertIsNone(reason)

    def test_binance_vault_key_derivation_is_domain_separated(self):
        from cryptography.fernet import InvalidToken
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            blob = vault.encrypt_key(KEY_NAME, PEM)
        # Aynı gizli metinle bile başka bir alan öneki (Binance kasası gibi) bu kaydı açamaz.
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        foreign = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"ProTreBot:ExchangeVault:V28\0" + MASTER.encode()).digest()))
        with self.assertRaises(InvalidToken):
            foreign.decrypt(blob)

    def test_garbage_and_foreign_scope_are_rejected(self):
        from cryptography.fernet import Fernet
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            with self.assertRaises(CoinbaseError):
                vault.decrypt_key(b"not-a-token")
            foreign = vault.vault_cipher().encrypt(b'{"scope":"SOMETHING_ELSE","key_name":"a","private_key":"b"}')
            with self.assertRaises(CoinbaseError):
                vault.decrypt_key(foreign)
            incomplete = vault.vault_cipher().encrypt(f'{{"scope":"{vault.SCOPE}","key_name":"a"}}'.encode())
            with self.assertRaises(CoinbaseError):
                vault.decrypt_key(incomplete)
            self.assertIsInstance(Fernet, type)


class StorageTests(unittest.TestCase):
    def setUp(self):
        vault.reset_schema_cache()

    def test_save_load_meta_delete(self):
        pool = FakePool()
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            self.assertIsNone(run(vault.load_key(pool, "u1")))
            self.assertIsNone(run(vault.key_meta(pool, "u1")))
            run(vault.save_key(pool, "u1", KEY_NAME, PEM, {"can_transfer": False, "permissions_checked": True}))
            self.assertEqual(run(vault.load_key(pool, "u1")), (KEY_NAME, PEM))
            meta = run(vault.key_meta(pool, "u1"))
            self.assertEqual(meta["key_hint"], "ey-456")
            self.assertEqual(len(meta["fingerprint"]), 12)
            self.assertEqual(meta["permissions"], {"can_transfer": False, "permissions_checked": True})
            self.assertNotIn("private_key", meta)
            self.assertNotIn(PEM, repr(meta))
            self.assertNotIn("org-123", repr(meta))
            self.assertIs(run(vault.delete_key(pool, "u1")), True)
            self.assertIs(run(vault.delete_key(pool, "u1")), False)
            self.assertIsNone(run(vault.load_key(pool, "u1")))

    def test_stored_row_holds_only_ciphertext(self):
        pool = FakePool()
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            run(vault.save_key(pool, "u1", KEY_NAME, PEM, None))
        row = pool.rows["u1"]
        self.assertNotIn(PEM.encode(), row["encrypted_payload"])
        self.assertIsNone(row["permissions"])
        self.assertNotIn(PEM, repr({k: v for k, v in row.items() if k != "encrypted_payload"}))

    def test_users_are_separated_and_resave_overwrites(self):
        pool = FakePool()
        other_pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        ).decode()
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            run(vault.save_key(pool, "u1", KEY_NAME, PEM, None))
            run(vault.save_key(pool, "u2", "organizations/o/apiKeys/other-1", other_pem, None))
            self.assertEqual(run(vault.load_key(pool, "u1"))[1], PEM)
            self.assertEqual(run(vault.load_key(pool, "u2"))[1], other_pem)
            run(vault.save_key(pool, "u1", "organizations/o/apiKeys/newer-9", other_pem, None))
            self.assertEqual(run(vault.load_key(pool, "u1"))[0], "organizations/o/apiKeys/newer-9")

    def test_schema_uses_own_table_and_is_created_once_per_pool(self):
        pool = FakePool()
        with env(COINBASE_VAULT_MASTER_KEY=MASTER):
            run(vault.save_key(pool, "u1", KEY_NAME, PEM, None))
            run(vault.load_key(pool, "u1"))
        creates = [s for s in pool.statements if "CREATE TABLE" in s]
        self.assertEqual(len(creates), 1)
        self.assertIn("kaistrade_coinbase_vault", creates[0])
        self.assertNotIn("protrebot_exchange", "".join(pool.statements))

    def test_save_without_master_key_writes_nothing(self):
        pool = FakePool()
        with env(COINBASE_VAULT_MASTER_KEY=""), self.assertRaises(CoinbaseError):
            run(vault.save_key(pool, "u1", KEY_NAME, PEM, None))
        self.assertEqual(pool.rows, {})


if __name__ == "__main__":
    unittest.main()
