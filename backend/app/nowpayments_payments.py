"""NOWPayments (kripto) ile otomatik ödeme: fatura, imzalı bildirim doğrulama ve 30 günlük dönem.

Binance elle ödeme tablolarına dokunmaz. Para yalnız NOWPayments'tan İMZALI ve tutarı eşleşen bir
'finished' bildirimi gelince üyelik açar; aynı bildirim tekrar gelse bile dönem bir kez verilir.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import httpx
from asyncpg import PostgresError, UndefinedTableError
from fastapi import HTTPException

from . import binance_manual_payments as payments

logger = logging.getLogger(__name__)

PRODUCTION_API = "https://api.nowpayments.io/v1"
SANDBOX_API = "https://api-sandbox.nowpayments.io/v1"
DEFAULT_IPN_URL = "https://api.kaistrade.com/api/v22/payments/nowpayments/ipn"
INVOICE_REUSE_HOURS = 24
PROVIDER_STATUSES = frozenset({
    "waiting", "confirming", "confirmed", "sending", "partially_paid", "finished", "failed", "refunded", "expired",
})
OPEN_STATUSES = ("created", "waiting", "confirming", "confirmed", "sending")
MAX_IPN_BYTES = 64 * 1024


# ---------------------------------------------------------------- ayarlar

def api_key() -> str:
    return os.getenv("NOWPAYMENTS_API_KEY", "").strip()


def ipn_secret() -> str:
    return os.getenv("NOWPAYMENTS_IPN_SECRET", "").strip()


def enabled() -> bool:
    """Anahtarlar tanımlı değilse yöntem kapalıdır (arayüzde görünmez, bildirim kabul edilmez)."""
    return bool(api_key() and ipn_secret())


def api_base() -> str:
    custom = os.getenv("NOWPAYMENTS_API_BASE", "").strip().rstrip("/")
    if custom:
        if not custom.startswith("https://"):
            raise HTTPException(503, "Kripto ödeme ayarı geçersiz.")
        return custom
    sandbox = os.getenv("NOWPAYMENTS_SANDBOX", "false").strip().lower() in {"1", "true"}
    return SANDBOX_API if sandbox else PRODUCTION_API


def ipn_url() -> str:
    return os.getenv("NOWPAYMENTS_IPN_URL", "").strip() or DEFAULT_IPN_URL


def site_url() -> str:
    value = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    return value if value.startswith("https://") else "https://kaistrade.com"


# ---------------------------------------------------------------- imza

class _Number(str):
    """JSON sayısını yazıldığı haliyle (119.90 gibi) korur."""


def _dump_sorted(value: Any) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + _dump_sorted(value[key])
                              for key in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_dump_sorted(item) for item in value) + "]"
    if isinstance(value, _Number):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _reject_constant(name: str) -> Any:
    raise ValueError(f"JSON constant {name} is not allowed")


def signature_candidates(raw: bytes) -> list[str]:
    """İmzalanan metin: anahtarları alfabetik sıralı, boşluksuz JSON. Sayı yazımı iki biçimde denenir."""
    text = raw.decode("utf-8")
    literal = json.loads(text, parse_float=_Number, parse_int=_Number, parse_constant=_reject_constant)
    python = json.loads(text, parse_constant=_reject_constant)
    return [
        _dump_sorted(literal),
        json.dumps(python, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
    ]


def verify_signature(raw: bytes, header: str | None, secret: str) -> bool:
    if not secret or not header:
        return False
    received = header.strip().lower()
    try:
        candidates = signature_candidates(raw)
    except (ValueError, UnicodeDecodeError):
        return False
    for body in candidates:
        expected = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha512).hexdigest()
        if hmac.compare_digest(expected, received):
            return True
    return False


# ---------------------------------------------------------------- bildirim kararı (saf mantık)

@dataclass(frozen=True)
class Decision:
    action: str          # grant | update | duplicate | mismatch | ignore
    status: str
    note: str | None = None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def evaluate(row: Mapping[str, Any], data: Mapping[str, Any]) -> Decision:
    """Kayıtlı ödeme satırı ve (imzası doğrulanmış) bildirimden ne yapılacağına karar verir."""
    provider = str(data.get("payment_status") or "").strip().lower()
    if provider not in PROVIDER_STATUSES:
        return Decision("ignore", row["status"], "bilinmeyen ödeme durumu")
    if row["status"] == "finished":
        return Decision("duplicate", "finished")
    price = _decimal(data.get("price_amount"))
    expected = _decimal(row["price_amount"])
    if price is None or expected is None or price != expected \
            or str(data.get("price_currency") or "").strip().lower() != "usd":
        return Decision("mismatch", "mismatch", "bildirimdeki tutar veya para birimi taleple eşleşmiyor")
    if provider == "finished":
        paid, needed = _decimal(data.get("actually_paid")), _decimal(data.get("pay_amount"))
        if paid is not None and needed is not None and paid < needed:
            return Decision("mismatch", "partially_paid", "ödenen miktar istenen miktardan az")
        return Decision("grant", "finished")
    return Decision("update", provider)


# ---------------------------------------------------------------- fatura

def safe_invoice_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 500:
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "nowpayments.io" or host.endswith(".nowpayments.io")):
        return None
    return value


async def request_invoice(order_id: str, plan: str, amount: Decimal) -> tuple[str, str]:
    """NOWPayments'ta fatura açar; (invoice_id, invoice_url) döner. Ağ hatası kullanıcıya sade mesajla çıkar."""
    body = {
        "price_amount": float(amount),
        "price_currency": "usd",
        "order_id": order_id,
        "order_description": f"KaisTrade {payments.PLAN_NAMES[plan]} - 30 gün",
        "ipn_callback_url": ipn_url(),
        "success_url": site_url() + "/",
        "cancel_url": site_url() + "/",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{api_base()}/invoice", json=body,
                                         headers={"x-api-key": api_key(), "Content-Type": "application/json"})
        response.raise_for_status()
        data = response.json()
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("NOWPayments invoice request failed type=%s", type(exc).__name__)
        raise HTTPException(502, "Kripto ödeme sayfası şu anda açılamadı; biraz sonra tekrar deneyin.") from None
    url = safe_invoice_url(data.get("invoice_url") if isinstance(data, dict) else None)
    invoice_id = str(data.get("id") or "") if isinstance(data, dict) else ""
    if url is None or not invoice_id or len(invoice_id) > 128:
        logger.warning("NOWPayments invoice response failed validation")
        raise HTTPException(502, "Kripto ödeme sayfası doğrulanamadı; biraz sonra tekrar deneyin.")
    return invoice_id, url


