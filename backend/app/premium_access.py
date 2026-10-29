"""Membership enforcement and fail-closed public trading projections."""
from __future__ import annotations

from typing import Any

PRIVATE_KEYS = frozenset({
    "entry", "entry_price", "entryPrice", "entry_preview", "entryPreview", "entry_quality",
    "stop", "stop_loss", "stopLoss", "initial_stop_loss", "sl", "tp1", "tp2", "tp3",
    "target", "targets", "target_price", "take_profit", "takeProfit", "trigger_price",
    "triggerPrice", "limit_price", "support", "resistance", "risk_reward", "riskReward",
    "risk_pct", "potential_tp3_pct", "estimated_stop_loss_usdt", "stop_distance_pct",
    "reason", "reasons", "whyWait", "why_wait", "waitingFor", "waiting_for",
    "explanation", "long_case", "short_case", "longCase", "shortCase",
    "trigger_monitor", "triggerMonitor", "conditions", "invalidation", "detail",
    "plans", "events", "external_trades", "open_orders", "open_algo_orders",
})

PUBLIC_TRADE_KEYS = frozenset({
    "results", "items", "data", "history", "analysis", "signal", "scanner", "account",
    "positions", "settings", "policy", "auto", "credentials", "vault", "connections",
    "LIVE", "TESTNET", "stream", "readiness", "gates", "emergency", "daily",
    "authorization", "consent", "safety", "markets", "candidates",
    "id", "symbol", "symbols", "display", "direction", "side", "trend", "momentum",
    "price", "currentPrice", "current_price", "mark_price", "markPrice", "change",
    "volume", "volume_ratio", "volume_change_pct", "volatility_pct", "rsi", "macd",
    "macd_signal", "macd_hist", "atr", "ema20", "ema50", "ema200", "confidence",
    "smart_score", "opportunity_score", "final_decision_score", "mtf_direction",
    "mtf_alignment", "mtf_timeframes", "timeframes", "timeframe", "interval",
    "anomaly", "kind", "label", "strength", "time", "open", "high", "low", "close",
    "timestamp", "created_at", "updated_at", "cached", "count", "limit",
    "status", "connected", "configured", "active", "ready", "enabled", "passed",
    "key", "read_only", "real_trading_locked", "armed", "armed_until",
    "live_auto_trade", "execution_state", "reconciliation_required", "recovery_ready",
    "policy_acknowledged", "last_scan", "last_scan_at", "last_skip_reason",
    "last_cycle_stage", "scanned_symbol_count", "candidate_count", "candidate_symbols",
    "selected_symbols", "selected_symbols_count", "session_until", "expires_at",
    "valid", "scope_match", "fingerprint", "tested_at", "last_test_ok",
    "wallet_balance", "available_balance", "margin_balance", "unrealized_pnl",
    "realized_pnl", "remaining_loss_budget", "entries", "hedge_mode", "leverage",
    "quantity", "positionAmt", "positionSide", "margin_type", "marginType",
    "total_trades", "wins", "losses", "win_rate", "total_profit", "total_loss",
    "net_profit", "average_trade", "best_trade", "worst_trade", "profit_factor",
    "average_win", "average_loss", "losing_streak", "max_drawdown", "history_quality",
    "allowed_symbols", "risk_per_trade_pct", "max_positions", "max_exposure_usdt",
    "min_confidence", "min_margin_usdt", "max_margin_usdt", "max_leverage",
    "max_daily_loss_usdt", "max_trades_per_day", "timeframe", "strategy",
    "market_data", "live_account", "risk", "exposure", "protection", "protection_state",
    "last_verified", "secrets_returned_to_browser", "connection_test_creates_orders",
    "live_orders_require_v25_gates", "contractType", "quoteAsset", "score",
})


