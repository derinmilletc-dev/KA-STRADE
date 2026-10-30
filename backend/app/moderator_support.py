"""Internal team cases. Legacy customer support endpoints are unchanged."""
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .audit_log import AuditAction, write_audit
from .moderator_access import ModeratorIdentity, require_permission
from .moderator_customers import CustomerSummary, customer_read_connection, customer_row, customer_summary
from . import support_attachments, support_thread
from .support_sync import sync_support_cases
from .v22_commercial import add_audit, now_iso, runtime, save_state

router = APIRouter(prefix="/api/mod/support", tags=["Internal support"])
CaseStatus = Literal["NEW", "OPEN", "WAITING", "RESOLVED", "CLOSED"]
Priority = Literal["LOW", "NORMAL", "HIGH"]
ID = r"^[A-Za-z0-9_-]+$"


def mask_support_text(text: str) -> str:
    text = re.sub(r"\b(?:sk|pk)_[A-Za-z0-9_-]+", "[GİZLİ]", text, flags=re.I)
    text = re.sub(r"\bBearer\s+[^\s<>\"']+", "Bearer [GİZLİ]", text, flags=re.I)
    text = re.sub(r"(?i)\b(?:api[_ -]?key|secret|token|password)\s*[:=]\s*[\"']?[^\s<>\"']+",
                  "[GİZLİ]", text)
    text = re.sub(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", "[GİZLİ]", text)
    return re.sub(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9_+/=-]{24,})(?![A-Za-z0-9])", "[GİZLİ]", text)


class ReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseItem(ReadModel):
    id: str = Field(pattern=ID, max_length=160)
    user_id: str = Field(pattern=ID, max_length=160)
    subject: str
    priority: Priority
    case_status: CaseStatus
    assignee_user_id: str | None = Field(default=None, pattern=ID, max_length=160)
    created_at: datetime
    version: int = Field(ge=1)
    assigned_to_me: bool
    # Canlı talepten: müşterinin dili ve son mesaj (liste satırı için; eski/silinmiş talepte boş).
    language: Literal["tr", "en"] | None = None
    last_author: Literal["CUSTOMER", "STAFF"] | None = None
    last_preview: str = ""
    last_at: datetime | None = None


class CasesPage(ReadModel):
    items: list[CaseItem]
    total: int
    limit: int
    offset: int


class NoteItem(ReadModel):
    id: str = Field(pattern=ID, max_length=160)
    author_user_id: str = Field(pattern=ID, max_length=160)
    body: str
    created_at: datetime


class ThreadItem(ReadModel):
    id: str = Field(pattern=ID, max_length=160)
    author: Literal["CUSTOMER", "STAFF"]
    body: str
    created_at: datetime | None = None


class AttachmentItem(ReadModel):
    id: str = Field(pattern=ID, max_length=160)
    name: str = Field(max_length=200)
    type: str = Field(max_length=100)
    size: int = Field(ge=0)


class CaseDetail(CaseItem):
    message: str
    legacy_status: Literal["OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED"]
    legacy_response_note: str
    notes: list[NoteItem]
    customer: CustomerSummary
    # Müşteriyle yazışma ve müşterinin eklediği dosyalar (canlı talepten okunur; anlık görüntü gecikmesi yok).
    thread: list[ThreadItem] = []
    attachments: list[AttachmentItem] = []


class Summary(ReadModel):
    open_cases: int | None = Field(default=None, ge=0)
    unassigned: int | None = Field(default=None, ge=0)
    assigned_to_me: int | None = Field(default=None, ge=0)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CaseStatus
    expected_version: int = Field(ge=1, strict=True)


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Note must not be blank")
        return value


class ReplyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=2, max_length=1000)
    resolve: bool = False

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("Reply must not be blank")
        return value.strip()


