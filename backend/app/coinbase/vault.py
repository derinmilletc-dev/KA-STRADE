"""Coinbase için AYRI anahtar kasası (Binance kasasıyla hiçbir şeyi paylaşmaz).

- Ayrı tablo: kaistrade_coinbase_vault
- Ayrı şifre anahtarı: COINBASE_VAULT_MASTER_KEY (en az 24 karakter). Binance anahtarına geri düşmez.
- Gizli anahtar yalnızca Fernet şifreli saklanır; bellekte önbelleklenmez, her okumada çözülür.
- Cevaplara yalnızca özet bilgi (son 6 karakter, parmak izi, tarih) girer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .errors import CoinbaseError
from .jwt_auth import key_fingerprint, key_hint

TABLE = "kaistrade_coinbase_vault"
SCOPE = "COINBASE_ADVANCED_TRADE_CFM"
_SCHEMA_DONE: set[int] = set()


def _master_secret() -> str:
    return os.getenv("COINBASE_VAULT_MASTER_KEY", "").strip()


def master_key_ready() -> tuple[bool, str | None]:
    if len(_master_secret()) < 24:
        return False, "COINBASE_VAULT_MASTER_KEY ayarlı değil veya 24 karakterden kısa; Coinbase kasası hazır değil."
    return True, None


def vault_cipher(master_secret: str | None = None) -> Fernet:
    secret = (master_secret if master_secret is not None else _master_secret()).strip()
    if len(secret) < 24:
        raise CoinbaseError(
            "VAULT_NOT_READY",
            "COINBASE_VAULT_MASTER_KEY ayarlı değil veya 24 karakterden kısa; Coinbase kasası hazır değil.",
        )
    digest = hashlib.sha256(b"KaisTrade:CoinbaseVault:V1\0" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_key(key_name: str, pem: str, *, master_secret: str | None = None) -> bytes:
    payload = {
        "scope": SCOPE,
        "key_name": key_name,
        "private_key": pem,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    return vault_cipher(master_secret).encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def decrypt_key(blob: bytes, *, master_secret: str | None = None) -> tuple[str, str]:
    try:
        payload = json.loads(vault_cipher(master_secret).decrypt(bytes(blob)))
    except InvalidToken:
        raise CoinbaseError(
            "VAULT_DECRYPT",
            "Kasadaki Coinbase anahtarı açılamadı: COINBASE_VAULT_MASTER_KEY değişmiş olabilir. Anahtarı yeniden kaydet.",
        ) from None
    except ValueError:
        raise CoinbaseError("VAULT_DECRYPT", "Kasadaki Coinbase kaydı bozuk. Anahtarı yeniden kaydet.") from None
    if not isinstance(payload, dict) or payload.get("scope") != SCOPE:
        raise CoinbaseError("VAULT_DECRYPT", "Kasadaki kayıt bir Coinbase anahtarı değil. Anahtarı yeniden kaydet.")
    key_name, pem = payload.get("key_name"), payload.get("private_key")
    if not isinstance(key_name, str) or not isinstance(pem, str) or not key_name or not pem:
        raise CoinbaseError("VAULT_DECRYPT", "Kasadaki Coinbase kaydı eksik. Anahtarı yeniden kaydet.")
    return key_name, pem


async def ensure_schema(pool: Any) -> None:
    if id(pool) in _SCHEMA_DONE:
        return
    await pool.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
          user_id TEXT PRIMARY KEY,
          encrypted_payload BYTEA NOT NULL,
          fingerprint TEXT NOT NULL,
          key_hint TEXT NOT NULL,
          permissions JSONB,
          last_test_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _SCHEMA_DONE.add(id(pool))


def reset_schema_cache() -> None:
    _SCHEMA_DONE.clear()


async def save_key(pool: Any, user_id: str, key_name: str, pem: str, permissions: dict[str, Any] | None) -> None:
    await ensure_schema(pool)
    blob = encrypt_key(key_name, pem)
    await pool.execute(
        f"""
        INSERT INTO {TABLE} (user_id, encrypted_payload, fingerprint, key_hint, permissions, last_test_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
          encrypted_payload = EXCLUDED.encrypted_payload,
          fingerprint = EXCLUDED.fingerprint,
          key_hint = EXCLUDED.key_hint,
          permissions = EXCLUDED.permissions,
          last_test_at = NOW(),
          updated_at = NOW()
        """,
        user_id, blob, key_fingerprint(key_name), key_hint(key_name),
        json.dumps(permissions) if permissions is not None else None,
    )


async def load_key(pool: Any, user_id: str) -> tuple[str, str] | None:
    await ensure_schema(pool)
    row = await pool.fetchrow(f"SELECT encrypted_payload FROM {TABLE} WHERE user_id = $1", user_id)
    if row is None:
        return None
    return decrypt_key(bytes(row["encrypted_payload"]))


async def key_meta(pool: Any, user_id: str) -> dict[str, Any] | None:
    await ensure_schema(pool)
    row = await pool.fetchrow(
        f"SELECT fingerprint, key_hint, permissions, last_test_at, created_at, updated_at FROM {TABLE} WHERE user_id = $1",
        user_id,
    )
    if row is None:
        return None
    permissions = row["permissions"]
    if isinstance(permissions, str):
        try:
            permissions = json.loads(permissions)
        except ValueError:
            permissions = None

    def _iso(value: Any) -> str | None:
        return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)

    return {
        "fingerprint": row["fingerprint"],
        "key_hint": row["key_hint"],
        "permissions": permissions,
        "last_test_at": _iso(row["last_test_at"]),
        "saved_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
    }


async def delete_key(pool: Any, user_id: str) -> bool:
    await ensure_schema(pool)
    result = await pool.execute(f"DELETE FROM {TABLE} WHERE user_id = $1", user_id)
    return str(result).strip().endswith("1")
