"""Original V2 taramasının 24 saatlik elenme dökümü.

Yalnızca kayıt/özet: tarama kararını, emirleri ya da risk kapılarını değiştirmez. Her tarama bittiğinde
o taramadaki elenme sayıları geçmiş kaydına eklenir; özet son 24 saatin kayıtlarını toplar.
Saf standart kütüphane: canlı işlem koduna bağımlı değil.
"""
from __future__ import annotations

import math
from typing import Any

WINDOW_SECONDS = 24 * 60 * 60
MAX_KEYS = 24
# Güven/tuzak/kırılım/MTF gibi sinyal kapıları; 15m/1h/4h gibi başka "failed_checks" değerleri sayılmaz.
SIGNAL_CHECKS = ("direction", "confidence", "trap", "breakout", "mtf",
                 "pb_trend", "pb_structure", "pb_pullback", "pb_trigger", "pb_extension", "pb_room")


def clean_counts(value: Any, allowed: tuple[str, ...] | None = None) -> dict[str, int]:
    """{anahtar: pozitif tamsayı} dışındakini atar; anahtar sayısını sınırlar. Hata fırlatmaz."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, count in value.items():
        if len(result) >= MAX_KEYS:
            break
        if not isinstance(key, str) or not key or len(key) > 48:
            continue
        if allowed is not None and key not in allowed:
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            continue
        result[key] = count
    return result


def history_fields(rejections: Any, checks: Any) -> dict[str, dict[str, int]]:
    """Geçmiş kaydına eklenecek alanlar. Her ikisi de boşsa (veri yok) hiçbir alan eklenmez."""
    fields: dict[str, dict[str, int]] = {}
    reasons = clean_counts(rejections)
    failed = clean_counts(checks, SIGNAL_CHECKS)
    # "rejections" anahtarı, bu taramanın döküm tuttuğunu gösterir (sıfır elenme de geçerli bir sonuçtur).
    fields["rejections"] = reasons
    if failed:
        fields["checks"] = failed
    return fields


def summarize(history: Any, now: float, window: float = WINDOW_SECONDS) -> dict[str, Any]:
    """Son 24 saatteki, dökümü tutulmuş taramaların toplamı. Dökümü olmayan eski kayıtlar sayılmaz."""
    reasons: dict[str, int] = {}
    checks: dict[str, int] = {}
    scans = 0
    since: float | None = None
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("rejections"), dict):
            continue
        at = item.get("at")
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
            continue
        if not now - window <= at <= now:
            continue
        scans += 1
        since = at if since is None else min(since, at)
        for key, count in clean_counts(item["rejections"]).items():
            reasons[key] = reasons.get(key, 0) + count
        for key, count in clean_counts(item.get("checks"), SIGNAL_CHECKS).items():
            checks[key] = checks.get(key, 0) + count
    return {"scans": scans, "since_epoch": since, "reasons": reasons, "checks": checks}