def public_projection(payload: Any, *, allowlist: bool = False) -> Any:
    if isinstance(payload, list):
        return [public_projection(row, allowlist=allowlist) for row in payload]
    if isinstance(payload, dict):
        return {
            key: public_projection(value, allowlist=allowlist)
            for key, value in payload.items()
            if key not in PRIVATE_KEYS and (not allowlist or key in PUBLIC_TRADE_KEYS)
        }
    return payload


def scanner_teaser(scanner: Any) -> dict[str, Any]:
    """Premium olmayan üyeye tarama sonucunu değil, yalnızca "tarama yapıldı" bilgisini verir.

    Aday listesi, semboller, yön ve skorlar hiçbir koşulda dahil edilmez; ekrandaki buzlu
    görünüm yalnızca bu sayılarla çizilir.
    """
    source = scanner if isinstance(scanner, dict) else {}

    def count(name: str) -> int:
        value = source.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    candidates = source.get("all_candidates")
    return {
        "locked": True,
        "scan_status": source.get("scan_status") if isinstance(source.get("scan_status"), str) else None,
        "running": source.get("running") is True,
        "last_scan_at": source.get("last_scan_at") if isinstance(source.get("last_scan_at"), str) else None,
        "next_scan_at": source.get("next_scan_at") if isinstance(source.get("next_scan_at"), str) else None,
        "last_scan_timeframe": source.get("last_scan_timeframe") if isinstance(source.get("last_scan_timeframe"), str) else None,
        "last_scan_universe": source.get("last_scan_universe") if isinstance(source.get("last_scan_universe"), str) else None,
        "last_error": "Tarama tamamlanamadı. Biraz sonra tekrar dene." if source.get("last_error") else None,
        "coins_scanned": count("coins_scanned"),
        "eligible_count": count("eligible_count"),
        "locked_count": len(candidates) if isinstance(candidates, list) else 0,
        "top_candidates": [],
        "all_candidates": [],
    }


