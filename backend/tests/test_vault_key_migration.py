"""Kasa anahtarı geçişi: eski kayıtlar yeni etkin anahtarla da açılır; şifreleme yalnızca etkin anahtarla yapılır."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import exchange_connections as vault  # noqa: E402

OLD_SECRET = "legacy-web-access-token-0123456789"
NEW_SECRET = "dedicated-vault-master-key-9876543210"
UNKNOWN_SECRET = "unrelated-secret-value-abcdefghijk"
API_KEY = "FAKEAPIKEY" + "0" * 22
SECRET_KEY = "FAKESECRET" + "0" * 22


class VaultKeyMigrationTests(unittest.TestCase):
    def env(self, **values):
        base = {"PROTREBOT_VAULT_MASTER_KEY": "", "PROTREBOT_WEB_ACCESS_TOKEN": "", "PROTREBOT_VAULT_LEGACY_KEY": ""}
        base.update(values)
        return patch.dict(os.environ, base, clear=False)

    def test_legacy_record_opens_with_dedicated_key_after_switch(self):
        ciphertext = vault.encrypt_credentials(API_KEY, SECRET_KEY, mode="TESTNET", master_secret=OLD_SECRET)
        with self.env(PROTREBOT_VAULT_MASTER_KEY=NEW_SECRET, PROTREBOT_WEB_ACCESS_TOKEN=OLD_SECRET):
            self.assertEqual(vault.decrypt_credentials(ciphertext, mode="TESTNET"), (API_KEY, SECRET_KEY))

    def test_new_records_are_encrypted_only_with_dedicated_key(self):
        with self.env(PROTREBOT_VAULT_MASTER_KEY=NEW_SECRET, PROTREBOT_WEB_ACCESS_TOKEN=OLD_SECRET):
            ciphertext = vault.encrypt_credentials(API_KEY, SECRET_KEY, mode="LIVE")
            self.assertEqual(vault.decrypt_credentials(ciphertext, mode="LIVE"), (API_KEY, SECRET_KEY))
        with self.assertRaises(vault.VaultError):
            vault.decrypt_credentials(ciphertext, mode="LIVE", master_secret=OLD_SECRET)

    def test_legacy_key_env_is_used_for_old_records(self):
        ciphertext = vault.encrypt_credentials(API_KEY, SECRET_KEY, mode="TESTNET", master_secret=OLD_SECRET)
        with self.env(PROTREBOT_VAULT_MASTER_KEY=NEW_SECRET, PROTREBOT_WEB_ACCESS_TOKEN="rotated-token-0123456789abcdefg",
                      PROTREBOT_VAULT_LEGACY_KEY=OLD_SECRET):
            self.assertEqual(vault.decrypt_credentials(ciphertext, mode="TESTNET"), (API_KEY, SECRET_KEY))

    def test_unknown_key_is_refused(self):
        ciphertext = vault.encrypt_credentials(API_KEY, SECRET_KEY, mode="TESTNET", master_secret=UNKNOWN_SECRET)
        with self.env(PROTREBOT_VAULT_MASTER_KEY=NEW_SECRET, PROTREBOT_WEB_ACCESS_TOKEN=OLD_SECRET):
            with self.assertRaises(vault.VaultError):
                vault.decrypt_credentials(ciphertext, mode="TESTNET")

    def test_explicit_master_secret_does_not_fall_back(self):
        ciphertext = vault.encrypt_credentials(API_KEY, SECRET_KEY, mode="TESTNET", master_secret=OLD_SECRET)
        with self.env(PROTREBOT_VAULT_MASTER_KEY=NEW_SECRET, PROTREBOT_WEB_ACCESS_TOKEN=OLD_SECRET):
            with self.assertRaises(vault.VaultError):
                vault.decrypt_credentials(ciphertext, mode="TESTNET", master_secret=NEW_SECRET)


if __name__ == "__main__":
    unittest.main()