async def create_invoice(conn: payments.PaymentConnection, user_id: str, plan: str) -> dict[str, Any]:
    amount = payments.PLAN_AMOUNTS.get(plan)
    if amount is None:
        raise HTTPException(422, "Bilinmeyen plan.")
    reuse: Mapping[str, Any] | None = None
    order_id = uuid.uuid4().hex
    async with payments.storage(conn):
        await payments.lock_user(conn, user_id)
        await payments.ensure_no_paid_stripe(conn, user_id)
        reuse = await conn.fetchrow(
            """SELECT id,plan,price_amount,invoice_url,status FROM nowpayments_payments
               WHERE user_id=$1 AND plan=$2 AND status = ANY($3::text[]) AND erased_at IS NULL
                 AND invoice_url IS NOT NULL AND created_at > clock_timestamp() - ($4 * INTERVAL '1 hour')
               ORDER BY created_at DESC LIMIT 1""", user_id, plan, list(OPEN_STATUSES), INVOICE_REUSE_HOURS,
        )
        if reuse is None:
            await conn.execute(
                "INSERT INTO nowpayments_payments (id,user_id,plan,price_amount) VALUES ($1,$2,$3,$4)",
                order_id, user_id, plan, amount,
            )
    if reuse is not None:
        return {"payment_id": reuse["id"], "plan": reuse["plan"], "amount": str(reuse["price_amount"]),
                "invoice_url": reuse["invoice_url"], "reused": True}
    # Ağ çağrısı veritabanı kilidinin dışında yapılır.
    invoice_id, url = await request_invoice(order_id, plan, amount)
    async with payments.storage(conn):
        await conn.execute(
            """UPDATE nowpayments_payments SET invoice_id=$2,invoice_url=$3,status='waiting',updated_at=clock_timestamp()
               WHERE id=$1 AND status='created'""", order_id, invoice_id, url,
        )
    return {"payment_id": order_id, "plan": plan, "amount": str(amount), "invoice_url": url, "reused": False}


