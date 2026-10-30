"""Destek talebi yazışması: müşteri ve ekip mesajları, okunma durumu ve bildirim zili.

Talep (legacy ticket) v22 ticari durumunda (tek süreç, bellekte) tutulur ve anlık görüntüyle kalıcı olur.
`messages` listesi eskiden yalnız `response_note` tutan taleplerle uyumludur: eski not, ilk ekip mesajı sayılır.
`response_note` her zaman son ekip yanıtıdır (eşitleme ve eski istemciler için korunur).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

CUSTOMER = "CUSTOMER"
STAFF = "STAFF"
MAX_MESSAGES = 100
BELL_PREFIX = "support:"

# Moderatör vaka durumu -> müşterinin gördüğü talep durumu (eşitleme yalnız bu dört değeri kabul eder).
CASE_TO_LEGACY = {"NEW": "OPEN", "OPEN": "OPEN", "WAITING": "IN_PROGRESS", "RESOLVED": "RESOLVED", "CLOSED": "CLOSED"}


def _now() -> str:
    from .v22_commercial import now_iso
    return now_iso()


def messages_of(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """Yazışmayı döndürür; eski (yalnız response_note olan) talepte notu ilk ekip mesajı sayar. Talebi değiştirmez."""
    rows = ticket.get("messages")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    note = str(ticket.get("response_note") or "").strip()
    if not note:
        return []
    return [{"id": "legacy-" + str(ticket.get("id") or ""), "author": STAFF, "body": note,
             "created_at": ticket.get("updated_at") or ticket.get("created_at"), "customer_read": True}]


def append_message(ticket: dict[str, Any], author: str, body: str, author_id: str) -> dict[str, Any]:
    if author not in (CUSTOMER, STAFF):
        raise ValueError("Unknown support author")
    rows = messages_of(ticket)
    if len(rows) >= MAX_MESSAGES:
        raise HTTPException(409, "Bu talepte mesaj sınırına ulaşıldı; lütfen yeni talep aç.")
    message = {"id": uuid.uuid4().hex, "author": author, "body": body, "created_at": _now(), "author_id": author_id}
    if author == STAFF:
        message["customer_read"] = False
        ticket["response_note"] = body[:1000]
    ticket["messages"] = [*rows, message]
    ticket["updated_at"] = message["created_at"]
    return message


def public_thread(ticket: dict[str, Any]) -> list[dict[str, Any]]:
    """Müşteriye gösterilen yazışma: ekip üyesinin kimliği gösterilmez."""
    return [{"id": str(row.get("id") or ""), "author": row.get("author"), "body": str(row.get("body") or ""),
             "created_at": row.get("created_at")} for row in messages_of(ticket)
            if row.get("author") in (CUSTOMER, STAFF)]


# Müşteriye dönen talep: ekip üyesinin kimliği (updated_by, author_id) ve ham mesaj listesi gönderilmez.
_CUSTOMER_HIDDEN = {"messages", "updated_by"}


def customer_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    return {**{key: value for key, value in ticket.items() if key not in _CUSTOMER_HIDDEN},
            "thread": public_thread(ticket), "unread_replies": unread_replies(ticket)}


def ticket_language(ticket: dict[str, Any] | None) -> str | None:
    value = (ticket or {}).get("language")
    return value if value in ("tr", "en") else None


def last_message(ticket: dict[str, Any]) -> dict[str, Any]:
    """Listede gösterilecek son mesaj: yazışma yoksa müşterinin ilk mesajı."""
    rows = [row for row in messages_of(ticket) if row.get("author") in (CUSTOMER, STAFF)]
    if rows:
        row = rows[-1]
        return {"author": row["author"], "body": str(row.get("body") or ""), "at": row.get("created_at")}
    return {"author": CUSTOMER, "body": str(ticket.get("message") or ""), "at": ticket.get("created_at")}


def unread_replies(ticket: dict[str, Any]) -> int:
    return sum(1 for row in messages_of(ticket) if row.get("author") == STAFF and row.get("customer_read") is False)


def mark_read(ticket: dict[str, Any], message_id: str | None = None) -> bool:
    changed = False
    for row in ticket.get("messages") or []:
        if isinstance(row, dict) and row.get("author") == STAFF and row.get("customer_read") is False \
                and (message_id is None or row.get("id") == message_id):
            row["customer_read"] = True
            changed = True
    return changed


def _bell_item(ticket: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    body = " ".join(str(row.get("body") or "").split())
    subject = " ".join(str(ticket.get("subject") or "").split())
    return {"id": BELL_PREFIX + str(row["id"]), "type": "SUPPORT_REPLY", "severity": "info",
            "title": "Destek ekibi yanıt verdi", "message": (f"{subject}: " if subject else "") + body[:160],
            "timestamp": row.get("created_at"), "read": row.get("customer_read") is not False, "target": "support"}


def merge_support_bell(request: Any, items: list[dict[str, Any]], *, read_id: str | None = None,
                       read_all: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    """Zil listesine bu kullanıcının destek yanıtlarını ekler; istenirse okundu işaretler."""
    from .v22_commercial import authenticated_user, runtime, save_state

    try:
        user = authenticated_user(request)
        rt = runtime(request)
    except (HTTPException, AttributeError, KeyError):
        # Ticari oturum yoksa (ör. yalnız demo uygulaması) destek bildirimi de yoktur.
        if read_id:
            raise HTTPException(404, "Bildirim bulunamadı.") from None
        return items
    tickets = [row for row in rt["state"].get("support_tickets", []) if isinstance(row, dict) and row.get("user_id") == user["id"]]
    found = False
    changed = False
    bell: list[dict[str, Any]] = []
    for ticket in tickets:
        for row in messages_of(ticket):
            if row.get("author") != STAFF:
                continue
            if "customer_read" not in row:  # eski not: zile düşmez, zaten okunmuş sayılır
                continue
            if read_id and BELL_PREFIX + str(row.get("id")) == read_id:
                found = True
                changed |= mark_read(ticket, str(row.get("id")))
            elif read_all:
                changed |= mark_read(ticket, str(row.get("id")))
            bell.append(_bell_item(ticket, row))
    if read_id and not found:
        raise HTTPException(404, "Bildirim bulunamadı.")
    if changed:
        save_state(rt["state"])
    return sorted([*items, *bell], key=lambda item: str(item.get("timestamp") or ""), reverse=True)[:limit]


async def refresh_moderator_view(request: Any) -> None:
    """Yeni talep/mesaj moderatör listesine beklemeden düşsün: anlık görüntüyü yaz, eşitleme önbelleğini boz.
    En iyi çaba: hata talebi geri almaz (15 sn'lik döngü ve 30 sn'lik eşitleme yine yakalar)."""
    from .v22_commercial import persist_v22_commercial

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return
    try:
        if await persist_v22_commercial(request.app):
            await pool.execute("UPDATE support_sync_state SET synced_at = to_timestamp(0) WHERE id = 1")
    except Exception as exc:
        logger.warning("Support moderator refresh failed type=%s", type(exc).__name__)


async def reopen_case(request: Any, ticket_id: str, user_id: str) -> None:
    """Müşteri yeni mesaj yazınca vaka yeniden açılır (NEW olan NEW kalır). En iyi çaba."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return
    try:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(61010004)")
            await conn.execute(
                """UPDATE support_cases SET case_status = CASE WHEN case_status = 'NEW' THEN 'NEW' ELSE 'OPEN' END,
                          legacy_status = 'OPEN', version = version + 1, updated_at = clock_timestamp()
                   WHERE legacy_ticket_id = $1 AND user_id = $2""", ticket_id, user_id,
            )
    except Exception as exc:
        logger.warning("Support case reopen failed type=%s", type(exc).__name__)


