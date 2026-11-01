"""KAISTRADE Instagram vitrini: giriş ekranındaki telefon için herkese açık, önbellekli akış.

Yalnız Meta'nın resmi "Instagram API with Instagram Login" uç noktaları (graph.instagram.com) kullanılır;
izin: instagram_business_basic. Tüm kimlikli istekler sunucuda yapılır. Erişim anahtarı, Meta'nın ham
sayfalama bağlantıları ve hata ayrıntıları tarayıcıya hiçbir zaman gönderilmez; yalnız normalleştirilmiş,
izinli alanlar döner. Sayfalama yalnız bu sunucunun daha önce verdiği imleçlerle yapılır (keyfi istekle
Meta'ya çağrı yaptırılamaz). Meta yanıt vermezse son başarılı içerik "stale" işaretiyle gösterilir.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


class TokenLogFilter(logging.Filter):
    """httpx/httpcore istek günlüklerinde Instagram erişim anahtarı geçen satırları düşürür."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith(("httpx", "httpcore")):
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - biçimlenemeyen kayıt güvenli tarafta atılır
            return False
        return "access_token" not in message and "graph.instagram.com" not in message


def protect_logging() -> None:
    guard = TokenLogFilter()
    targets = [logging.getLogger(name) for name in ("httpx", "httpcore")] + list(logging.getLogger().handlers)
    for target in targets:
        if not any(isinstance(item, TokenLogFilter) for item in target.filters):
            target.addFilter(guard)

FEED_PATH = "/api/public/instagram/feed"
GRAPH_HOST = "https://graph.instagram.com"
DEFAULT_USERNAME = "kaistrade_"
DEFAULT_API_VERSION = "v26.0"
PAGE_SIZE = 12
CACHE_SECONDS = 12 * 60          # içerik yaklaşık 12 dakikada bir yenilenir
STALE_SECONDS = 7 * 24 * 3600    # Meta hata verirse son içerik en fazla bu kadar gösterilir
RETRY_AFTER_ERROR_SECONDS = 60   # hatadan sonra Meta'yı en erken tekrar deneme
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
MAX_CACHED_PAGES = 24
MAX_CAPTION = 2200
MAX_BIO = 300
REFRESH_EVERY_SECONDS = 7 * 24 * 3600   # uzun ömürlü anahtar haftada bir yenilenir (60 gün geçerli)
REFRESH_RETRY_SECONDS = 24 * 3600       # Meta, 24 saatten genç anahtarı yenilemez
CURSOR_PATTERN = re.compile(r"[A-Za-z0-9_\-=]{1,512}")
VERSION_PATTERN = re.compile(r"v\d{1,3}\.\d{1,2}")
USER_ID_PATTERN = re.compile(r"\d{1,40}")
MEDIA_TYPES = frozenset({"IMAGE", "VIDEO", "CAROUSEL_ALBUM"})
MEDIA_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")
PERMALINK_HOSTS = frozenset({"www.instagram.com", "instagram.com"})

# Alan setleri: Meta bir alanı desteklemezse (hata kodu 100) bir sonrakine geçilir; uydurma alan yoktur.
PROFILE_FIELD_SETS = (
    "user_id,username,name,profile_picture_url,biography,media_count",
    "user_id,username,name,profile_picture_url,media_count",
    "user_id,username",
)
CHILD_FIELDS = "id,media_type,media_url,thumbnail_url"
MEDIA_FIELD_SETS = (
    f"id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,children{{{CHILD_FIELDS}}}",
    "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp",
    "id,media_type,media_url,thumbnail_url,permalink,timestamp",
)


class InstagramError(Exception):
    """Meta çağrısı başarısız; ayrıntı yalnız güvenli bir kod olarak tutulur."""

    def __init__(self, code: str, *, field_error: bool = False):
        super().__init__(code)
        self.code = code
        self.field_error = field_error


@dataclass(frozen=True)
class InstagramConfig:
    username: str
    user_id: str
    token: str
    version: str

    @property
    def configured(self) -> bool:
        return bool(self.token)


