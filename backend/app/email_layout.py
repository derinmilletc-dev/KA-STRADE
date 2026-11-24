"""Tüm e-postaların ortak görünümü: koyu zemin, KaisTrade logosu, yeşil vurgu, modern düğme.
Yalnız dış kabuk ve parçalar; metinleri ve bağlantıları çağıran şablon belirler. Çağıran, kullanıcıdan gelen her değeri
`html.escape` ile kaçışlı verir (burada tekrar kaçışlanmaz). Arka plan görseli, blur ve script yok: e-posta istemcileri güvenle gösterir."""

LOGO_URL = "https://kaistrade.com/kaistrade-logo-trim.png"
SITE = "kaistrade.com"
BG, CARD, LINE = "#0B0E0C", "#121714", "#1F2823"
INK, HEAD, MUTE, DIM, GREEN, GREEN_SOFT = "#E6ECE8", "#F2F6F3", "#A3B0A9", "#8E9C95", "#5FCB8E", "#7FD8A6"
FONT = "-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

FOOT = {
    "tr": "Bu e-posta KaisTrade tarafından otomatik gönderildi.",
    "en": "This email was sent automatically by KaisTrade.",
}


def logo(height: int = 34) -> str:
    width = round(height * 896 / 216)
    return (f'<img src="{LOGO_URL}" alt="KaisTrade" width="{width}" height="{height}" '
            f'style="display:block;border:0;outline:none;height:{height}px;width:{width}px;color:{GREEN};font-weight:800">')


def button(href: str, label: str) -> str:
    """Düğme: href ve label çağıran tarafından kaçışlanmış gelir."""
    return (f'<table role="presentation" cellspacing="0" cellpadding="0" style="margin:26px 0"><tr>'
            f'<td bgcolor="{GREEN}" style="border-radius:12px"><a href="{href}" style="display:inline-block;padding:15px 28px;'
            f'color:#06120C;font-size:15px;font-weight:700;text-decoration:none;border-radius:12px">{label}</a></td></tr></table>')


def callout(inner: str) -> str:
    return (f'<div style="margin:0 0 18px;padding:14px 16px;border-left:3px solid {GREEN};background:#0E1511;'
            f'border-radius:10px;color:{INK};font-size:14px;line-height:1.6">{inner}</div>')


def link_line(href: str, text: str) -> str:
    return (f'<p style="margin:0 0 6px;font-size:13px;line-height:1.6;overflow-wrap:anywhere;word-break:break-all">'
            f'<a href="{href}" style="color:{GREEN_SOFT}">{text}</a></p>')


def para(inner: str, *, muted: bool = False, size: int = 15) -> str:
    color = MUTE if muted else INK
    return f'<p style="margin:0 0 14px;font-size:{size}px;line-height:1.7;color:{color}">{inner}</p>'


def heading(text: str, *, level: int = 1) -> str:
    size = 25 if level == 1 else 20
    return f'<h{level} style="margin:0 0 14px;font-size:{size}px;line-height:1.3;color:{HEAD};font-weight:700">{text}</h{level}>'


def shell(*, lang: str, title: str, body: str, badge: str = "", width: int = 520) -> str:
    """Tam HTML belge. `title` sekme/önizleme başlığıdır (kaçışlı verilir), `body` kart içeriğidir."""
    language = "en" if lang == "en" else "tr"
    badge_cell = (f'<td align="right" style="font-size:11px;font-weight:700;letter-spacing:.14em;color:{GREEN}">'
                  f'<span style="border:1px solid {GREEN};border-radius:99px;padding:4px 10px">{badge}</span></td>') if badge else ""
    return f"""<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title></head>
<body style="margin:0;padding:0;background:{BG};color:{INK};font-family:{FONT}">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BG}"><tr><td align="center" style="padding:28px 14px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:{width}px;background:{CARD};border:1px solid {LINE};border-radius:20px;overflow:hidden">
<tr><td height="4" style="height:4px;line-height:4px;font-size:4px;background:{GREEN}">&nbsp;</td></tr>
<tr><td style="padding:24px 30px 0"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td>{logo()}</td>{badge_cell}</tr></table></td></tr>
<tr><td style="padding:22px 30px 30px">{body}</td></tr>
<tr><td style="padding:16px 30px 22px;border-top:1px solid {LINE};font-size:12px;line-height:1.6;color:{DIM}">{FOOT[language]}<br>{SITE}</td></tr>
</table></td></tr></table></body></html>"""
