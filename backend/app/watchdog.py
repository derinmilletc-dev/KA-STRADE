"""Sunucu bekçisi: site/servis/veritabanı düşerse sahibine e-posta gönderir (systemd zamanlayıcısı her dakika çalıştırır).

    python -m app.watchdog

Ardışık 3 başarısız kontrolde (≈3 dk) "düştü" e-postası, 6 saatte bir hatırlatma, düzelince "tekrar çalışıyor" e-postası.
Alıcı: ALERT_EMAIL (yoksa FOUNDING_NOTIFY_EMAIL). Ek adresler: WATCHDOG_URLS (virgülle). Sunucunun kendisi tamamen kapanırsa
bu bekçi de çalışmaz; onun için dışarıdan bir izleme servisi gerekir.
"""
import json
import logging
import os
import sys
import time
import urllib.request
from html import escape
from pathlib import Path

logger = logging.getLogger("kaistrade.watchdog")
FAIL_LIMIT = 3
REMIND_SECONDS = 6 * 3600
STATE_FILE = Path(os.getenv("WATCHDOG_STATE", "/var/lib/kaistrade-watchdog/state.json"))
DEFAULT_CHECKS = (
    ("Sunucu (arka uç)", "http://127.0.0.1:8000/healthz"),
    ("Veritabanı", "http://127.0.0.1:8000/api/health/database"),
    ("Web sitesi", "https://kaistrade.com/"),
)


def checks() -> list[tuple[str, str]]:
    extra = [item.strip() for item in os.getenv("WATCHDOG_URLS", "").split(",") if item.strip().startswith("https://")]
    return list(DEFAULT_CHECKS) + [(url, url) for url in extra]


def probe(url: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "KaisTrade-Watchdog/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - yalnız sabit http/https adresleri
            return 200 <= response.status < 300
    except Exception:  # noqa: BLE001 - her hata "düştü" demektir
        return False


BACKUP_MARKER = Path(os.getenv("WATCHDOG_BACKUP_MARKER", "/var/backups/kaistrade/LAST_OK"))
BACKUP_MAX_AGE = 36 * 3600


def backup_ok(now: float, marker: Path | None = None) -> bool:
    """Yedek kurulu değilse (işaret dosyası yok) sorun sayılmaz; kuruluysa son yedek 36 saatten yeni olmalı."""
    marker = marker or BACKUP_MARKER
    try:
        last = float(marker.read_text().strip())
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False
    return now - last <= BACKUP_MAX_AGE


def decide(state: dict, failed: list[str], now: float) -> tuple[dict, str | None]:
    """Yeni durum ve gönderilecek e-posta türü: 'down', 'remind', 'recovered' ya da None."""
    state = dict(state)
    if not failed:
        was_down = bool(state.get("alerted"))
        state.update(fails=0, alerted=False, down_since=None, last_alert=0)
        return state, "recovered" if was_down else None
    state["fails"] = int(state.get("fails", 0)) + 1
    if state["fails"] < FAIL_LIMIT:
        return state, None
    if not state.get("alerted"):
        state.update(alerted=True, down_since=now, last_alert=now)
        return state, "down"
    if now - float(state.get("last_alert", 0)) >= REMIND_SECONDS:
        state["last_alert"] = now
        return state, "remind"
    return state, None


def message(kind: str, failed: list[str], down_since: float | None, now: float) -> tuple[str, str, str]:
    names = ", ".join(failed) if failed else "—"
    if kind == "recovered":
        minutes = max(1, int((now - (down_since or now)) // 60))
        subject = "KaisTrade tekrar çalışıyor"
        text = f"Sorun giderildi. Kesinti yaklaşık {minutes} dakika sürdü."
    else:
        subject = "KaisTrade DÜŞTÜ: " + names if kind == "down" else "KaisTrade hâlâ çalışmıyor: " + names
        text = f"Şu kontroller başarısız: {names}. Sunucuya bağlanıp 'systemctl status kaistrade' ve 'journalctl -u kaistrade -n 50' ile bak."
    color = "#16a34a" if kind == "recovered" else "#dc2626"
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;border:1px solid #e5e7eb;border-radius:12px">'
            f'<h2 style="margin:0 0 12px;color:{color}">{escape(subject)}</h2><p style="font-size:15px;line-height:1.5">{escape(text)}</p></div>')
    return subject, text, html


def recipient() -> str:
    return (os.getenv("ALERT_EMAIL", "").strip() or os.getenv("FOUNDING_NOTIFY_EMAIL", "").strip()
            or os.getenv("PROTREBOT_BOOTSTRAP_OWNER_EMAIL", "").strip())


def run() -> int:
    failed = [name for name, url in checks() if not probe(url)]
    if not backup_ok(time.time()):
        failed.append("Gece yedeği (36 saattir alınmadı)")
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:  # noqa: BLE001
        state = {}
    now = time.time()
    previous_down_since = state.get("down_since")
    state, kind = decide(state, failed, now)
    if kind:
        to_email = recipient()
        if not to_email:
            logger.error("ALERT_EMAIL tanımlı değil; uyarı gönderilemedi")
        else:
            from .email_service import send_email_message
            subject, text, html = message(kind, failed, previous_down_since, now)
            try:
                send_email_message(to_email=to_email, subject=subject, text=text, html=html)
            except Exception as exc:  # noqa: BLE001
                logger.error("Uyarı e-postası gönderilemedi: %s", type(exc).__name__)
                # Gönderilemediyse bir sonraki dakika yeniden dene.
                if kind == "down":
                    state["alerted"] = False
                elif kind == "remind":
                    state["last_alert"] = 0
                else:
                    state.update(alerted=True, down_since=previous_down_since)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    except OSError as exc:
        logger.error("Durum dosyası yazılamadı: %s", type(exc).__name__)
    return 1 if failed else 0


def notify(subject: str, text: str) -> int:
    """Tek seferlik bildirim e-postası (ör. yedek betiği): python -m app.watchdog --notify "Konu" "Metin"."""
    to_email = recipient()
    if not to_email:
        return 1
    from .email_service import send_email_message
    html = (f'<div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;border:1px solid #e5e7eb;border-radius:12px">'
            f'<h2 style="margin:0 0 12px;color:#dc2626">{escape(subject)}</h2><p style="font-size:15px;line-height:1.5">{escape(text)}</p></div>')
    try:
        send_email_message(to_email=to_email, subject=subject, text=text, html=html)
    except Exception as exc:  # noqa: BLE001
        logger.error("Bildirim gönderilemedi: %s", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) == 4 and sys.argv[1] == "--notify":
        sys.exit(notify(sys.argv[2], sys.argv[3]))
    sys.exit(run())