def load_config(environ: dict[str, str] | None = None) -> InstagramConfig:
    env = os.environ if environ is None else environ
    username = str(env.get("INSTAGRAM_USERNAME") or DEFAULT_USERNAME).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
        username = DEFAULT_USERNAME
    user_id = str(env.get("INSTAGRAM_USER_ID") or "").strip()
    version = str(env.get("INSTAGRAM_API_VERSION") or DEFAULT_API_VERSION).strip()
    return InstagramConfig(
        username=username,
        user_id=user_id if USER_ID_PATTERN.fullmatch(user_id) else "me",
        token=str(env.get("INSTAGRAM_ACCESS_TOKEN") or "").strip(),
        version=version if VERSION_PATTERN.fullmatch(version) else DEFAULT_API_VERSION,
    )


def profile_url(username: str) -> str:
    return f"https://www.instagram.com/{username}/"


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- normalleştirme

def safe_media_url(value: Any) -> str | None:
    """Yalnız Instagram/Meta CDN'indeki https adresleri; başka her şey atılır."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host.endswith(suffix) for suffix in MEDIA_HOST_SUFFIXES):
        return None
    return value


def safe_permalink(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 512:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in PERMALINK_HOSTS:
        return None
    return value


def safe_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.replace("\x00", "").strip()
    return text[:limit] if text else None


def safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return value


def safe_id(value: Any) -> str | None:
    text = str(value) if isinstance(value, (str, int)) else ""
    return text if re.fullmatch(r"[0-9_]{1,64}", text) else None


def normalize_child(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    media_id, media_type = safe_id(raw.get("id")), raw.get("media_type")
    if media_id is None or media_type not in ("IMAGE", "VIDEO"):
        return None
    media_url, thumbnail = safe_media_url(raw.get("media_url")), safe_media_url(raw.get("thumbnail_url"))
    if not media_url and not thumbnail:
        return None
    return {"id": media_id, "media_type": media_type, "media_url": media_url, "thumbnail_url": thumbnail}


def normalize_media(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    media_id, media_type = safe_id(raw.get("id")), raw.get("media_type")
    permalink = safe_permalink(raw.get("permalink"))
    if media_id is None or media_type not in MEDIA_TYPES or permalink is None:
        return None
    children_raw = raw.get("children", {}).get("data") if isinstance(raw.get("children"), dict) else None
    children = [child for child in (normalize_child(row) for row in (children_raw or [])[:20]) if child]
    media_url, thumbnail = safe_media_url(raw.get("media_url")), safe_media_url(raw.get("thumbnail_url"))
    if not media_url and not thumbnail and not children:
        return None  # gösterilecek görsel yok; kart uydurulmaz
    item = {
        "id": media_id, "media_type": media_type, "media_url": media_url, "thumbnail_url": thumbnail,
        "permalink": permalink, "caption": safe_text(raw.get("caption"), MAX_CAPTION),
        "timestamp": safe_timestamp(raw.get("timestamp")),
    }
    if media_type == "CAROUSEL_ALBUM":
        item["children"] = children
    return item


def normalize_profile(raw: Any, config: InstagramConfig) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    username = raw.get("username") if isinstance(raw.get("username"), str) and re.fullmatch(r"[A-Za-z0-9._]{1,30}", raw["username"]) else config.username
    media_count = raw.get("media_count")
    return {
        "username": username,
        "name": safe_text(raw.get("name"), 80),
        "profile_picture_url": safe_media_url(raw.get("profile_picture_url")),
        "biography": safe_text(raw.get("biography"), MAX_BIO),
        "media_count": media_count if isinstance(media_count, int) and 0 <= media_count < 10_000_000 else None,
        "profile_url": profile_url(username),
    }


def next_cursor(payload: Any) -> str | None:
    """Yalnız Meta'nın 'after' imleci alınır; ham 'next' bağlantısı (anahtar içerir) asla kullanılmaz."""
    paging = payload.get("paging") if isinstance(payload, dict) else None
    if not isinstance(paging, dict) or not paging.get("next"):
        return None
    cursor = (paging.get("cursors") or {}).get("after") if isinstance(paging.get("cursors"), dict) else None
    return cursor if isinstance(cursor, str) and CURSOR_PATTERN.fullmatch(cursor) else None


# --------------------------------------------------------------------------- erişim anahtarı