VISIBLE = """FROM support_cases c JOIN commercial_auth_users u ON u.user_id = c.user_id
    WHERE u.security->>'role' = 'CUSTOMER' AND c.user_id <> $1
      AND NOT EXISTS (SELECT 1 FROM commercial_erased_users e
        WHERE e.user_hash = encode(sha256(convert_to(c.user_id, 'UTF8')), 'hex'))"""
FILTERS = """AND ($2::text IS NULL OR c.case_status = $2
                 OR ($2 = 'OPEN' AND c.case_status = 'NEW'))
    AND ($3::text IS NULL OR ($3 = 'mine' AND c.assignee_user_id = $1)
         OR ($3 = 'unassigned' AND c.assignee_user_id IS NULL))
    AND ($4::text IS NULL OR c.priority = $4)"""


@asynccontextmanager
async def support_connection(request: Request, identity: ModeratorIdentity, permission: str):
    # Same durable 30/60s bucket as the stage 4 customer endpoints.
    async with customer_read_connection(request, identity, permission) as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(61010004)")
        yield conn


def case_item(row, identity: ModeratorIdentity, *, full_subject=False, ticket: dict | None = None) -> CaseItem:
    subject = mask_support_text(row["subject"])
    last = support_thread.last_message(ticket) if ticket is not None else None
    return CaseItem(
        id=row["id"], user_id=row["user_id"], subject=subject if full_subject else subject[:100],
        priority=row["priority"], case_status=row["case_status"],
        assignee_user_id=row["assignee_user_id"], created_at=row["created_at_legacy"],
        version=row["version"], assigned_to_me=row["assignee_user_id"] == identity.user_id,
        language=support_thread.ticket_language(ticket),
        last_author=last["author"] if last else None,
        last_preview=" ".join(mask_support_text(last["body"]).split())[:120] if last else "",
        last_at=last["at"] if last else None,
    )


async def visible_case(conn, identity: ModeratorIdentity, case_id: str):
    row = await conn.fetchrow(
        f"SELECT c.* {VISIBLE} AND c.id = $2 FOR UPDATE OF c", identity.user_id, case_id,
    )
    if row is None:
        raise HTTPException(404, "Support case not found")
    # Canonical target/customer DTO uses the same hidden-target and erasure rules.
    customer = customer_summary(await customer_row(conn, identity, row["user_id"]))
    return row, customer


def legacy_ticket(request: Request, row) -> dict | None:
    """Vakanın bağlı olduğu müşteri talebi (sunucu belleğindeki canlı kayıt); yoksa None."""
    tickets = runtime(request)["state"].get("support_tickets", [])
    return next((t for t in tickets if isinstance(t, dict) and t.get("id") == row["legacy_ticket_id"]
                 and t.get("user_id") == row["user_id"]), None)


def case_thread(ticket: dict | None) -> list[ThreadItem]:
    if ticket is None:
        return []
    return [ThreadItem(id=item["id"], author=item["author"], body=mask_support_text(item["body"]), created_at=item["created_at"])
            for item in support_thread.public_thread(ticket) if re.fullmatch(ID, item["id"] or "")]


def case_attachments(ticket: dict | None) -> list[AttachmentItem]:
    rows = (ticket or {}).get("attachments") or []
    return [AttachmentItem(id=str(item.get("id")), name=str(item.get("name") or "dosya")[:200],
                           type=str(item.get("type") or "")[:100], size=int(item.get("size") or 0))
            for item in rows if isinstance(item, dict) and re.fullmatch(ID, str(item.get("id") or ""))]


async def audit_case(conn, request: Request, case_id: str, action: AuditAction):
    await write_audit(conn, request.state.mod_read_actor, action, "SUPPORT_CASE", case_id, {}, {})


