"""Önemli saatler: ABD makro veri takvimi (FOMC, TÜFE, işsizlik raporu) ve fonlama saatleri.

Takvim elle yazılmıştır (kaynak: federalreserve.gov FOMC takvimi ve bls.gov yayın takvimi, 2026-11 itibarıyla).
2027 FOMC tarihleri Fed tarafından "geçici" olarak işaretlidir. Takvimin bittiği tarihten sonra makro uyarı vermez
(`calendar_covers` yanlış güven vermemek için bunu açıkça bildirir); fonlama saatleri her zaman çalışır.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
CALENDAR_END = date(2027, 12, 31)

FOMC = ["2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27", "2027-11-17"]
CPI = ["2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10", "2027-07-14", "2027-08-11",
       "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"]
JOBS = ["2026-12-04", "2027-01-08", "2027-02-05", "2027-03-05", "2027-04-02", "2027-05-07", "2027-06-04", "2027-07-02", "2027-08-06",
        "2027-09-03", "2027-10-08", "2027-11-05", "2027-12-03"]
KINDS = (("fomc", "FOMC faiz kararı", FOMC, time(14, 0)), ("cpi", "ABD enflasyon (TÜFE) verisi", CPI, time(8, 30)),
         ("jobs", "ABD işsizlik/istihdam raporu", JOBS, time(8, 30)))
FUNDING_HOURS = (0, 8, 16)   # UTC; Binance varsayılan 8 saatlik fonlama


def _events() -> list[tuple[datetime, str, str]]:
    out = []
    for kind, name, days, at in KINDS:
        for day in days:
            local = datetime.combine(date.fromisoformat(day), at, tzinfo=NY)
            out.append((local.astimezone(timezone.utc), kind, name))
    return sorted(out)


EVENTS = _events()


def calendar_covers(now: datetime) -> bool:
    return now.astimezone(timezone.utc).date() <= CALENDAR_END


def next_funding(now: datetime) -> datetime:
    now = now.astimezone(timezone.utc)
    for hour in FUNDING_HOURS:
        t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if t > now:
            return t
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _span(minutes: float) -> str:
    minutes = max(0, int(round(minutes)))
    if minutes < 60:
        return f"{minutes} dk"
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} sa {rest} dk" if rest else f"{hours} sa"
    return f"{hours // 24} gün"


def upcoming(now: datetime, *, funding_at: datetime | None = None, funding_pct: float | None = None, horizon_hours: int = 48) -> dict[str, Any]:
    """Önümüzdeki saatlerin uyarıları. `level`: danger (≤2 sa makro), warn (≤12 sa makro, ≤30 dk fonlama), info."""
    now = now.astimezone(timezone.utc)
    items: list[dict[str, Any]] = []
    for at, kind, name in EVENTS:
        minutes = (at - now).total_seconds() / 60
        if -30 <= minutes <= horizon_hours * 60:
            level = "danger" if minutes <= 120 else "warn" if minutes <= 720 else "info"
            if minutes < 0:
                text = f"{name} {_span(-minutes)} önce açıklandı: fiyat hâlâ oynak olabilir."
                level = "warn"
            else:
                text = f"{name} {_span(minutes)} sonra (TSİ {at.astimezone(ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')}). Öncesinde ve sonrasında sert hareket olabilir."
            items.append({"kind": kind, "at": at.isoformat(), "minutes": round(minutes), "level": level, "text": text})
    fund = funding_at.astimezone(timezone.utc) if funding_at else next_funding(now)
    fmin = (fund - now).total_seconds() / 60
    if 0 <= fmin <= 30:
        extra = f" Oran {funding_pct:+.4f}%." if funding_pct is not None else ""
        items.append({"kind": "funding", "at": fund.isoformat(), "minutes": round(fmin), "level": "warn",
                      "text": f"Fonlama {_span(fmin)} sonra.{extra} Kalabalık tarafın pozisyon kapattığı anlarda kısa süreli oynama olabilir."})
    items.sort(key=lambda i: i["minutes"])
    return {"covered": calendar_covers(now), "items": items[:4], "next_funding": fund.isoformat()}