class TokenStore:
    """Uzun ömürlü anahtar: ortam değişkeninden gelir, haftalık yenilenir; yenisi şifreli dosyada tutulur.

    Ortam değişkeni değişirse (yeni anahtar girilirse) dosyadaki eski anahtar yok sayılır.
    """

    def __init__(self, path: Path | None):
        self.path = path
        self.last_attempt = 0.0

    def cipher(self, secret: bytes | None) -> Fernet | None:
        if not secret:
            return None
        key = hashlib.sha256(b"KAISTRADE:INSTAGRAM-TOKEN:V1\0" + secret).digest()
        return Fernet(base64.urlsafe_b64encode(key))

    def read(self, secret: bytes | None) -> dict[str, Any] | None:
        cipher = self.cipher(secret)
        if cipher is None or self.path is None:
            return None
        try:
            raw = json.loads(cipher.decrypt(self.path.read_bytes()))
        except (OSError, InvalidToken, ValueError):
            return None
        return raw if isinstance(raw, dict) and isinstance(raw.get("token"), str) else None

    def write(self, secret: bytes | None, record: dict[str, Any]) -> None:
        cipher = self.cipher(secret)
        if cipher is None or self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_bytes(cipher.encrypt(json.dumps(record).encode()))
            os.chmod(temporary, 0o600)
            temporary.replace(self.path)
        except OSError:
            logger.warning("instagram_token_store_unavailable")

    def current(self, config: InstagramConfig, secret: bytes | None) -> tuple[str, dict[str, Any] | None]:
        stored = self.read(secret)
        if stored and stored.get("source") == fingerprint(config.token):
            return stored["token"], stored
        return config.token, None

    async def maybe_refresh(self, config: InstagramConfig, secret: bytes | None, client: httpx.AsyncClient, now: float) -> None:
        token, stored = self.current(config, secret)
        refreshed_at = float(stored.get("refreshed_at", 0)) if stored else 0.0
        if now - refreshed_at < REFRESH_EVERY_SECONDS or now - self.last_attempt < REFRESH_RETRY_SECONDS:
            return
        self.last_attempt = now
        try:
            response = await client.get(f"{GRAPH_HOST}/refresh_access_token",
                                        params={"grant_type": "ig_refresh_token", "access_token": token})
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("instagram_token_refresh_failed reason=network")
            return
        new_token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(new_token, str) or not new_token:
            logger.warning("instagram_token_refresh_failed status=%s", response.status_code)
            return
        self.write(secret, {"token": new_token, "source": fingerprint(config.token), "refreshed_at": now,
                            "expires_in": payload.get("expires_in")})
        logger.info("instagram_token_refreshed")


# --------------------------------------------------------------------------- Meta istemcisi ve önbellek

def meta_error(response: httpx.Response) -> InstagramError:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    code = error.get("code") if isinstance(error, dict) else None
    if code == 190 or response.status_code == 401:
        return InstagramError("invalid_token")
    if code in (4, 17, 32, 613) or response.status_code == 429:
        return InstagramError("rate_limited")
    if code == 100:
        return InstagramError("unsupported_field", field_error=True)
    return InstagramError(f"http_{response.status_code}")


@dataclass
class CachedPage:
    payload: dict[str, Any]
    fetched_at: float


