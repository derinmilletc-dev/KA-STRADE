"""Coinbase emir sınırları. DEĞİŞMEZ kurallar kodda sabittir; ortam değişkeni onları yalnızca SIKILAŞTIRABİLİR.

Değişmez (üst sınır koddadır, env bunu aşamaz):
- Bir işlemin en kötü kaybı en fazla 5 USD (1 USDT = 1 USD kabul edilir)
- Kaldıraç 3x–5x aralığında (env aralığı sadece daraltabilir)
- Aynı anda en fazla 5 açık emir, en fazla 3 açık pozisyon (Coinbase hesabı genelinde)
- Stop zorunlu (bu dosyada ayar yok; akış stop'suz pozisyon bırakmaz)

Ayarlanabilir (sınırlı aralıkta): günlük zarar sınırı, stop-limit mesafesi, kayma/komisyon payı.
Geçersiz env değeri yok sayılır ve GÜVENLİ varsayılan kullanılır (sessizce gevşetmez).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

HARD_MAX_LOSS_USD = Decimal("5")
HARD_LEVERAGE_MIN = Decimal("3")
HARD_LEVERAGE_MAX = Decimal("5")
HARD_MAX_OPEN_ORDERS = 5
HARD_MAX_POSITIONS = 3


def _env_decimal(name: str, default: Decimal, low: Decimal, high: Decimal) -> Decimal:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return default
    if not value.is_finite() or value < low or value > high:
        return default
    return value


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if low <= value <= high else default


@dataclass(frozen=True)
class Limits:
    max_loss_usd: Decimal
    leverage_min: Decimal
    leverage_max: Decimal
    max_open_orders: int
    max_positions: int
    daily_loss_limit_usd: Decimal
    stop_limit_gap_pct: Decimal      # stop-limit'te limit, stop fiyatından bu oran kadar uzakta
    entry_slippage_pct: Decimal      # piyasa girişi için olumsuz kayma payı
    fee_rate: Decimal                # tek yön komisyon tahmini (açılış + kapanış hesaba katılır)
    min_stop_distance_pct: Decimal
    max_stop_distance_pct: Decimal
    max_spread_pct: Decimal


def current_limits() -> Limits:
    leverage_min = _env_decimal("COINBASE_LEVERAGE_MIN", HARD_LEVERAGE_MIN, HARD_LEVERAGE_MIN, HARD_LEVERAGE_MAX)
    leverage_max = _env_decimal("COINBASE_LEVERAGE_MAX", HARD_LEVERAGE_MAX, HARD_LEVERAGE_MIN, HARD_LEVERAGE_MAX)
    if leverage_min > leverage_max:
        leverage_min, leverage_max = HARD_LEVERAGE_MIN, HARD_LEVERAGE_MAX
    return Limits(
        max_loss_usd=_env_decimal("COINBASE_MAX_LOSS_USD", HARD_MAX_LOSS_USD, Decimal("0.1"), HARD_MAX_LOSS_USD),
        leverage_min=leverage_min,
        leverage_max=leverage_max,
        max_open_orders=_env_int("COINBASE_MAX_OPEN_ORDERS", HARD_MAX_OPEN_ORDERS, 1, HARD_MAX_OPEN_ORDERS),
        max_positions=_env_int("COINBASE_MAX_POSITIONS", HARD_MAX_POSITIONS, 1, HARD_MAX_POSITIONS),
        daily_loss_limit_usd=_env_decimal("COINBASE_DAILY_LOSS_LIMIT_USD", Decimal("10"), Decimal("1"), Decimal("50")),
        stop_limit_gap_pct=_env_decimal("COINBASE_STOP_LIMIT_GAP_PCT", Decimal("0.003"), Decimal("0.0005"), Decimal("0.02")),
        entry_slippage_pct=_env_decimal("COINBASE_ENTRY_SLIPPAGE_PCT", Decimal("0.001"), Decimal("0"), Decimal("0.02")),
        fee_rate=_env_decimal("COINBASE_FEE_RATE", Decimal("0.001"), Decimal("0.0001"), Decimal("0.01")),
        min_stop_distance_pct=Decimal("0.001"),
        max_stop_distance_pct=Decimal("0.10"),
        max_spread_pct=Decimal("0.005"),
    )
