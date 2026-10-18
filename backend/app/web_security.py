"""Small, deployment-safe owner gate for the first ProTreBot web preview.

This is intentionally separate from the commercial customer/session system.
It protects the entire API while the project is being deployed and tested by
its owner.  A later multi-tenant release can replace it with per-user scopes.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


MIN_ACCESS_TOKEN_LENGTH = 24
PUBLIC_PATHS = frozenset({"/healthz", "/api/health", "/api/health/database", "/api/web/access/logout", "/announcements/unsubscribe"})
OWNER_REQUIRED_PATHS = frozenset({"/api/web/access/check"})
CUSTOMER_SESSION_PATH_PREFIXES = ("/api/v22", "/api/v24", "/api/v25")
OWNER_GATE_PATH_PREFIXES = (
    "/api/web/access/check",
)
LOCAL_BOOTSTRAP_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


_CLOUDFLARE_NETWORKS = tuple(ipaddress.ip_network(item) for item in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
))


def _parse_ip(value: str | None):
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def _is_proxy_address(address) -> bool:
    return bool(
        address.is_private or address.is_loopback or address.is_link_local
        or any(address in network for network in _CLOUDFLARE_NETWORKS if network.version == address.version)
    )


def client_ip(request) -> str:
    """Real visitor address behind Render/Cloudflare.

    X-Forwarded-For is read only when the direct peer is itself a proxy (private
    or Cloudflare).  The list is scanned from the right and proxy addresses are
    skipped, so values a visitor writes on the left can never be chosen.
    """
    peer = request.client.host if getattr(request, "client", None) else ""
    peer_address = _parse_ip(peer)
    if peer_address is None or not _is_proxy_address(peer_address):
        return peer or "unknown"
    forwarded = str(request.headers.get("x-forwarded-for", ""))
    for item in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        address = _parse_ip(item)
        if address is None:
            return peer
        if not _is_proxy_address(address):
            return str(address)
    return peer


def env_flag(name: str, *, default: bool = False) -> bool:
    value = str(os.getenv(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "enabled"}


def production_or_hosted_environment() -> bool:
    production = any(os.getenv(name, "").strip().lower() in {"production", "prod"} for name in (
        "PROTREBOT_ENVIRONMENT", "ENVIRONMENT", "APP_ENV", "NODE_ENV", "VERCEL_ENV", "RAILWAY_ENVIRONMENT_NAME",
    ))
    hosted = env_flag("RENDER") or env_flag("VERCEL") or bool(os.getenv("DYNO"))
    return production or hosted


def validate_auth_security_configuration(*, expose_dev_tokens: bool | None = None) -> None:
    exposed = env_flag("PROTREBOT_EXPOSE_DEV_TOKENS") if expose_dev_tokens is None else expose_dev_tokens
    if not exposed:
        return
    mode = os.getenv("PROTREBOT_ENVIRONMENT", "").strip().lower()
    if production_or_hosted_environment() or mode not in {"development", "test"}:
        raise RuntimeError(
            "PROTREBOT_EXPOSE_DEV_TOKENS is forbidden outside explicit, unhosted development/test environments"
        )


def cors_origins(value: str | None, *, fallback: list[str] | None = None) -> list[str]:
    raw_value = str(value or "").strip()
    if raw_value.startswith("[") and raw_value.endswith("]"):
        try:
            parsed_value = json.loads(raw_value)
            raw_items = parsed_value if isinstance(parsed_value, list) else [raw_value]
        except json.JSONDecodeError:
            raw_items = [raw_value]
    else:
        raw_items = raw_value.split(",")
    configured = []
    for item in raw_items:
        origin = str(item).strip().strip('"\'').rstrip("/")
        if not origin or origin == "*":
            continue
        try:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                continue
            parsed.port
        except ValueError:
            continue
        configured.append(origin)
    configured = list(dict.fromkeys(configured))
    return configured or (fallback if fallback is not None else ["http://localhost:5173", "http://127.0.0.1:5173"])


def is_allowed_cors_origin(origin: str | None, configured_origins: set[str] | list[str] | tuple[str, ...]) -> bool:
    normalized = str(origin or "").strip().rstrip("/")
    return normalized in configured_origins


def bearer_token(value: str | None) -> str:
    header = str(value or "").strip()
    if not header.lower().startswith("bearer "):
        return ""
    return header.split(" ", 1)[1].strip()


def bootstrap_access_allowed(client_host: str | None, *, web_owner_authenticated: bool) -> bool:
    """Allow first-owner creation only from the local application."""
    host = str(client_host or "").strip().lower()
    return host in LOCAL_BOOTSTRAP_HOSTS


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    status_code: int = 200
    detail: str = "OK"


def evaluate_access(
    *,
    required: bool,
    configured_token: str,
    authorization: str | None,
    path: str,
    method: str,
    owner_access: str | None = None,
) -> AccessDecision:
    """Return a deterministic decision without logging either secret."""
    if not required or method.upper() == "OPTIONS" or path in PUBLIC_PATHS:
        return AccessDecision(True)

    if any(path.startswith(prefix) for prefix in CUSTOMER_SESSION_PATH_PREFIXES):
        return AccessDecision(True)

    if len(configured_token) < MIN_ACCESS_TOKEN_LENGTH:
        return AccessDecision(False, 503, "Web erişim kilidi sunucuda tamamlanmamış.")

    provided_owner = str(owner_access or "").strip()
    if not any(path.startswith(prefix) for prefix in OWNER_GATE_PATH_PREFIXES):
        if bearer_token(authorization):
            return AccessDecision(True)
        if provided_owner and hmac.compare_digest(provided_owner, configured_token):
            return AccessDecision(True)
    provided = provided_owner or bearer_token(authorization)
    if not provided:
        return AccessDecision(False, 401, "Yönetici erişim kodu gerekli.")
    if not hmac.compare_digest(provided, configured_token):
        return AccessDecision(False, 401, "Yönetici erişim kodu geçersiz.")
    return AccessDecision(True)