@router.get("/cases", response_model=CasesPage)
async def list_cases(
    request: Request,
    identity: ModeratorIdentity = Depends(require_permission("support.view")),
    limit: int = Query(25, ge=1, le=50), offset: int = Query(0, ge=0),
    status: CaseStatus | None = None,
    assignment: Literal["mine", "unassigned"] | None = None, priority: Priority | None = None,
):
    async with support_connection(request, identity, "support.view") as conn:
        await sync_support_cases(conn)
        args = (identity.user_id, status, assignment, priority)
        total = await conn.fetchval(f"SELECT COUNT(*) {VISIBLE} {FILTERS}", *args)
        rows = await conn.fetch(
            f"SELECT c.* {VISIBLE} {FILTERS} ORDER BY c.created_at_legacy DESC, c.id LIMIT $5 OFFSET $6",
            *args, limit, offset,
        )
        return CasesPage(items=[case_item(row, identity, ticket=legacy_ticket(request, row)) for row in rows],
                         total=total, limit=limit, offset=offset)


@router.get("/summary", response_model=Summary)
async def support_summary(
    request: Request, identity: ModeratorIdentity = Depends(require_permission("support.view")),
):
    try:
        async with support_connection(request, identity, "support.view") as conn:
            await sync_support_cases(conn)
            row = await conn.fetchrow(
                f"""SELECT COUNT(*) FILTER (WHERE c.case_status IN ('NEW','OPEN','WAITING')) AS open_cases,
                    COUNT(*) FILTER (WHERE c.assignee_user_id IS NULL AND c.case_status NOT IN ('RESOLVED','CLOSED')) AS unassigned,
                    COUNT(*) FILTER (WHERE c.assignee_user_id = $1 AND c.case_status NOT IN ('RESOLVED','CLOSED')) AS assigned_to_me
                    {VISIBLE}""", identity.user_id,
            )
            if row is None:
                raise RuntimeError("Support counts unavailable")
            return Summary(**dict(row))
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        # Explicit requirement: unavailable data is null, never a fabricated zero.
        return Summary()


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def case_detail(
    request: Request, case_id: str = Path(pattern=ID, min_length=1, max_length=160),
    identity: ModeratorIdentity = Depends(require_permission("support.view")),
):
    async with support_connection(request, identity, "support.view") as conn:
        row, customer = await visible_case(conn, identity, case_id)
        notes = await conn.fetch(
            "SELECT id, author_user_id, body, created_at FROM support_notes WHERE case_id = $1 ORDER BY created_at, id", case_id,
        )
        ticket = legacy_ticket(request, row)
        result = CaseDetail(
            **case_item(row, identity, full_subject=True, ticket=ticket).model_dump(), customer=customer,
            message=mask_support_text(row["message"]), legacy_status=row["legacy_status"],
            legacy_response_note=mask_support_text(row["legacy_response_note"]),
            notes=[NoteItem(**{**dict(note), "body": mask_support_text(note["body"])}) for note in notes],
            thread=case_thread(ticket), attachments=case_attachments(ticket),
        )
        await audit_case(conn, request, case_id, "support.case.viewed")
        return result


