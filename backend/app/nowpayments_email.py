"""Kripto ödemesi alınınca müşteriye giden e-posta (ilk ödeme ve erken yenileme). Dil: hesap dili (tr | en)."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from . import email_layout as L
from . import email_service as mail
from .binance_notifications import recipient_language

logger = logging.getLogger("kaistrade.nowpayments")

PLAN_NAMES = {"SMART_TRADE": "Smart Trade", "MASTER_MODE": "Master Mode"}
MONTHS = {
    "tr": ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}
TEXT = {
    "tr": {
        "subject_new": "KaisTrade · Ödemen alındı, üyeliğin açıldı ✅", "subject_renew": "KaisTrade · Üyeliğin uzatıldı ✅",
        "title_new": "Ödemen alındı 🎉", "title_renew": "Üyeliğin uzatıldı 🎉",
        "intro_new": "Teşekkürler! Kripto ödemen ağda onaylandı ve üyeliğin otomatik olarak açıldı.",
        "intro_renew": "Teşekkürler! Kripto ödemen ağda onaylandı. Yeni 30 gün, kalan günlerinin üstüne eklendi.",
        "plan": "Paket", "amount": "Tutar", "until": "Üyelik bitişi", "button": "Panele git",
        "note": "Üyeliğin 30 gün sürer. Bitmeden önce aynı sayfadan tekrar ödeyerek uzatabilirsin; kalan günlerin kaybolmaz.",
        "help": "Bir sorun olursa bu e-postaya cevap yazman yeterli, yardımcı oluruz.",
    },
    "en": {
        "subject_new": "KaisTrade · Payment received, your membership is active ✅", "subject_renew": "KaisTrade · Your membership was extended ✅",
        "title_new": "Payment received 🎉", "title_renew": "Membership extended 🎉",
        "intro_new": "Thank you! Your crypto payment is confirmed on the network and your membership has been activated automatically.",
        "intro_renew": "Thank you! Your crypto payment is confirmed. A new 30 days were added on top of your remaining days.",
        "plan": "Plan", "amount": "Amount", "until": "Active until", "button": "Go to panel",
        "note": "Your membership lasts 30 days. You can extend it before it ends by paying again from the same page; your remaining days are kept.",
        "help": "If anything looks wrong, just reply to this email and we'll help.",
    },
}


def format_date(value: datetime, language: str) -> str:
    value = value.astimezone(timezone.utc)
    if language == "en":
        return f"{value.day} {MONTHS['en'][value.month - 1]} {value.year}"
    return f"{value.day} {MONTHS['tr'][value.month - 1]} {value.year}"


def format_amount(amount, language: str) -> str:
    text = f"{amount:.2f}"
    return f"{text.replace('.', ',') if language == 'tr' else text} USD"


def is_renewal(period_start: datetime, now: datetime | None = None) -> bool:
    """Dönem şimdiden sonra başlıyorsa mevcut üyeliğin üstüne eklenmiştir (erken yenileme)."""
    return period_start > (now or datetime.now(timezone.utc)) + timedelta(minutes=5)


def rows(items: list[tuple[str, str]]) -> str:
    cells = "".join(
        f'<tr><td style="padding:9px 0;border-bottom:1px solid {L.LINE};color:{L.MUTE};font-size:13px">{escape(label)}</td>'
        f'<td align="right" style="padding:9px 0;border-bottom:1px solid {L.LINE};color:{L.HEAD};font-size:14px;font-weight:700">{escape(value)}</td></tr>'
        for label, value in items)
    return f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 6px">{cells}</table>'


def email_content(*, language: str, plan: str, amount, period_start: datetime, period_end: datetime, link: str,
                  now: datetime | None = None) -> tuple[str, str, str]:
    language = "en" if language == "en" else "tr"
    words = TEXT[language]
    kind = "renew" if is_renewal(period_start, now) else "new"
    title, intro, subject = words["title_" + kind], words["intro_" + kind], words["subject_" + kind]
    details = [(words["plan"], PLAN_NAMES.get(plan, plan)), (words["amount"], format_amount(amount, language)),
               (words["until"], format_date(period_end, language))]
    text = "\n".join(["KaisTrade", "", title, "", intro, "", *[f"{label}: {value}" for label, value in details], "",
                      f"{words['button']}: {link}", "", words["note"], words["help"]])
    html = L.shell(lang=language, title=escape(title), body=(
        L.heading(escape(title), level=2) + L.para(escape(intro)) + rows(details)
        + L.button(escape(link, quote=True), escape(words["button"]))
        + L.para(escape(words["note"]), muted=True, size=13) + L.para(escape(words["help"]), muted=True, size=13)))
    return subject, text, html


async def notify_paid(conn, order_id: str) -> None:
    """Ödeme tamamlanıp üyelik açılınca tek e-posta. Hata ödemeyi/üyeliği asla bozmaz (yalnız log)."""
    try:
        row = await conn.fetchrow(
            """SELECT r.plan,r.price_amount,r.user_id,p.period_start,p.period_end,u.security->>'email' AS email
               FROM nowpayments_payments r
               JOIN nowpayments_periods p ON p.payment_id=r.id
               JOIN commercial_auth_users u ON u.user_id=r.user_id
               WHERE r.id=$1 AND r.status='finished' AND r.erased_at IS NULL""", order_id)
        if row is None or not row["email"]:
            return
        language = await recipient_language(conn, row["user_id"])
        base = mail.validate_app_base_url()
        link = base + "/billing"
        mail.validate_action_url(link)
        subject, text, html = email_content(language=language, plan=row["plan"], amount=row["price_amount"],
                                            period_start=row["period_start"], period_end=row["period_end"], link=link)
        await asyncio.to_thread(mail.send_email_message, to_email=row["email"], subject=subject, text=text, html=html,
                                idempotency_key=order_id, idempotency_prefix="nowpayments-paid-")
    except Exception as exc:  # noqa: BLE001 -- e-posta hatası üyeliği etkilemez
        logger.warning("NOWPayments paid email failed type=%s", type(exc).__name__)
