"""Coinbase okuma işlemleri: ham cevabı sade, sabit bir biçime çevirir.

Beklenen alan yoksa sessizce boş döndürmek yerine açık hata verir (BAD_RESPONSE).
Para değerleri Coinbase'in verdiği metin olarak aktarılır; burada yuvarlama/çevirme yok.
"""

from __future__ import annotations

from typing import Any

from .client import CoinbaseClient
from .errors import CoinbaseError

BALANCE_PATH = "/api/v3/brokerage/cfm/balance_summary"
POSITIONS_PATH = "/api/v3/brokerage/cfm/positions"
MARGIN_WINDOW_PATH = "/api/v3/brokerage/cfm/intraday/current_margin_window"
ACCOUNTS_PATH = "/api/v3/brokerage/accounts"
KEY_PERMISSIONS_PATH = "/api/v3/brokerage/key_permissions"

AMOUNT_FIELDS = (
    "futures_buying_power", "total_usd_balance", "cbi_usd_balance", "cfm_usd_balance",
    "total_open_orders_hold_amount", "unrealized_pnl", "daily_realized_pnl", "initial_margin",
    "available_margin", "liquidation_threshold", "liquidation_buffer_amount",
    "total_pending_transfers_amount", "funding_pnl",
)
WINDOW_FIELDS = (
    "margin_window_type", "margin_level", "initial_margin", "maintenance_margin",
    "liquidation_buffer", "total_hold", "futures_buying_power",
)
POSITION_FIELDS = (
    "product_id", "side", "number_of_contracts", "avg_entry_price", "current_price",
    "unrealized_pnl", "daily_realized_pnl", "expiration_time",
)
_LEVEL_RANK = {"BASE": 0, "WARNING": 1, "DANGER": 2, "LIQUIDATION": 3}


def _bad(message: str) -> CoinbaseError:
    return CoinbaseError("BAD_RESPONSE", f"Coinbase cevabı beklenen biçimde değil: {message}")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _amount(value: Any) -> dict[str, str | None] | None:
    """Coinbase tutarları {value, currency} gelir; düz metin gelirse de kabul edilir."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {"value": _text(value.get("value")), "currency": _text(value.get("currency")) or "USD"}
    text = _text(value)
    return None if text is None else {"value": text, "currency": "USD"}


def level_rank(level: Any) -> int:
    """Marjin seviyesi kötüleştikçe artar. Tanınmayan değer EN KÖTÜ sayılır (güvenli taraf)."""
    name = str(level or "").rsplit("_", 1)[-1].upper()
    return _LEVEL_RANK.get(name, 9)


def normalize_window(raw: Any, label: str) -> dict[str, str | None]:
    if not isinstance(raw, dict):
        raise _bad(f"{label} marjin penceresi yok")
    return {name: _text(raw.get(name)) for name in WINDOW_FIELDS}


def normalize_balance_summary(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("balance_summary")
    if not isinstance(summary, dict):
        raise _bad("balance_summary yok")
    amounts = {name: _amount(summary.get(name)) for name in AMOUNT_FIELDS}
    if amounts["futures_buying_power"] is None or amounts["available_margin"] is None:
        raise _bad("futures_buying_power veya available_margin yok")
    intraday = normalize_window(summary.get("intraday_margin_window_measure"), "gün içi")
    overnight = normalize_window(summary.get("overnight_margin_window_measure"), "gece")
    worse = max((intraday, overnight), key=lambda item: level_rank(item["margin_level"]))
    return {
        **amounts,
        "liquidation_buffer_percentage": _text(summary.get("liquidation_buffer_percentage")),
        "intraday_window": intraday,
        "overnight_window": overnight,
        "worst_margin_level": worse["margin_level"],
        "worst_margin_window": "intraday" if worse is intraday else "overnight",
    }


def normalize_margin_window(payload: dict[str, Any]) -> dict[str, Any]:
    window = payload.get("margin_window")
    if not isinstance(window, dict):
        raise _bad("margin_window yok")
    return {
        "margin_window_type": _text(window.get("margin_window_type")),
        "end_time": _text(window.get("end_time")),
        "intraday_killswitch": payload.get("is_intraday_margin_killswitch_enabled") is True,
        "intraday_enrollment_killswitch": payload.get("is_intraday_margin_enrollment_killswitch_enabled") is True,
    }


def normalize_positions(payload: dict[str, Any]) -> list[dict[str, str | None]]:
    if "positions" not in payload:
        if payload:
            raise _bad("positions alanı yok")
        return []
    rows = payload["positions"]
    if not isinstance(rows, list):
        raise _bad("positions liste değil")
    result: list[dict[str, str | None]] = []
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("product_id")):
            raise _bad("pozisyon satırında product_id yok")
        result.append({name: _text(row.get(name)) for name in POSITION_FIELDS})
    return result


async def fetch_account(client: CoinbaseClient) -> dict[str, Any]:
    balance = normalize_balance_summary(await client.get(BALANCE_PATH))
    window = normalize_margin_window(await client.get(MARGIN_WINDOW_PATH))
    return {"balance": balance, "margin_window": window}


async def fetch_positions(client: CoinbaseClient) -> list[dict[str, str | None]]:
    return normalize_positions(await client.get(POSITIONS_PATH))


async def check_key(client: CoinbaseClient) -> dict[str, Any]:
    """Anahtarı doğrular: hesaplar uç noktası imzayı ve 'görüntüleme' iznini sınar.

    key_permissions ucu bu araştırmada doğrulanmadı: çalışırsa transfer iznini okuruz;
    çalışmazsa hata YUTULMAZ, 'permissions_checked: false' ve açık uyarı döner.
    """
    await client.get(ACCOUNTS_PATH, params={"limit": 1})
    result: dict[str, Any] = {
        "auth_ok": True, "permissions_checked": False, "can_view": None, "can_trade": None,
        "can_transfer": None, "warning": None,
    }
    try:
        permissions = await client.get(KEY_PERMISSIONS_PATH)
    except CoinbaseError as exc:
        if exc.code in ("RATE_LIMITED", "NETWORK", "CLOCK_SKEW"):
            raise
        result["warning"] = (
            "Anahtar izinleri okunamadı, 'transfer' izninin kapalı olduğunu kendin kontrol et. "
            f"({exc.message})"
        )
        return result
    flags = {name: permissions.get(name) for name in ("can_view", "can_trade", "can_transfer")}
    if not all(isinstance(value, bool) for value in flags.values()):
        result["warning"] = "Anahtar izinleri beklenmedik biçimde geldi; 'transfer' izninin kapalı olduğunu kendin kontrol et."
        return result
    result.update(flags)
    result["permissions_checked"] = True
    return result
