"""Founding 30 e-postaları: alındı (onay bağlantılı), incelemede, onaylandı, bekleme listesi, olumsuz.

Hepsi başvuru numarasını ve kişiye özel durum sayfası bağlantısını içerir.
HTML e-posta istemcileri için tablo + satır içi stil kullanılır; düz metin sürümü de gönderilir.
"""
from __future__ import annotations

import hashlib
import hmac
import html
from typing import Literal

from . import email_service

Kind = Literal["received", "review", "approved", "waitlist", "rejected", "invite", "reminder", "invite_expired",
               "account_created", "payment_pending", "active"]
GREEN = "#2ee59d"


def status_key(secret: str, reference: str) -> str:
    """Durum sayfası bağlantısı için imza: numarayı bilen ama imzayı bilmeyen kimse sayfayı açamaz."""
    return hmac.new(secret.encode("utf-8"), f"founding-status:{reference}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def status_url(base: str, secret: str | None, reference: str | None) -> str | None:
    if not secret or not reference:
        return None
    return f"{base}/basvuru-durumu?r={reference}&k={status_key(secret, reference)}"


COPY: dict[str, dict[str, dict[str, str]]] = {
    "tr": {
        "received": {"subject": "Başvurun alındı · {ref}", "title": "Başvurun alındı",
                     "body": "KaisTrade Founding 30'a gösterdiğin ilgi için teşekkürler. Başvurunu tamamlamak için aşağıdaki düğmeyle e-posta adresini onayla. Bağlantı 48 saat geçerlidir.",
                     "cta": "Başvurumu onayla", "status": "E-posta onayı bekliyor"},
        "review": {"subject": "Başvurun incelemede · {ref}", "title": "Başvurun incelemeye alındı",
                   "body": "E-posta adresin onaylandı. Başvurun ekibimiz tarafından manuel olarak değerlendirilecek; sonucu 72 saat içinde e-postayla bildireceğiz.",
                   "cta": "Başvuru durumunu gör", "status": "İnceleme bekliyor"},
        "approved": {"subject": "Founding 30: başvurun onaylandı · {ref}", "title": "Tebrikler, başvurun onaylandı",
                     "body": "Sana bir kurucu koltuğu ayrıldı. Kişiye özel davetini lansmandan bir hafta önce e-postayla göndereceğiz; davetle hesabını oluşturacaksın. Davet sana özeldir ve başkasına devredilemez. O aşamaya kadar hiçbir ücret alınmaz.",
                     "cta": "Başvuru durumunu gör", "status": "Onaylandı"},
        "waitlist": {"subject": "Founding 30: bekleme listesindesin · {ref}", "title": "Bekleme listesine alındın",
                     "body": "Başvurun için teşekkürler. Şu an tüm koltuklar ayrıldığı için seni bekleme listesine aldık. Bir koltuk boşalırsa sıradaki adaylara e-postayla haber vereceğiz.",
                     "cta": "Başvuru durumunu gör", "status": "Bekleme listesi"},
        "rejected": {"subject": "Founding 30 başvurun hakkında · {ref}", "title": "Başvurun değerlendirildi",
                     "body": "Başvurun için teşekkürler. Bu dönem Founding 30 için başvurunu kabul edemiyoruz. KaisTrade genel kullanıma açıldığında seni e-postayla haberdar edeceğiz.",
                     "cta": "Başvuru durumunu gör", "status": "Olumsuz"},
        "invite": {"subject": "KaisTrade davetin hazır · {ref}", "title": "Kişiye özel davetin hazır",
                   "body": "KaisTrade Founding 30 üyeliğin için davetin hazır. Aşağıdaki düğmeyle hesabını oluştur. Davet yalnızca bu e-posta adresiyle kullanılabilir, tek kullanımlıktır ve 48 saat geçerlidir.",
                   "cta": "Davetimi kabul et", "status": "Davet gönderildi"},
        "reminder": {"subject": "Davetin seni bekliyor · {ref}", "title": "Davetin hâlâ seni bekliyor",
                     "body": "KaisTrade davetin henüz kullanılmadı. Süresi dolmadan hesabını oluştur; aksi halde koltuğun sıradaki adaya geçebilir.",
                     "cta": "Davetimi kabul et", "status": "Davet gönderildi"},
        "invite_expired": {"subject": "Davetinin süresi doldu · {ref}", "title": "Davetinin süresi doldu",
                           "body": "Davetin kullanılmadan süresi doldu. Hâlâ katılmak istiyorsan bu e-postayı yanıtlayarak destek ekibimize yazabilirsin.",
                           "cta": "Başvuru durumunu gör", "status": "Davet süresi doldu"},
        "account_created": {"subject": "KaisTrade hesabın oluşturuldu · {ref}", "title": "Hesabın oluşturuldu",
                            "body": "Hesabın başarıyla oluşturuldu. Son bir güvenlik adımı kaldı: hesabını korumak için iki aşamalı doğrulamayı (2FA) etkinleştir.",
                            "cta": "Başvuru durumunu gör", "status": "Hesap oluşturuldu"},
        "payment_pending": {"subject": "Üyeliğini tamamla · {ref}", "title": "Üyeliğini tamamla",
                            "body": "Hesap kurulumun tamamlandı. Üyelik ödemen bekleniyor; ödemen doğrulandığında üyeliğin otomatik olarak açılır.",
                            "cta": "Başvuru durumunu gör", "status": "Ödeme bekleniyor"},
        "active": {"subject": "KaisTrade'e hoş geldin · üyeliğin aktif", "title": "Üyeliğin aktif, hoş geldin",
                   "body": "Tebrikler! KaisTrade üyeliğin artık aktif. Hesabın hazır; platforma giriş yapabilirsin. Kurucu üyelerimizden biri olduğun için teşekkür ederiz.",
                   "cta": "Başvuru durumunu gör", "status": "Aktif üye"},
        "hello": "Merhaba {name},", "ref": "Başvuru numarası", "state": "Durum", "program": "Program",
        "foot": "Bu e-postayı KaisTrade Founding 30 başvurun nedeniyle aldın. Başvuruyu sen yapmadıysan bu e-postayı yok sayabilirsin.",
        "risk": "Kripto vadeli işlemler yüksek risk içerir; kâr garantisi verilmez.",
    },
    "en": {
        "received": {"subject": "Application received · {ref}", "title": "We received your application",
                     "body": "Thank you for your interest in KaisTrade Founding 30. To complete your application, confirm your email address with the button below. The link is valid for 48 hours.",
                     "cta": "Confirm my application", "status": "Awaiting email confirmation"},
        "review": {"subject": "Your application is in review · {ref}", "title": "Your application is in review",
                   "body": "Your email is confirmed. Our team reviews every application manually; we will email you the result within 72 hours.",
                   "cta": "View application status", "status": "Awaiting review"},
        "approved": {"subject": "Founding 30: you're approved · {ref}", "title": "Congratulations, you're approved",
                     "body": "A founding seat is reserved for you. We will email your personal invitation one week before launch; you will use it to create your account. The invitation is personal and cannot be transferred. Nothing is charged before then.",
                     "cta": "View application status", "status": "Approved"},
        "waitlist": {"subject": "Founding 30: you're on the waitlist · {ref}", "title": "You're on the waitlist",
                     "body": "Thanks for applying. All seats are currently reserved, so we have added you to the waitlist. If a seat opens up we will email the next applicants.",
                     "cta": "View application status", "status": "Waitlisted"},
        "rejected": {"subject": "About your Founding 30 application · {ref}", "title": "Your application was reviewed",
                     "body": "Thanks for applying. We can't accept your application for Founding 30 this round. We will let you know by email when KaisTrade opens to everyone.",
                     "cta": "View application status", "status": "Not approved"},
        "invite": {"subject": "Your KaisTrade invitation is ready · {ref}", "title": "Your personal invitation is ready",
                   "body": "Your invitation to KaisTrade Founding 30 is ready. Use the button below to create your account. It works only with this email address, can be used once and is valid for 48 hours.",
                   "cta": "Accept your invitation", "status": "Invitation sent"},
        "reminder": {"subject": "Your KaisTrade invitation is waiting · {ref}", "title": "Your invitation is still waiting",
                     "body": "Your KaisTrade invitation has not been used yet. Create your account before it expires, otherwise your seat may pass to the next applicant.",
                     "cta": "Accept your invitation", "status": "Invitation sent"},
        "invite_expired": {"subject": "Your KaisTrade invitation has expired · {ref}", "title": "Your invitation has expired",
                           "body": "Your invitation expired before it was used. If you are still interested in joining KaisTrade, reply to this email to contact our support team.",
                           "cta": "View application status", "status": "Invitation expired"},
        "account_created": {"subject": "Your KaisTrade account has been created · {ref}", "title": "Your account has been created",
                            "body": "Your account was created successfully. One important security step remains: please set up two-factor authentication (2FA) to secure your account.",
                            "cta": "View application status", "status": "Account created"},
        "payment_pending": {"subject": "Complete your KaisTrade membership · {ref}", "title": "Complete your membership",
                            "body": "Your account setup is complete. Your membership payment is now awaiting completion; once it is verified your membership is activated automatically.",
                            "cta": "View application status", "status": "Payment pending"},
        "active": {"subject": "Welcome to KaisTrade · your membership is active", "title": "Your membership is active",
                   "body": "Congratulations! Your KaisTrade membership is now active. Your account is ready and you can access the platform. Thank you for being one of our founding members.",
                   "cta": "View application status", "status": "Active member"},
        "hello": "Hi {name},", "ref": "Application number", "state": "Status", "program": "Program",
        "foot": "You received this email because of your KaisTrade Founding 30 application. If you did not apply, you can ignore it.",
        "risk": "Crypto futures carry high risk; no profit is guaranteed.",
    },
}


def render(kind: Kind, *, name: str, reference: str, lang: str, action_url: str | None) -> tuple[str, str, str]:
    words = COPY["en" if lang == "en" else "tr"]
    copy = words[kind]
    first = (name or "").strip().split(" ")[0] or name
    subject = copy["subject"].format(ref=reference)
    text_lines = [words["hello"].format(name=first), "", copy["title"], "", copy["body"], "",
                  f"{words['ref']}: {reference}", f"{words['state']}: {copy['status']}", f"{words['program']}: Founding 30"]
    if action_url:
        text_lines += ["", f"{copy['cta']}: {action_url}"]
    text_lines += ["", words["foot"], words["risk"], "", "— KaisTrade"]
    e = html.escape
    button = (f"<tr><td style='padding:8px 0 4px'><a href='{e(action_url)}' style='display:inline-block;background:{GREEN};color:#04130c;"
              f"text-decoration:none;font-weight:700;font-size:15px;padding:13px 24px;border-radius:12px'>{e(copy['cta'])}</a></td></tr>"
              if action_url else "")
    rows = "".join(
        f"<tr><td style='padding:9px 0;color:#93a1b0;font-size:13px;border-top:1px solid #1d2a35'>{e(label)}</td>"
        f"<td align='right' style='padding:9px 0;font-size:13px;font-weight:700;color:{color};border-top:1px solid #1d2a35'>{e(value)}</td></tr>"
        for label, value, color in ((words["ref"], reference, "#e8eef4"), (words["program"], "Founding 30", "#e8eef4"),
                                    (words["state"], copy["status"], GREEN if kind in ("approved", "invite", "reminder", "account_created", "active") else "#ff8d9a" if kind in ("rejected", "invite_expired") else "#ffd36b"))
    )
    body_html = f"""<!doctype html><html><body style="margin:0;padding:0;background:#05090d">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#05090d;padding:28px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#0b1218;border:1px solid #1f4a3a;border-radius:20px;font-family:Arial,Helvetica,sans-serif;color:#e8eef4">
<tr><td style="padding:24px 26px 6px"><span style="font-size:20px;font-weight:800;color:#e8eef4">kais</span><span style="font-size:20px;font-weight:800;color:{GREEN}">trade</span>
<span style="float:right;margin-top:4px;border:1px solid {GREEN};color:{GREEN};border-radius:99px;padding:3px 10px;font-size:11px;font-weight:700;letter-spacing:.14em">FOUNDING 30</span></td></tr>
<tr><td style="padding:14px 26px 0"><p style="margin:0 0 6px;color:#93a1b0;font-size:14px">{e(words['hello'].format(name=first))}</p>
<h1 style="margin:0 0 12px;font-size:22px;line-height:1.3;color:#ffffff">{e(copy['title'])}</h1>
<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#c3ced9">{e(copy['body'])}</p></td></tr>
<tr><td style="padding:0 26px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>
<tr><td style="padding:14px 26px 6px"><table role="presentation" cellpadding="0" cellspacing="0">{button}</table></td></tr>
<tr><td style="padding:14px 26px 24px;color:#6f7f90;font-size:12px;line-height:1.5">{e(words['foot'])}<br>{e(words['risk'])}</td></tr>
</table></td></tr></table></body></html>"""
    return subject, "\n".join(text_lines), body_html


def send(kind: Kind, *, email: str, name: str, reference: str, lang: str, action_url: str | None, idempotency: str) -> None:
    subject, text, body_html = render(kind, name=name, reference=reference, lang=lang, action_url=action_url)
    if action_url:
        email_service.validate_action_url(action_url)
    email_service.send_email_message(to_email=email, subject=subject, text=text, html=body_html,
                                     idempotency_key=idempotency, idempotency_prefix=f"founding-{kind}-")