async def current_payment(conn: payments.PaymentConnection, user_id: str) -> Mapping[str, Any] | None:
    async with payments.storage(conn):
        return await conn.fetchrow(
            """SELECT id,plan,price_amount,status,created_at,completed_at FROM nowpayments_payments
               WHERE user_id=$1 AND erased_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 1""", user_id,
        )


# ---------------------------------------------------------------- bildirimi işleme ve dönem verme

async def _next_period(conn: payments.PaymentConnection, user_id: str) -> tuple[datetime, datetime]:
    """Binance elle onaydaki kuralın aynısı: dönem, deneme/önceki ödeme bitiminden sonra başlar."""
    trial_end = await conn.fetchval(
        """SELECT MAX(COALESCE(trial_end,current_period_end)) FROM subscriptions WHERE user_id=$1
           AND plan='TRIAL' AND status='TRIALING' AND COALESCE(trial_end,current_period_end) > clock_timestamp()""",
        user_id,
    )
    from .free_trial import enabled as trial_enabled
    if trial_enabled():
        free_end = await conn.fetchval(
            "SELECT ends_at FROM member_free_trials WHERE user_id=$1 AND revoked_at IS NULL AND ends_at>clock_timestamp()",
            user_id,
        )
        if free_end is not None:
            trial_end = max(trial_end, free_end) if trial_end is not None else free_end
    ends = []
    for query in (
        """SELECT MAX(p.period_end) FROM binance_manual_periods p
           JOIN binance_manual_payment_requests r ON r.id=p.payment_request_id
           WHERE p.user_id=$1 AND r.user_id=$1 AND r.status='approved' AND r.erased_at IS NULL AND p.revoked_at IS NULL""",
        """SELECT MAX(p.period_end) FROM nowpayments_periods p
           JOIN nowpayments_payments r ON r.id=p.payment_id
           WHERE p.user_id=$1 AND r.user_id=$1 AND r.status='finished' AND r.erased_at IS NULL AND p.revoked_at IS NULL""",
    ):
        value = await conn.fetchval(query, user_id)
        if value is not None:
            ends.append(value)
    now = await conn.fetchval("SELECT clock_timestamp()")
    return payments.payment_period(now, trial_end=trial_end, manual_end=max(ends) if ends else None)


