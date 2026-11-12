"""Coinbase (CDP) ES256 JWT üretimi. Yeni paket gerekmez: yalnızca `cryptography`.

Kurallar (Coinbase belgesi, api-key-authentication):
- Algoritma ES256 (P-256). Ed25519 desteklenmez.
- Başlık: alg, kid (anahtar adı), nonce, typ. İçerik: sub, iss="cdp", nbf, exp=nbf+120, uri.
- uri = "METHOD host/path" (şemasız). Her istek için YENİ JWT.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from email.utils import parsedate_to_datetime

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .config import JWT_LIFETIME_SECONDS
from .errors import CoinbaseError

KEY_NAME_PATTERN = re.compile(r"^organizations/[A-Za-z0-9_-]+/apiKeys/[A-Za-z0-9_-]+$")

_KEY_FORMAT_HELP = (
    "Coinbase Developer Platform'dan 'ECDSA' (ES256) anahtarı oluştur; "
    "'-----BEGIN EC PRIVATE KEY-----' ile başlayan gizli anahtarı yapıştır."
)


def validate_key_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not KEY_NAME_PATTERN.fullmatch(cleaned):
        raise CoinbaseError(
            "KEY_FORMAT",
            "Anahtar adı biçimi yanlış. Şöyle olmalı: organizations/…/apiKeys/…  (eski tip anahtarlar çalışmaz).",
            http_status=422,
        )
    return cleaned


def normalize_pem(raw: str) -> str:
    text = (raw or "").strip().strip('"').strip("'")
    text = text.replace("\\r", "").replace("\\n", "\n").replace("\r", "")
    text = text.strip()
    # Tek satırlık alana yapıştırılınca satır sonları kaybolur: başlık/gövde/bitiş ayrıştırılıp 64'lük satırlarla yeniden kurulur.
    match = re.fullmatch(r"-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----", text, flags=re.DOTALL)
    if match:
        body = re.sub(r"\s+", "", match.group(2))
        if body:
            lines = [body[i:i + 64] for i in range(0, len(body), 64)]
            return f"-----BEGIN {match.group(1)}-----\n" + "\n".join(lines) + f"\n-----END {match.group(1)}-----"
    return text


def load_private_key(raw: str) -> ec.EllipticCurvePrivateKey:
    pem = normalize_pem(raw)
    if "BEGIN" not in pem or "PRIVATE KEY" not in pem:
        raise CoinbaseError("KEY_FORMAT", f"Gizli anahtar biçimi yanlış. {_KEY_FORMAT_HELP}", http_status=422)
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        # Hata metni anahtar parçası taşıyabilir; bilerek eklenmez.
        raise CoinbaseError("KEY_FORMAT", f"Gizli anahtar okunamadı. {_KEY_FORMAT_HELP}", http_status=422) from None
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise CoinbaseError(
            "KEY_FORMAT",
            f"Bu anahtar ES256 (P-256) değil. Ed25519 ve RSA anahtarları çalışmaz. {_KEY_FORMAT_HELP}",
            http_status=422,
        )
    return key


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_jwt(
    *,
    key_name: str,
    private_key: ec.EllipticCurvePrivateKey,
    method: str,
    host: str,
    path: str,
    now: float | None = None,
    nonce: str | None = None,
) -> str:
    issued = int(time.time() if now is None else now)
    clean_path = path.split("?", 1)[0]
    header = {"alg": "ES256", "kid": key_name, "nonce": nonce or secrets.token_hex(16), "typ": "JWT"}
    payload = {
        "sub": key_name,
        "iss": "cdp",
        "nbf": issued,
        "exp": issued + JWT_LIFETIME_SECONDS,
        "uri": f"{method.upper()} {host}{clean_path}",
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    )
    der_signature = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_signature)
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + _b64url(raw_signature)


def key_fingerprint(key_name: str) -> str:
    return hashlib.sha256(key_name.encode("utf-8")).hexdigest()[:12]


def key_hint(key_name: str) -> str:
    """Ekranda göstermek için anahtar kimliğinin son 6 karakteri."""
    return key_name.rsplit("/", 1)[-1][-6:]


def skew_seconds(local_epoch: float, date_header: str | None) -> float | None:
    """Yerel saat − Coinbase saati (sn). Pozitif: bizim saat ileride. Okunamazsa None."""
    if not date_header:
        return None
    try:
        server_epoch = parsedate_to_datetime(date_header).timestamp()
    except (TypeError, ValueError, IndexError):
        return None
    return local_epoch - server_epoch
