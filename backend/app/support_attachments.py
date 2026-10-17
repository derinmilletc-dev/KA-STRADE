"""Customer support attachments: small images and PDFs stored in PostgreSQL.

Files are bound to one ticket and its owner. The content type is decided from
the file signature, never from the client, and files are only ever served as a
download with ``nosniff``. Only the metadata is kept on the ticket itself.
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
import uuid
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_FILES_PER_TICKET = 3
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DAILY_UPLOADS = 15

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS support_attachments (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 2097152),
        data BYTEA NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS support_attachments_ticket_idx ON support_attachments (ticket_id)",
    "CREATE INDEX IF NOT EXISTS support_attachments_user_idx ON support_attachments (user_id, created_at)",
)


class AttachmentFile(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=4, max_length=3_000_000)


class AttachmentUpload(BaseModel):
    files: list[AttachmentFile] = Field(min_length=1, max_length=MAX_FILES_PER_TICKET)


def sniff_type(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "application/pdf": ".pdf"}


def clean_name(name: str, content_type: str) -> str:
    base = name.replace("\\", "/").split("/")[-1]
    base = "".join(ch for ch in unicodedata.normalize("NFKC", base) if ch.isprintable())
    base = re.sub(r"[\"<>:|?*]", "_", base).strip(" .")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = (stem or "dosya")[:60]
    return stem + EXTENSIONS[content_type]


def decode_files(payload: AttachmentUpload) -> list[tuple[str, str, bytes]]:
    out = []
    for item in payload.files:
        raw = item.data.split(",", 1)[1] if item.data.startswith("data:") and "," in item.data else item.data
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "Dosya okunamadı. Başka bir dosya dene.")
        if not data:
            raise HTTPException(422, "Dosya boş.")
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, "Dosya en fazla 2 MB olabilir.")
        kind = sniff_type(data)
        if kind is None:
            raise HTTPException(415, "Yalnızca PNG, JPG, WEBP ve PDF dosyaları eklenebilir.")
        out.append((clean_name(item.name, kind), kind, data))
    return out


async def ensure_schema(pool) -> None:
    if getattr(pool, "_support_attachments_ready", False):
        return
    async with pool.acquire() as conn:
        for statement in SCHEMA:
            await conn.execute(statement)
    try:
        pool._support_attachments_ready = True
    except AttributeError:
        pass


async def store(pool, user_id: str, ticket_id: str, files: list[tuple[str, str, bytes]]) -> list[dict]:
    await ensure_schema(pool)
    saved = []
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"support-att:{user_id}")
        existing = await conn.fetchval("SELECT COUNT(*) FROM support_attachments WHERE ticket_id = $1 AND user_id = $2", ticket_id, user_id)
        if existing + len(files) > MAX_FILES_PER_TICKET:
            raise HTTPException(422, "Bir talebe en fazla 3 dosya eklenebilir.")
        recent = await conn.fetchval(
            "SELECT COUNT(*) FROM support_attachments WHERE user_id = $1 AND created_at > NOW() - INTERVAL '24 hours'", user_id)
        if recent + len(files) > MAX_DAILY_UPLOADS:
            raise HTTPException(429, "Günlük dosya yükleme sınırına ulaştın. Yarın tekrar dene.")
        for name, kind, data in files:
            attachment_id = uuid.uuid4().hex
            await conn.execute(
                """INSERT INTO support_attachments (id, ticket_id, user_id, filename, content_type, size_bytes, data)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                attachment_id, ticket_id, user_id, name, kind, len(data), data,
            )
            saved.append({"id": attachment_id, "name": name, "type": kind, "size": len(data)})
    return saved


async def fetch(pool, ticket_id: str, attachment_id: str):
    await ensure_schema(pool)
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, user_id, filename, content_type, data FROM support_attachments WHERE id = $1 AND ticket_id = $2",
            attachment_id, ticket_id,
        )
