"""Coinbase özellik anahtarı ve erişim izni.

Bu dosya ağ, veritabanı ve FastAPI kullanmaz; yalnızca ortam değişkenlerini okur.

COINBASE_ENABLED         "true" değilse hiçbir Coinbase rotası yüklenmez (varsayılan: kapalı).
COINBASE_ALLOWED_EMAILS  Virgülle ayrılmış e-posta listesi. Boşsa kimseye izin verilmez.
COINBASE_ORDERS_ENABLED  "true" değilse emir rotaları yüklenmez ve emir gönderen kod çalışmaz (varsayılan: kapalı).
"""

from __future__ import annotations

import os
from typing import Any

TRUE_VALUES = frozenset({"true", "1", "yes"})

HOST = "api.coinbase.com"
BASE_URL = f"https://{HOST}"
JWT_LIFETIME_SECONDS = 120
REQUEST_TIMEOUT_SECONDS = 10.0
# Bu farktan büyük saat kayması açık hata sayılır (JWT ömrü 120 sn; Coinbase tolerans yazmıyor).
MAX_CLOCK_SKEW_SECONDS = 20.0


def coinbase_enabled() -> bool:
    return os.getenv("COINBASE_ENABLED", "false").strip().lower() in TRUE_VALUES


def orders_enabled() -> bool:
    """Emir gönderme ayrı bir anahtardır: COINBASE_ENABLED açık OLSA BİLE bu açılmadıkça tek emir gitmez."""
    return coinbase_enabled() and os.getenv("COINBASE_ORDERS_ENABLED", "false").strip().lower() in TRUE_VALUES


def resting_enabled() -> bool:
    """Bekleyen (dolana kadar duran) limit giriş ayrı anahtardır; Coinbase'de 'ekli bracket' doğrulanana kadar KAPALI tutulur."""
    return orders_enabled() and os.getenv("COINBASE_RESTING_ENABLED", "false").strip().lower() in TRUE_VALUES


def allowed_emails() -> frozenset[str]:
    raw = os.getenv("COINBASE_ALLOWED_EMAILS", "")
    return frozenset(item.strip().casefold() for item in raw.replace(";", ",").split(",") if item.strip())


def min_request_interval() -> float:
    """İki Coinbase isteği arasındaki en kısa süre (sn). Hız sınırı belgelenmediği için ihtiyatlı."""
    try:
        value = float(os.getenv("COINBASE_MIN_REQUEST_INTERVAL_SECONDS", "0.25"))
    except ValueError:
        return 0.25
    return min(max(value, 0.0), 5.0)


def public_enabled() -> bool:
    """Müşterilere açık mod: izin listesi aranmaz; Binance canlı işlemle aynı üyelik şartı (premium) aranır."""
    return coinbase_enabled() and os.getenv("COINBASE_PUBLIC", "false").strip().lower() in TRUE_VALUES


def user_allowed(user: dict[str, Any] | None, premium: bool | None = None) -> tuple[bool, str]:
    """(izinli mi, değilse Türkçe neden).
    Kapalı mod: hesap hem OWNER hem izin listesinde olmalı.
    Herkese açık mod (COINBASE_PUBLIC): giriş yapmış üye yeter; OWNER değilse premium üyelik şart (Binance canlı işlemle aynı)."""
    if not user:
        return False, "Oturum gerekli"
    if public_enabled():
        if user.get("role") == "OWNER" or premium is True:
            return True, ""
        return False, "Coinbase manuel işlem Premium üyelikle açılır."
    if user.get("role") != "OWNER":
        return False, "Coinbase yalnızca yönetici hesabı için açık."
    allowed = allowed_emails()
    if not allowed:
        return False, "Coinbase için izinli hesap tanımlı değil (COINBASE_ALLOWED_EMAILS boş)."
    email = str(user.get("email") or "").strip().casefold()
    if not email or email not in allowed:
        return False, "Bu hesap Coinbase için izinli değil."
    return True, ""
