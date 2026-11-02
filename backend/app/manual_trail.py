"""İz süren stop (trailing) için saf hesap ve ayar doğrulaması. Borsaya dokunmaz; Canlı ve Demo aynı kuralı kullanır.

Kural: stop yalnız sıkılaşır (LONG'da yukarı, SHORT'ta aşağı), hiçbir zaman gevşemez. Fiyat, ayarlı mesafenin
(%) gerisinde kalacak şekilde izlenir; küçük kıpırdamalar için stop taşınmaz (her taşıma borsada sil-kur demektir)."""
from __future__ import annotations

import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

TRAIL_PCT_MIN = Decimal("0.3")
TRAIL_PCT_MAX = Decimal("10")
TRAIL_MIN_INTERVAL_SECONDS = 30
TRAIL_MAX_FAILURES = 3
# Stop, ayarlı mesafenin en az bu kadarı kadar iyileşmedikçe (ve fiyatın en az bu oranı kadar) taşınmaz.
TRAIL_MIN_STEP_OF_DISTANCE = Decimal("0.25")
TRAIL_MIN_STEP_OF_PRICE = Decimal("0.0005")


def valid_trail_pct(value: Any) -> Decimal | None:
    try:
        pct = Decimal(str(value))
    except Exception:  # noqa: BLE001 - geçersiz giriş yalnız reddedilir
        return None
    if not pct.is_finite() or pct < TRAIL_PCT_MIN or pct > TRAIL_PCT_MAX:
        return None
    return pct


def trail_target(direction: str, mark: Decimal, current_stop: Decimal, pct: Decimal, tick: Decimal | None = None) -> Decimal | None:
    """Yeni stop fiyatı; taşımaya değmiyorsa ya da güvenli değilse None.

    Önce tick'siz kaba fark bakılır: fark eşiğin altındaysa borsa kurallarını okumaya gerek yoktur."""
    if direction not in {"LONG", "SHORT"}:
        return None
    if not all(isinstance(value, Decimal) and value.is_finite() and value > 0 for value in (mark, current_stop, pct)):
        return None
    long_side = direction == "LONG"
    distance = mark * pct / 100
    raw = mark - distance if long_side else mark + distance
    improvement = raw - current_stop if long_side else current_stop - raw
    threshold = max(distance * TRAIL_MIN_STEP_OF_DISTANCE, mark * TRAIL_MIN_STEP_OF_PRICE)
    if improvement < threshold:
        return None
    target = raw
    if tick is not None:
        if not tick.is_finite() or tick <= 0:
            return None
        # LONG'da aşağı, SHORT'ta yukarı yuvarla: stop ayarlı mesafeden asla fiyata daha yakın olmaz.
        target = (raw / tick).to_integral_value(rounding=ROUND_FLOOR if long_side else ROUND_CEILING) * tick
        if (long_side and target <= current_stop) or (not long_side and target >= current_stop):
            return None
    if (long_side and target >= mark) or (not long_side and target <= mark) or target <= 0:
        return None
    return target


def trail_due(trail: dict[str, Any], now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return now - float(trail.get("last_move_epoch") or 0) >= TRAIL_MIN_INTERVAL_SECONDS


def trail_config(pct: Decimal, now_text: str) -> dict[str, Any]:
    return {"pct": float(pct), "set_at": now_text, "failures": 0, "last_move_epoch": 0.0}


def record_trail_failure(trail: dict[str, Any], reason: str) -> bool:
    """Başarısızlığı sayar; üst üste sınıra ulaşılırsa True (iz süren kapatılmalı)."""
    trail["failures"] = int(trail.get("failures") or 0) + 1
    trail["last_error"] = str(reason)[:200]
    # Hemen tekrar denemesin: aralık dolana kadar bekle.
    trail["last_move_epoch"] = time.time()
    return trail["failures"] >= TRAIL_MAX_FAILURES


def record_trail_move(trail: dict[str, Any]) -> None:
    trail["failures"] = 0
    trail["last_error"] = None
    trail["last_move_epoch"] = time.time()