def schedule_reply_email(request: Any, ticket: dict[str, Any], message: dict[str, Any]) -> None:
    """Ekip yanıtını müşteriye e-postayla iletir. Arka planda, en iyi çaba: yanıtı asla bozmaz."""
    from .v22_commercial import runtime

    rt = runtime(request)
    user = next((row for row in rt["state"].get("users", []) if row.get("id") == ticket.get("user_id")), None)
    email = str((user or {}).get("email") or "").strip()
    if not email or (user or {}).get("active") is False:
        return
    subject = str(ticket.get("subject") or "")
    body = str(message.get("body") or "")
    key = "support-reply:" + str(message.get("id"))
    language = ticket_language(ticket) or "tr"

    async def deliver() -> None:
        from . import email_service as mail
        try:
            await asyncio.to_thread(mail.send_support_reply_email, to_email=email, ticket_subject=subject,
                                    reply=body, idempotency_key=key, language=language)
        except Exception as exc:  # gönderim hatası yanıtı etkilemez; e-posta adresi loglanmaz
            logger.warning("Support reply email failed type=%s", type(exc).__name__)

    try:
        task = asyncio.get_running_loop().create_task(deliver())
        _PENDING.add(task)
        task.add_done_callback(_PENDING.discard)
    except RuntimeError:
        pass


_PENDING: set[asyncio.Task] = set()