async def mutate_case(request: Request, identity: ModeratorIdentity, case_id: str, operation: str,
                      payload: StatusChange | NoteCreate | ReplyCreate | None = None):
    # Müşteri talebine (bellekteki kayıt) yansıtma yalnız veritabanı işlemi tamamlandıktan sonra yapılır:
    # yetki/sürüm çakışması, denetim kaydı ya da commit hatasında müşteri tarafı değişmez.
    after_commit = None
    async with support_connection(request, identity, "support.manage") as conn:
        row, _ = await visible_case(conn, identity, case_id)
        if operation == "take":
            changed = await conn.fetchrow(
                """UPDATE support_cases SET assignee_user_id = $2, version = version + 1, updated_at = clock_timestamp(),
                          legacy_status = CASE WHEN legacy_status = 'OPEN' THEN 'IN_PROGRESS' ELSE legacy_status END
                   WHERE id = $1 AND assignee_user_id IS NULL RETURNING *""", case_id, identity.user_id,
            )
            action: AuditAction = "support.case.taken"
            after_commit = ("take", None)
        else:
            if row["assignee_user_id"] != identity.user_id:
                raise HTTPException(409, "Support case assignment changed")
            if operation == "release":
                changed = await conn.fetchrow(
                    """UPDATE support_cases SET assignee_user_id = NULL, version = version + 1, updated_at = clock_timestamp()
                       WHERE id = $1 AND assignee_user_id = $2 RETURNING *""", case_id, identity.user_id,
                )
                action = "support.case.released"
            elif isinstance(payload, StatusChange):
                changed = await conn.fetchrow(
                    """UPDATE support_cases SET case_status = $3, version = version + 1, updated_at = clock_timestamp(),
                              legacy_status = $5
                       WHERE id = $1 AND assignee_user_id = $2 AND version = $4 RETURNING *""",
                    case_id, identity.user_id, payload.status, payload.expected_version,
                    support_thread.CASE_TO_LEGACY[payload.status],
                )
                action = "support.case.status_changed"
                after_commit = ("status", support_thread.CASE_TO_LEGACY[payload.status])
            elif isinstance(payload, NoteCreate):
                await conn.execute(
                    "INSERT INTO support_notes (id, case_id, author_user_id, body) VALUES ($1, $2, $3, $4)",
                    uuid.uuid4().hex, case_id, identity.user_id, payload.body,
                )
                changed = await conn.fetchrow(
                    """UPDATE support_cases SET version = version + 1, updated_at = clock_timestamp()
                       WHERE id = $1 AND assignee_user_id = $2 RETURNING *""", case_id, identity.user_id,
                )
                action = "support.note.added"
            elif isinstance(payload, ReplyCreate):
                # Müşterinin gördüğü yanıt: talebin yazışmasına eklenir, e-posta ve bildirim zili ile iletilir.
                ticket = legacy_ticket(request, row)
                if ticket is None:
                    raise HTTPException(404, "Support ticket not found")
                if len(support_thread.messages_of(ticket)) >= support_thread.MAX_MESSAGES:
                    raise HTTPException(409, "Support ticket message limit reached")
                legacy_status = "RESOLVED" if payload.resolve else "IN_PROGRESS"
                await conn.execute(
                    "INSERT INTO support_notes (id, case_id, author_user_id, body) VALUES ($1, $2, $3, $4)",
                    uuid.uuid4().hex, case_id, identity.user_id, "[Müşteriye yanıt] " + payload.body,
                )
                changed = await conn.fetchrow(
                    """UPDATE support_cases SET version = version + 1, updated_at = clock_timestamp(),
                              case_status = CASE WHEN $3 THEN 'RESOLVED' ELSE case_status END,
                              legacy_status = $4, legacy_response_note = $5
                       WHERE id = $1 AND assignee_user_id = $2 RETURNING *""",
                    case_id, identity.user_id, payload.resolve, legacy_status, payload.body,
                )
                action = "support.note.added"
                after_commit = ("reply", legacy_status)
            else:
                raise RuntimeError("Unsupported support operation")
        if changed is None:
            raise HTTPException(409, "Support case updated by another operator")
        result = case_item(changed, identity)
        await audit_case(conn, request, case_id, action)
    if after_commit is not None:
        await apply_to_ticket(request, identity, row, after_commit[0], after_commit[1],
                              payload.body if isinstance(payload, ReplyCreate) else None)
    return result