async def process_notification(conn: payments.PaymentConnection, data: Mapping[str, Any]) -> tuple[str, str | None]:
    """İmzası doğrulanmış bildirimi tek işlemde uygular. (sonuç, üyelik açılan user_id) döner."""
    order_id = str(data.get("order_id") or "")
    if len(order_id) != 32 or any(char not in "0123456789abcdef" for char in order_id):
        return "unknown_order", None
    async with payments.storage(conn):
        row = await conn.fetchrow("SELECT * FROM nowpayments_payments WHERE id=$1 FOR UPDATE", order_id)
        if row is None or row["erased_at"] is not None:
            return "unknown_order", None
        decision = evaluate(row, data)
        payment_id = str(data.get("payment_id") or "")[:64] or None
        pay_currency = str(data.get("pay_currency") or "")[:32] or None
        if decision.action in {"duplicate", "ignore"}:
            return decision.action, None
        if decision.action in {"update", "mismatch"}:
            await conn.execute(
                """UPDATE nowpayments_payments SET status=$2,note=$3,last_payment_id=COALESCE($4,last_payment_id),
                     pay_currency=COALESCE($5,pay_currency),updated_at=clock_timestamp() WHERE id=$1""",
                order_id, decision.status, decision.note, payment_id, pay_currency,
            )
            if decision.action == "mismatch":
                logger.warning("NOWPayments notification mismatch order=%s", order_id)
            return decision.action, None
        # grant
        try:
            await payments.lock_user(conn, row["user_id"])
        except HTTPException:
            await conn.execute(
                """UPDATE nowpayments_payments SET status='mismatch',note='kullanıcı hesabı doğrulanamadı; elle incelenmeli',
                     last_payment_id=COALESCE($2,last_payment_id),updated_at=clock_timestamp() WHERE id=$1""",
                order_id, payment_id,
            )
            logger.warning("NOWPayments payment received for unavailable account order=%s", order_id)
            return "needs_review", None
        start, end = await _next_period(conn, row["user_id"])
        inserted = await conn.execute(
            """INSERT INTO nowpayments_periods (payment_id,user_id,period_start,period_end) VALUES ($1,$2,$3,$4)
               ON CONFLICT (payment_id) DO NOTHING""", order_id, row["user_id"], start, end,
        )
        await conn.execute(
            """UPDATE nowpayments_payments SET status='finished',note=NULL,completed_at=clock_timestamp(),
                 last_payment_id=COALESCE($2,last_payment_id),pay_currency=COALESCE($3,pay_currency),
                 updated_at=clock_timestamp() WHERE id=$1""", order_id, payment_id, pay_currency,
        )
        return ("granted" if inserted == "INSERT 0 1" else "duplicate"), row["user_id"]


# ---------------------------------------------------------------- üyelik (member_entitlements kullanır)

async def paid_until(conn: Any, user_id: str) -> datetime | None:
    """Ödenmiş son bitiş tarihi (erken yenilemeyle eklenen dönemler dahil); yoksa None."""
    try:
        return await conn.fetchval(
            """SELECT MAX(p.period_end) FROM nowpayments_periods p
               JOIN nowpayments_payments r ON r.id=p.payment_id
               WHERE p.user_id=$1 AND r.user_id=$1 AND r.status='finished' AND r.erased_at IS NULL
                 AND p.revoked_at IS NULL AND p.period_end > clock_timestamp()""", user_id,
        )
    except (UndefinedTableError, PostgresError, OSError, RuntimeError):
        return None


async def active_period(conn: Any, user_id: str) -> Mapping[str, Any] | None:
    """Şu an geçerli NOWPayments dönemi; tablo henüz kurulmadıysa None (Binance akışı etkilenmez)."""
    try:
        return await conn.fetchrow(
            """SELECT p.payment_id,p.user_id,p.period_start,p.period_end,r.plan,r.status,r.user_id AS payment_user_id
               FROM nowpayments_periods p
               JOIN nowpayments_payments r ON r.id=p.payment_id
               JOIN commercial_auth_users u ON u.user_id=p.user_id
               WHERE p.user_id=$1 AND r.user_id=$1 AND r.status='finished' AND r.erased_at IS NULL
                 AND p.revoked_at IS NULL AND p.period_start <= clock_timestamp() AND p.period_end > clock_timestamp()
                 AND u.security->'active'='true'::jsonb AND u.security->'email_verified'='true'::jsonb
               ORDER BY p.period_end DESC,p.payment_id LIMIT 1""", user_id,
        )
    except UndefinedTableError:
        logger.warning("NOWPayments tables are not installed; skipping entitlement lookup")
        return None
    except (PostgresError, OSError, RuntimeError) as exc:
        logger.warning("NOWPayments entitlement lookup failed type=%s", type(exc).__name__)
        raise HTTPException(503, "Kripto üyelik kaydı doğrulanamadı.") from None