def summary_shape(filtered: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Premium olmayan üyede /summary yanıtının iskeletini korur, içeriği boş bırakır.

    Ön yüz bu anahtarların varlığını doğrular; allowlist bunları düşürünce "Demo merkez yanıtı
    eksik veya geçersiz" hatası çıkıyordu. İşlem geçmişi, gerekçe ve sertifika verisi dahil edilmez.
    """
    auto = payload.get("auto") if isinstance(payload.get("auto"), dict) else {}
    cycles = auto.get("cycles")
    shaped_auto = dict(filtered.get("auto") if isinstance(filtered.get("auto"), dict) else {})
    shaped_auto["enabled"] = auto.get("enabled") is True
    shaped_auto["cycles"] = cycles if isinstance(cycles, (int, float)) and not isinstance(cycles, bool) else 0
    shaped_auto["last_decision"] = ""
    filtered["auto"] = shaped_auto
    for key in ("settings", "stream", "daily", "account", "protection"):
        if not isinstance(filtered.get(key), dict):
            filtered[key] = {}
    if not isinstance(filtered["settings"].get("allowed_symbols"), list):
        filtered["settings"]["allowed_symbols"] = []
    daily = filtered["daily"]
    if not isinstance(daily.get("remaining_loss_budget"), (int, float)) or isinstance(daily.get("remaining_loss_budget"), bool):
        daily["remaining_loss_budget"] = 0
    filtered["certificate"] = {}
    filtered["journal"] = []
    filtered["automation_trades"] = []
    return filtered


def public_notification_projection(payload: Any) -> Any:
    projected = public_projection(payload, allowlist=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list) or not isinstance(projected, dict):
        return projected
    visible_types = {"BINANCE_APPROVED", "BINANCE_REJECTED", "BINANCE_RENEWAL"}
    visible_keys = {"id", "type", "severity", "title", "message", "timestamp", "read", "target"}
    projected["items"] = [
        {key: value for key, value in item.items() if key in visible_keys}
        if isinstance(item, dict) and item.get("type") in visible_types
        and isinstance(item.get("id"), str) and item["id"].startswith("binance:")
        and len(item["id"]) == 40 and all(char in "0123456789abcdef" for char in item["id"][8:])
        else public_projection(item, allowlist=True)
        for item in payload["items"]
    ]
    return projected


def requires_premium(path: str, method: str) -> bool:
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    # Demo (Testnet) trading is open to every member. LIVE actions stay premium:
    # /api/v25/ is gated here and LIVE credentials are owner-only inside exchange_connections.
    if path.startswith("/api/v25/"):
        return True
    if path.startswith("/api/grid/plan/clear/"):
        return True
    if path in {
        "/api/grid/engine/start",
        "/api/grid/plan/save", "/api/grid/engine/recenter", "/api/paper/open",
        "/api/paper/limit", "/api/paper/bot/start", "/api/v7/orchestrator/start",
        "/api/v9/paper/order", "/api/v10/evolution/start", "/api/v11/risk/start",
        "/api/v9/twin/start", "/api/grid/engine/profile",
        "/api/paper/bot/training/toggle",
    }:
        return True
    return path.startswith(("/api/paper/demo/", "/api/v20/profile/"))


# Smart Trade (yalnız elle işlem planı): Kais'in otomatik stratejilerini başlatan yollar kapalıdır.
# Durdurma/güvenlik yolları (…/stop, disarm, emergency) bilerek burada YOK: biri otomasyonu durdurabilmelidir.
SMART_TRADE_AUTOMATION_PATHS = frozenset({
    "/api/v25/auto/start", "/api/v25/original-v2/start", "/api/v25/original-v2/extend-session",
    "/api/paper/bot/start", "/api/v7/orchestrator/start", "/api/v9/twin/start",
    "/api/v10/evolution/start", "/api/v11/risk/start",
})
AUTOMATION_LOCKED_MESSAGE = "Otomatik stratejiler MASTER MODE ile açılır. Smart Trade yalnız elle işlem içindir."


def automation_locked(path: str, method: str) -> bool:
    return method.upper() in {"POST", "PUT", "PATCH"} and path.rstrip("/") in SMART_TRADE_AUTOMATION_PATHS


DEMO_LIMIT_KEYS = ("max_margin_usdt", "max_leverage", "max_notional_usdt", "max_open_positions", "arm_minutes")


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def demo_status_shape(filtered: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Premium olmayan üyede /binance-demo/status ve /account yanıtının iskeletini korur.

    Ön yüz (demo-request.ts) "limits" nesnesini zorunlu tutar; allowlist onu düşürünce
    "Demo durum yanıtı eksik veya geçersiz" hatası çıkıyordu. Limitler sistem genelindeki
    sabit üst sınırlardır, kullanıcıya özel veri değildir; yalnız bilinen sayısal alanlar geçer.
    """
    limits = payload.get("limits") if isinstance(payload.get("limits"), dict) else {}
    filtered["limits"] = {key: limits[key] for key in DEMO_LIMIT_KEYS if _positive_number(limits.get(key))}
    for key in ("configured", "connected", "armed"):
        if not isinstance(filtered.get(key), bool):
            filtered[key] = False
    filtered["real_trading_locked"] = True
    if not (filtered.get("armed_until") is None or isinstance(filtered.get("armed_until"), str)):
        filtered["armed_until"] = None
    return filtered


def demo_account_shape(filtered: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """/binance-demo/account için iskelet: emir, plan ve pozisyon ayrıntıları boş liste olarak döner.

    Premium olmayan üye Demo emri açamaz (requires_premium); bu listeler zaten boş olmalıdır.
    Gizli alanlar (giriş fiyatı, planlar, açık emirler) hiçbir durumda geri eklenmez.
    """
    filtered = demo_status_shape(filtered, payload)
    for key in ("positions", "open_orders", "open_algo_orders", "plans"):
        filtered[key] = []
    if filtered["configured"]:
        for key in ("wallet_balance", "available_balance", "margin_balance", "unrealized_pnl"):
            value = filtered.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                filtered[key] = 0
    return filtered