async def apply_to_ticket(request: Request, identity: ModeratorIdentity, row, kind: str,
                          legacy_status: str | None, reply: str | None) -> None:
    rt = runtime(request)
    async with rt["lock"]:
        ticket = legacy_ticket(request, row)
        if ticket is None:
            return  # talep anlık görüntüden düşmüş/silinmiş: yalnız vaka güncellenir
        if kind == "take":
            if ticket.get("status") != "OPEN":
                return
            ticket.update(status="IN_PROGRESS", updated_at=now_iso(), updated_by=identity.user_id)
            message = "Destek kaydı ekip tarafından üstlenildi."
        elif kind == "status":
            if ticket.get("status") == legacy_status:
                return
            ticket.update(status=legacy_status, updated_at=now_iso(), updated_by=identity.user_id)
            message = f"Destek kaydı {legacy_status} olarak güncellendi."
        else:
            sent = support_thread.append_message(ticket, support_thread.STAFF, reply or "", identity.user_id)
            ticket.update(status=legacy_status, updated_by=identity.user_id)
            message = "Destek kaydına moderatör yanıtı eklendi."
        add_audit(rt["state"], "SUPPORT_STATUS", message, actor=identity.user_id, subject=row["legacy_ticket_id"])
        save_state(rt["state"])
    if kind == "reply":
        support_thread.schedule_reply_email(request, ticket, sent)


@router.post("/cases/{case_id}/take", response_model=CaseItem)
async def take_case(request: Request, case_id: str = Path(pattern=ID, min_length=1, max_length=160),
                    identity: ModeratorIdentity = Depends(require_permission("support.manage"))):
    return await mutate_case(request, identity, case_id, "take")


@router.post("/cases/{case_id}/release", response_model=CaseItem)
async def release_case(request: Request, case_id: str = Path(pattern=ID, min_length=1, max_length=160),
                       identity: ModeratorIdentity = Depends(require_permission("support.manage"))):
    return await mutate_case(request, identity, case_id, "release")


@router.post("/cases/{case_id}/status", response_model=CaseItem)
async def status_case(payload: StatusChange, request: Request,
                      case_id: str = Path(pattern=ID, min_length=1, max_length=160),
                      identity: ModeratorIdentity = Depends(require_permission("support.manage"))):
    return await mutate_case(request, identity, case_id, "status", payload)


@router.post("/cases/{case_id}/notes", response_model=CaseItem)
async def note_case(payload: NoteCreate, request: Request,
                    case_id: str = Path(pattern=ID, min_length=1, max_length=160),
                    identity: ModeratorIdentity = Depends(require_permission("support.manage"))):
    return await mutate_case(request, identity, case_id, "notes", payload)


@router.post("/cases/{case_id}/reply", response_model=CaseItem)
async def reply_case(payload: ReplyCreate, request: Request,
                     case_id: str = Path(pattern=ID, min_length=1, max_length=160),
                     identity: ModeratorIdentity = Depends(require_permission("support.manage"))):
    return await mutate_case(request, identity, case_id, "reply", payload)


@router.get("/cases/{case_id}/attachments/{attachment_id}")
async def case_attachment(
    request: Request, case_id: str = Path(pattern=ID, min_length=1, max_length=160),
    attachment_id: str = Path(pattern=ID, min_length=1, max_length=160),
    identity: ModeratorIdentity = Depends(require_permission("support.view")),
):
    """Müşterinin talebe eklediği dosya (yalnız görünür vakalar; görüntüleme denetim kaydına yazılır)."""
    async with support_connection(request, identity, "support.view") as conn:
        row, _ = await visible_case(conn, identity, case_id)
        if not any(item.id == attachment_id for item in case_attachments(legacy_ticket(request, row))):
            raise HTTPException(404, "Attachment not found")
        record = await support_attachments.fetch(request.app.state.db_pool, row["legacy_ticket_id"], attachment_id)
        if record is None or record["user_id"] != row["user_id"]:
            raise HTTPException(404, "Attachment not found")
        await audit_case(conn, request, case_id, "support.case.viewed")
    quoted = record["filename"].encode("ascii", "ignore").decode() or "dosya"
    return Response(
        content=bytes(record["data"]), media_type=record["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{quoted}"', "X-Content-Type-Options": "nosniff",
                 "Cache-Control": "private, no-store"},
    )
