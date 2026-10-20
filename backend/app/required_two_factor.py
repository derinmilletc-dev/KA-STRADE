"""Zorunlu iki aşamalı doğrulama (2FA) kapısı. Varsayılan KAPALI: REQUIRE_2FA=true ile açılır.

Açıkken, 2FA'sı etkin olmayan bir hesap yalnızca kurulum için gereken uç noktalara erişebilir; geri kalan her
korumalı istek 403 alır. 2FA'sı olan hesaplar zaten her girişte kod sorar (`login_challenge`); burada atlama yoktur.
Kurulum ve çıkış yolları dışında hiçbir yol açılmaz (kapalı başarısızlık: okuma hatasında erişim verilmez).
"""
from __future__ import annotations

import os
from typing import Any


ENV = "REQUIRE_2FA"
SETUP_REQUIRED_DETAIL = "İki aşamalı doğrulama kurulumu gerekli"
SETUP_PATHS = frozenset({
    "/api/v22/session",
    "/api/v22/account/overview",
    "/api/v22/account/2fa/setup",
    "/api/v22/account/2fa/enable",
    "/api/v22/account/reauth/email",
    "/api/v22/auth/logout",
    "/api/exchange-connections/session",
})


def required(environ: Any = None) -> bool:
    value = (environ if environ is not None else os.environ).get(ENV, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def path_allowed(path: str) -> bool:
    return path.rstrip("/") in SETUP_PATHS


async def enforce(request: Any, user: dict[str, Any]) -> bool:
    """Kurulum gerekiyorsa True döner (istek yalnızca izinli yoldaysa); izinsiz yolda 403 fırlatır."""
    from fastapi import HTTPException

    if not required():
        return False
    request.state.two_factor_setup_required = False
    from .account_settings import enabled

    try:
        has_two_factor = await enabled(request, user)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "İki aşamalı doğrulama durumu okunamadı") from None
    if has_two_factor:
        return False
    if not path_allowed(request.url.path):
        raise HTTPException(403, SETUP_REQUIRED_DETAIL)
    request.state.two_factor_setup_required = True
    return True
