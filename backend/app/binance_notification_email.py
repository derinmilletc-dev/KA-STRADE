from html import escape

from . import email_layout as L
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
    html = L.shell(lang="en" if english else "tr", title=escape(title), body=(
        L.heading(escape(title), level=2) + L.callout(escape(message)) + L.button(escape(link, quote=True), go_to_panel)))
    return f"KaisTrade · {title}", text, html
