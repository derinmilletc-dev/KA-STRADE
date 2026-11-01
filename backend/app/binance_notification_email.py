from html import escape

from . import email_service as mail
from .binance_notifications import presentation


def notification_content(payload):
    _, title, message, path = presentation(payload)
    english = payload.get("language") == "en"
    go_to_panel = "Go to panel" if english else "Panele git"
    base = mail.validate_app_base_url()
    if base != mail.CANONICAL_EMAIL_ORIGIN:
        raise mail.EmailDeliveryError("Canonical payment notification origin required", provider="resend", code="provider_unavailable")
    link = base+path
    mail.validate_action_url(link)
    text = f"KaisTrade\n\n{title}\n\n{message}\n\n{go_to_panel}:\n{link}"
    html = f"""<!doctype html><html lang="{'en' if english else 'tr'}"><body style="margin:0;background:#f4f6f8;color:#18242d;font-family:Arial,sans-serif">
<div style="max-width:520px;margin:32px auto;padding:28px;background:white;border-radius:16px">
<p style="font-weight:700">KaisTrade</p><h2>{escape(title)}</h2><p>{escape(message)}</p>
<p><a href="{escape(link, quote=True)}">{go_to_panel}</a></p></div></body></html>"""
    return f"KaisTrade · {title}", text, html
