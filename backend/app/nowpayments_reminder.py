"""Kripto üyeliği bitmeden 7 gün kala müşteriye tek hatırlatma e-postası (kripto otomatik yenilenmez)."""
import asyncio
import logging
import math
import os
from datetime import datetime, timezone
from html import escape

from asyncpg import PostgresError, UndefinedTableError

from . import email_layout as L
from . import email_service as mail
from . import nowpayments_payments as nowpay
from .binance_notifications import recipient_language
from .nowpayments_email import PLAN_NAMES, format_date, rows

logger = logging.getLogger("kaistrade.nowpayments")

REMIND_DAYS = 7
INTERVAL_SECONDS = 3600
DAY = 86400

TEXT = {
    "tr": {
        "subject": "KaisTrade · Üyeliğin {days} gün sonra bitiyor ⏰", "title": "Üyeliğin {days} gün sonra bitiyor",
        "intro": "Kesinti olmaması için üyeliğini şimdi yenileyebilirsin. Kripto ödemeleri otomatik yenilenmez, süre dolunca üyelik kendiliğinden kapanır.",
        "plan": "Paket", "until": "Üyelik bitişi", "button": "Üyeliği yenile",
        "note": "Süre dolmadan ödersen yeni 30 gün, kalan günlerinin üstüne eklenir; hiçbir gün kaybolmaz.",
        "help": "Yenilemek istemiyorsan bir şey yapman gerekmez. Sorun olursa bu e-postaya cevap yazabilirsin.",
    },
    "en": {
        "subject": "KaisTrade · Your membership ends in {days} days ⏰", "title": "Your membership ends in {days} days",
        "intro": "Renew now to avoid any interruption. Crypto payments do not renew automatically; your membership closes by itself when time runs out.",
        "plan": "Plan", "until": "Active until", "button": "Renew membership",
        "note": "If you pay before it ends, a new 30 days are added on top of your remaining days; no day is lost.",
        "help": "If you don't want to renew, you don't need to do anything. If anything looks wrong, just reply to this email.",
    },
}


def enabled() -> bool:
    return nowpay.enabled() and os.getenv("NOWPAYMENTS_REMINDER_ENABLED", "true").strip().lower() not in {"false", "0", "no"}


def days_left(period_end: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return max(1, math.ceil((period_end - now).total_seconds() / DAY))


def reminder_content(*, language: str, plan: str, period_end: datetime, link: str,
                     now: datetime | None = None) -> tuple[str, str, str]:
    language = "en" if language == "en" else "tr"
    words = TEXT[language]
    days = days_left(period_end, now)
    subject, title = words["subject"].format(days=days), words["title"].format(days=days)
    details = [(words["plan"], PLAN_NAMES.get(plan, plan)), (words["until"], format_date(period_end, language))]
    text = "\n".join(["KaisTrade", "", title, "", words["intro"], "", *[f"{k}: {v}" for k, v in details], "",
                      f"{words['button']}: {link}", "", words["note"], words["help"]])
    html = L.shell(lang=language, title=escape(title), body=(
        L.heading(escape(title), level=2) + L.para(escape(words["intro"])) + rows(details)
        + L.button(escape(link, quote=True), escape(words["button"]))
        + L.para(escape(words["note"]), muted=True, size=13) + L.para(escape(words["help"]), muted=True, size=13)))
    return subject, text, html


DUE_SQL = """
SELECT p.payment_id,p.user_id,p.period_end,r.plan,u.security->>'email' AS email
FROM nowpayments_periods p
JOIN nowpayments_payments r ON r.id=p.payment_id
JOIN commercial_auth_users u ON u.user_id=p.user_id
WHERE r.status='finished' AND r.erased_at IS NULL AND p.revoked_at IS NULL
  AND p.period_end > clock_timestamp() AND p.period_end <= clock_timestamp() + ($1 * INTERVAL '1 day')
  AND NOT EXISTS (SELECT 1 FROM nowpayments_periods later
                  WHERE later.user_id=p.user_id AND later.revoked_at IS NULL AND later.period_end > p.period_end)
  AND NOT EXISTS (SELECT 1 FROM nowpayments_reminders m WHERE m.payment_id=p.payment_id)
  AND u.security->'active'='true'::jsonb AND u.security->'email_verified'='true'::jsonb
ORDER BY p.period_end LIMIT 50"""


async def send_due(conn) -> int:
    """Süresi yaklaşan üyelere hatırlatma gönderir; gönderilen sayısını döner. Hata ödemeyi/üyeliği bozmaz."""
    sent = 0
    try:
        due = await conn.fetch(DUE_SQL, REMIND_DAYS)
    except UndefinedTableError:
        logger.warning("NOWPayments reminder table is not installed; skipping")
        return 0
    base = mail.validate_app_base_url()
    link = base + "/billing"
    mail.validate_action_url(link)
    for row in due:
        if not row["email"]:
            continue
        claimed = await conn.execute(
            "INSERT INTO nowpayments_reminders(payment_id) VALUES ($1) ON CONFLICT DO NOTHING", row["payment_id"])
        if claimed != "INSERT 0 1":
            continue
        try:
            language = await recipient_language(conn, row["user_id"])
            subject, text, html = reminder_content(language=language, plan=row["plan"], period_end=row["period_end"], link=link)
            await asyncio.to_thread(mail.send_email_message, to_email=row["email"], subject=subject, text=text, html=html,
                                    idempotency_key=row["payment_id"], idempotency_prefix="nowpayments-remind-")
            sent += 1
        except Exception as exc:  # noqa: BLE001 -- gönderilemezse kayıt silinir, sonraki turda yeniden denenir
            logger.warning("NOWPayments reminder email failed type=%s", type(exc).__name__)
            await conn.execute("DELETE FROM nowpayments_reminders WHERE payment_id=$1", row["payment_id"])
    return sent


async def reminder_loop(application, *, interval: int = INTERVAL_SECONDS, iterations: int | None = None) -> None:
    done = 0
    while iterations is None or done < iterations:
        try:
            pool = getattr(application.state, "db_pool", None)
            if enabled() and pool is not None:
                async with pool.acquire() as conn:
                    count = await send_due(conn)
                if count:
                    logger.info("NOWPayments renewal reminders sent=%s", count)
        except asyncio.CancelledError:
            raise
        except (PostgresError, OSError, RuntimeError, ValueError) as exc:
            logger.warning("NOWPayments reminder iteration failed type=%s", type(exc).__name__)
        done += 1
        if iterations is not None and done >= iterations:
            return
        await asyncio.sleep(interval)
