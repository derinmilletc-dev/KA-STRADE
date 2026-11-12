"""Coinbase SALT OKUNUR HTTP istemcisi (B aşaması).

Güvenlik duvarları:
- Yalnızca GET var. Başka yöntem eklemek için bu dosyayı bilerek değiştirmek gerekir.
- Yalnızca aşağıdaki okuma yolları çağrılabilir; diğer her yol ağa çıkmadan reddedilir
  (emir, iptal, para aktarımı yolları bu listede YOK).
- Hata asla yutulmaz: her kötü cevap Türkçe mesajlı bir CoinbaseError olur.
- Gizli anahtar hiçbir mesaja, günlüğe veya cevaba yazılmaz.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec

from . import config
from .errors import CoinbaseError, clean_remote_message
from .jwt_auth import build_jwt, key_fingerprint, skew_seconds


class ReadOnlyViolation(RuntimeError):
    """Salt okunur olmayan bir yola gidilmek istendi (programlama hatası)."""


READ_ONLY_PATTERNS = (
    re.compile(r"/api/v3/brokerage/accounts"),
    re.compile(r"/api/v3/brokerage/key_permissions"),
    re.compile(r"/api/v3/brokerage/cfm/balance_summary"),
    re.compile(r"/api/v3/brokerage/cfm/positions"),
    re.compile(r"/api/v3/brokerage/cfm/intraday/current_margin_window"),
    re.compile(r"/api/v3/brokerage/products/[A-Za-z0-9][A-Za-z0-9._-]{0,39}"),
)

# (yöntem, adres, başlıklar, sorgu, zaman aşımı[, gövde]) -> (durum, başlıklar, gövde). Gövde yalnızca POST'ta verilir.
Transport = Callable[..., Awaitable[tuple[int, dict[str, str], bytes]]]

# Son ölçülen saat farkı (her Coinbase cevabındaki Date başlığından). Süreç geneli.
CLOCK: dict[str, Any] = {"skew_seconds": None, "measured_epoch": None, "measured_mono": None}
_PREFLIGHT_MAX_AGE_SECONDS = 60.0


def clock_status() -> dict[str, Any]:
    skew = CLOCK["skew_seconds"]
    return {
        "skew_seconds": None if skew is None else round(skew, 1),
        "ok": None if skew is None else abs(skew) <= config.MAX_CLOCK_SKEW_SECONDS,
        "measured_epoch": CLOCK["measured_epoch"],
    }


def reset_clock() -> None:
    CLOCK.update(skew_seconds=None, measured_epoch=None, measured_mono=None)


class _Limiter:
    """Anahtar başına istek aralığı + 429 sonrası bekleme. Kilitsiz: tek olay döngüsünde atomik."""

    def __init__(self) -> None:
        self.next_slot = 0.0
        self.blocked_until = 0.0


_LIMITERS: dict[str, _Limiter] = {}


def reset_limiters() -> None:
    _LIMITERS.clear()


async def httpx_transport(
    method: str, url: str, headers: dict[str, str], params: dict[str, Any] | None, timeout: float,
    content: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    import httpx  # tembel: kapalıyken veya testte gerekmez

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as http:
            response = await http.request(method, url, headers=headers, params=params, content=content)
    except httpx.TimeoutException:
        raise CoinbaseError(
            "NETWORK", "Coinbase zamanında cevap vermedi. Biraz sonra tekrar dene; sürerse Coinbase durum sayfasına bak.",
        ) from None
    except httpx.HTTPError:
        raise CoinbaseError(
            "NETWORK", "Coinbase'e bağlanılamadı. Sunucunun internet bağlantısını kontrol et.",
        ) from None
    return response.status_code, {k.lower(): v for k, v in response.headers.items()}, response.content


class CoinbaseClient:
    def __init__(
        self,
        key_name: str,
        private_key: ec.EllipticCurvePrivateKey,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        host: str = config.HOST,
        min_interval: float | None = None,
        timeout: float = config.REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._key_name = key_name
        self._private_key = private_key
        self._transport = transport or httpx_transport
        self._clock = clock
        self._mono = monotonic
        self._sleep = sleep
        self._host = host
        self._min_interval = config.min_request_interval() if min_interval is None else min_interval
        self._timeout = timeout
        self._limiter = _LIMITERS.setdefault(key_fingerprint(key_name), _Limiter())

    # ---- ön kontroller -------------------------------------------------
    def _read_patterns(self) -> tuple[re.Pattern[str], ...]:
        return READ_ONLY_PATTERNS

    def _write_patterns(self) -> tuple[re.Pattern[str], ...]:
        """Bu sınıfta yazma yolu YOK. Yazma ancak ayrı, dar izinli alt sınıfta açılır (trading_client)."""
        return ()

    def _guard(self, method: str, path: str) -> str:
        clean = path.split("?", 1)[0]
        patterns = self._read_patterns() if method == "GET" else self._write_patterns()
        if not any(pattern.fullmatch(clean) for pattern in patterns):
            raise ReadOnlyViolation(f"İzinli listede olmayan yol ({method}): {clean}")
        return clean

    def _preflight_clock(self) -> None:
        skew, measured = CLOCK["skew_seconds"], CLOCK["measured_mono"]
        if skew is None or measured is None or self._mono() - measured > _PREFLIGHT_MAX_AGE_SECONDS:
            return
        if abs(skew) > config.MAX_CLOCK_SKEW_SECONDS:
            raise CoinbaseError("CLOCK_SKEW", self._skew_message(skew) + " Saat düzelene kadar istek gönderilmiyor.")

    @staticmethod
    def _skew_message(skew: float) -> str:
        direction = "ileride" if skew > 0 else "geride"
        return f"Sunucu saati Coinbase saatinden yaklaşık {abs(skew):.0f} sn {direction}. Sunucu saatini düzelt."

    async def _wait_for_slot(self) -> None:
        limiter = self._limiter
        now = self._mono()
        if now < limiter.blocked_until:
            remaining = max(1, int(limiter.blocked_until - now + 0.999))
            raise CoinbaseError(
                "RATE_LIMITED",
                f"Coinbase istek sınırına takıldık. {remaining} sn sonra tekrar dene.",
                http_status=429,
                retry_after_seconds=remaining,
            )
        wait = max(0.0, limiter.next_slot - now)
        limiter.next_slot = max(now, limiter.next_slot) + self._min_interval
        if wait > 0:
            await self._sleep(wait)

    # ---- istek ---------------------------------------------------------
    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._execute("GET", path, params, None)

    async def _execute(
        self, method: str, path: str, params: dict[str, Any] | None, json_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        clean_path = self._guard(method, path)
        self._preflight_clock()
        await self._wait_for_slot()
        token = build_jwt(
            key_name=self._key_name,
            private_key=self._private_key,
            method=method,
            host=self._host,
            path=clean_path,
            now=self._clock(),
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "KaisTrade-Coinbase",
        }
        url = f"https://{self._host}{clean_path}"
        if json_body is None:
            status, response_headers, body = await self._transport(method, url, headers, params, self._timeout)
        else:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            status, response_headers, body = await self._transport(method, url, headers, params, self._timeout, payload)
        skew = self._record_skew(response_headers)
        return self._interpret(status, response_headers, body, skew)

    def _record_skew(self, response_headers: dict[str, str]) -> float | None:
        skew = skew_seconds(self._clock(), response_headers.get("date"))
        if skew is not None:
            CLOCK.update(skew_seconds=skew, measured_epoch=self._clock(), measured_mono=self._mono())
        return skew

    def _interpret(self, status: int, response_headers: dict[str, str], body: bytes, skew: float | None) -> dict[str, Any]:
        parsed: Any = None
        if body:
            try:
                parsed = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                parsed = None
        remote = ""
        if isinstance(parsed, dict):
            remote = clean_remote_message(parsed.get("message") or parsed.get("error"))
        suffix = f" Coinbase mesajı: {remote}" if remote else ""

        if 200 <= status < 300:
            if not isinstance(parsed, dict):
                raise CoinbaseError("BAD_RESPONSE", "Coinbase cevabı beklenen biçimde değil (JSON nesnesi yok).")
            return parsed
        if status == 401:
            clock_note = ""
            if skew is not None and abs(skew) > config.MAX_CLOCK_SKEW_SECONDS:
                clock_note = " " + self._skew_message(skew)
            raise CoinbaseError(
                "AUTH_FAILED",
                "Coinbase anahtarı kabul etmedi (401). Kontrol et: anahtar adı ve gizli anahtar doğru mu, "
                "anahtar silinmiş mi, sunucu saati doğru mu." + clock_note + suffix,
            )
        if status == 403:
            raise CoinbaseError(
                "PERMISSION_DENIED",
                "Coinbase bu isteğe izin vermedi (403). Anahtarda 'görüntüleme' izni açık mı ve adres kısıtı var mı kontrol et."
                + suffix,
            )
        if status == 404:
            raise CoinbaseError(
                "NOT_FOUND",
                "Coinbase bu isteği bulamadı (404). Vadeli (CFM) hesabın açık olmayabilir." + suffix,
            )
        if status == 429:
            retry = 30
            raw_retry = response_headers.get("retry-after", "")
            if raw_retry.strip().isdigit():
                retry = min(max(int(raw_retry.strip()), 1), 300)
            self._limiter.blocked_until = self._mono() + retry
            raise CoinbaseError(
                "RATE_LIMITED",
                f"Coinbase istek sınırına takıldık. {retry} sn sonra tekrar dene.",
                http_status=429,
                retry_after_seconds=retry,
            )
        if status >= 500:
            raise CoinbaseError("UPSTREAM", f"Coinbase şu an sorunlu (HTTP {status}). Biraz sonra tekrar dene.")
        raise CoinbaseError("UPSTREAM_REJECTED", f"Coinbase isteği reddetti (HTTP {status})." + suffix)