class InstagramShowcase:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, token_path: Path | None = None,
                 clock=time.time):
        self.transport = transport
        self.clock = clock
        self.tokens = TokenStore(token_path)
        self.pages: OrderedDict[str, CachedPage] = OrderedDict()
        self.issued_cursors: set[str] = set()
        self.locks: dict[str, asyncio.Lock] = {}
        self.failed_at: dict[str, float] = {}
        self.profile_fields: str | None = None
        self.media_fields: str | None = None
        self.config_fingerprint = ""

    def reset_if_config_changed(self, config: InstagramConfig) -> None:
        current = fingerprint(f"{config.token}|{config.user_id}|{config.version}")
        if current != self.config_fingerprint:
            self.config_fingerprint = current
            self.pages.clear()
            self.issued_cursors.clear()
            self.failed_at.clear()
            self.profile_fields = self.media_fields = None

    async def call(self, client: httpx.AsyncClient, config: InstagramConfig, token: str, path: str,
                   field_sets: tuple[str, ...], chosen: str | None, extra: dict[str, Any] | None = None) -> tuple[Any, str]:
        candidates = (chosen,) if chosen else field_sets
        last: InstagramError | None = None
        for fields in candidates:
            try:
                response = await client.get(f"{GRAPH_HOST}/{config.version}/{path}",
                                            params={"fields": fields, "access_token": token, **(extra or {})})
            except httpx.TimeoutException:
                raise InstagramError("timeout") from None
            except httpx.HTTPError:
                raise InstagramError("network") from None
            if response.status_code == 200:
                try:
                    return response.json(), fields
                except ValueError:
                    raise InstagramError("invalid_json") from None
            last = meta_error(response)
            if not last.field_error:
                raise last
        raise last or InstagramError("unsupported_field")

    async def fetch_page(self, config: InstagramConfig, secret: bytes | None, after: str | None) -> dict[str, Any]:
        protect_logging()
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, transport=self.transport,
                                     follow_redirects=False) as client:
            await self.tokens.maybe_refresh(config, secret, client, self.clock())
            token, _ = self.tokens.current(config, secret)
            profile = None
            if after is None:
                raw_profile, self.profile_fields = await self.call(client, config, token, config.user_id,
                                                                   PROFILE_FIELD_SETS, self.profile_fields)
                profile = normalize_profile(raw_profile, config)
            owner = config.user_id
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if after:
                params["after"] = after
            raw_media, self.media_fields = await self.call(client, config, token, f"{owner}/media",
                                                           MEDIA_FIELD_SETS, self.media_fields, params)
        rows = raw_media.get("data") if isinstance(raw_media, dict) else None
        media = [item for item in (normalize_media(row) for row in (rows or [])[:PAGE_SIZE * 2]) if item]
        cursor = next_cursor(raw_media)
        if cursor:
            if len(self.issued_cursors) > 500:
                self.issued_cursors.clear()
            self.issued_cursors.add(cursor)
        return {"profile": profile, "media": media, "next": cursor}

    def remember(self, key: str, payload: dict[str, Any], now: float) -> None:
        self.pages[key] = CachedPage(payload, now)
        self.pages.move_to_end(key)
        while len(self.pages) > MAX_CACHED_PAGES:
            self.pages.popitem(last=False)

    async def feed(self, config: InstagramConfig, secret: bytes | None, after: str | None) -> dict[str, Any]:
        self.reset_if_config_changed(config)
        base = {"configured": config.configured, "profile_url": profile_url(config.username), "username": config.username}
        if not config.configured:
            return {**base, "available": False, "stale": False, "profile": None, "media": [], "next": None, "fetched_at": None}
        key = after or ""
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = self.clock()
            cached = self.pages.get(key)
            if cached and now - cached.fetched_at < CACHE_SECONDS:
                return self.response(base, cached, stale=False)
            recently_failed = now - self.failed_at.get(key, 0.0) < RETRY_AFTER_ERROR_SECONDS
            if not recently_failed:
                try:
                    payload = await self.fetch_page(config, secret, after)
                except InstagramError as error:
                    self.failed_at[key] = now
                    logger.warning("instagram_feed_unavailable reason=%s page=%s", error.code, "next" if after else "first")
                else:
                    self.failed_at.pop(key, None)
                    self.remember(key, payload, now)
                    return self.response(base, self.pages[key], stale=False)
            if cached and now - cached.fetched_at < STALE_SECONDS:
                return self.response(base, cached, stale=True)
            return {**base, "available": False, "stale": False, "profile": None, "media": [], "next": None, "fetched_at": None}

    @staticmethod
    def response(base: dict[str, Any], page: CachedPage, *, stale: bool) -> dict[str, Any]:
        return {**base, "available": True, "stale": stale, **page.payload,
                "fetched_at": datetime.fromtimestamp(page.fetched_at).astimezone().isoformat()}


def showcase(request: Request) -> InstagramShowcase:
    existing = getattr(request.app.state, "instagram_showcase", None)
    if existing is None:
        from .local_storage import DATA_DIR
        existing = InstagramShowcase(token_path=DATA_DIR / "instagram_token.enc")
        request.app.state.instagram_showcase = existing
    return existing


def app_secret(request: Request) -> bytes | None:
    commercial = getattr(request.app.state, "v22_commercial", None)
    secret = commercial.get("secret") if isinstance(commercial, dict) else None
    return secret if isinstance(secret, bytes) and secret else None


@router.get(FEED_PATH)
async def instagram_feed(request: Request, after: str | None = Query(default=None, max_length=512)) -> JSONResponse:
    service = showcase(request)
    if after is not None and (not CURSOR_PATTERN.fullmatch(after) or after not in service.issued_cursors):
        return JSONResponse({"detail": "Geçersiz sayfa."}, status_code=400)
    payload = await service.feed(load_config(), app_secret(request), after)
    # Tarayıcı ve CDN kısa süre önbelleğe alabilir; içerik yalnız herkese açık gönderilerdir.
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=120"})
