"""Demo ikizi: canlı motora dokunmaz, yalnız demo motoruna yönlenir."""
import re
from pathlib import Path

from app import cockpit_demo_twin as twin

SOURCE = (Path(__file__).resolve().parents[1] / "app" / "cockpit_demo_twin.py").read_text(encoding="utf-8")


def test_twin_never_imports_or_names_the_live_engine():
    body = SOURCE.split('"""', 2)[2]
    assert not re.search(r"v25_execution|original_v2_live|LIVE_REST_BASE", body)
    assert "fapi.binance.com" not in re.sub(r"demo-fapi\.binance\.com", "", SOURCE)


def test_twin_routes_live_only_under_demo_prefix():
    paths = [route.path for route in twin.router.routes]
    assert paths and all(path.startswith("/api/binance-demo/cockpit/") for path in paths)
    assert len(paths) == len({(path, tuple(sorted(route.methods))) for path, route in zip(paths, twin.router.routes)})
    for needed in ("/status", "/original-v2/start", "/original-v2/stop", "/original-v2/risk-settings",
                   "/position/close", "/emergency", "/arm", "/consent"):
        assert f"/api/binance-demo/cockpit{needed}" in paths


def test_original_status_maps_percent_and_marks_demo(monkeypatch):
    v21 = {
        "settings": {"max_loss_per_trade": 2.0, "daily_loss_limit": 40.0, "max_margin_per_trade": 100, "allowed_symbols": ["BTCUSDT"]},
        "auto": {"enabled": False},
        "scanner": {},
        "daily": {"date": "2026-10-29", "realized_pnl": -5.0},
    }
    monkeypatch.setattr(twin, "server_flags", lambda: {"enabled": True, "send_orders": True})
    result = twin._original_status(v21, 1000.0)
    assert result["demo"] is True and result["enabled"] is False
    assert result["risk_percent"] == 0.2 and result["daily_loss_percent"] == 4.0
    assert result["daily"]["loss"] == 5.0 and result["daily"]["locked"] is False
    assert result["session"]["remaining_seconds"] == 0 and result["symbols"] == ["BTCUSDT"]


def test_start_refuses_when_server_flags_are_off(monkeypatch):
    import asyncio

    import pytest
    from fastapi import HTTPException

    monkeypatch.setattr(twin, "server_flags", lambda: {"enabled": True, "send_orders": False})
    body = twin.Loose(understood=True, session_hours=1)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(twin.start(object(), body))
    assert caught.value.status_code == 423


def test_twin_has_manual_wizard_routes():
    paths = {(route.path, m) for route in twin.router.routes for m in route.methods}
    for path, method in (("/order/preflight", "POST"), ("/order/test", "POST"), ("/order", "POST"), ("/policy", "PUT")):
        assert (f"/api/binance-demo/cockpit{path}", method) in paths


def test_demo_order_mapping_keeps_split_and_expiry_and_drops_extras():
    order = twin._demo_order({"symbol": "BTCUSDT", "direction": "LONG", "order_type": "LIMIT", "margin_usdt": 150, "leverage": 10,
                              "limit_price": 100, "stop_loss": 99, "tp1": 101, "tp2": 102, "tp3": 103,
                              "tp_split": "50-30-20", "limit_expiry_minutes": 240, "intent_id": "x", "atr": None, "confirmation": "y"})
    assert order.margin_usdt == 150 and order.tp_split == "50-30-20" and order.limit_expiry_minutes == 240


def test_policy_update_validates_bounds_and_writes_shared_settings(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app import v21_demo

    state = {"settings": {}}
    monkeypatch.setattr(v21_demo, "state_for", lambda request: state)
    monkeypatch.setattr(v21_demo, "persist_state", lambda value: None)

    async def fake_status(request):
        return {"ok": True}
    monkeypatch.setattr(twin, "build_status", fake_status)
    asyncio.run(twin.policy_update(SimpleNamespace(), twin.Loose(daily_loss_limit=40, max_leverage=20)))
    assert state["settings"]["daily_loss_limit"] == 40 and state["settings"]["max_leverage"] == 20
    try:
        asyncio.run(twin.policy_update(SimpleNamespace(), twin.Loose(daily_loss_limit=2)))
        raise AssertionError("limit altı kabul edildi")
    except HTTPException as exc:
        assert exc.status_code == 422


def test_protection_route_moves_stop_only_and_rejects_targets():
    import asyncio
    from fastapi import HTTPException

    for payload, code in (({"plan_id": "abcdef", "tp1": 101.0, "stop_loss": 99.0}, 409), ({"plan_id": "abcdef"}, 422)):
        try:
            asyncio.run(twin.protection(None, twin.Loose(**payload)))
            raise AssertionError("kabul edilmemeliydi")
        except HTTPException as exc:
            assert exc.status_code == code
