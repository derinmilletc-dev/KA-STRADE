"""KAIS FARMER (Demo): canlı Farmer motoru aynen; demo hesap, ARM yok, canlı kayıtla karışmaz."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

from app import farmer_demo as fd
from app.farmer_engine.live import LiveGates

SOURCE = (Path(__file__).resolve().parents[1] / "app" / "farmer_demo.py").read_text(encoding="utf-8")


def test_routes_mirror_live_farmer_under_demo_prefix():
    paths = {route.path for route in fd.router.routes}
    for name in ("status", "preview", "settings", "start", "stop-bot", "emergency-close", "acknowledge"):
        assert f"/api/binance-demo/cockpit/farmer/{name}" in paths
    assert all(path.startswith("/api/binance-demo/cockpit/farmer/") for path in paths)


def test_demo_never_touches_live_farmer_state_or_flags():
    body = SOURCE.split('"""', 2)[2]
    assert "live_farmer_registry" not in body and "FARMER_LIVE_ENV" not in body
    assert "KAISTRADE_FARMER_LIVE" in fd.DEMO_ENV  # yalnız durum cevabında kapıyı "açık" göstermek için; canlı bayrak okunmaz/yazılmaz
    assert fd.ACCOUNT_PREFIX == "DEMO" and fd._live_registry_key("u1") == "demo:u1"


def test_member_id_validation():
    assert fd.DemoFarmerRegistry.uid_of("") == "owner"
    assert fd.DemoFarmerRegistry.uid_of("abc-1_2") == "abc-1_2"
    for bad in ("a b", "../x", "x" * 81):
        try:
            fd.DemoFarmerRegistry.uid_of(bad)
            raise AssertionError("geçersiz üye kabul edildi")
        except ValueError:
            pass


def test_daily_loss_gate_uses_shared_demo_limit(monkeypatch):
    monkeypatch.setattr(fd.demo, "verified_realized_pnl", lambda state: -12.0)
    assert fd.demo_daily_loss_blockers({"settings": {"daily_loss_limit": 10}}) == ("DAILY_LOSS_LIMIT",)
    assert fd.demo_daily_loss_blockers({"settings": {"daily_loss_limit": 20}}) == ()
    assert fd.demo_daily_loss_blockers({"settings": {}}) == ()
    monkeypatch.setattr(fd.demo, "verified_realized_pnl", lambda state: (_ for _ in ()).throw(RuntimeError("x")))
    assert fd.demo_daily_loss_blockers({"settings": {"daily_loss_limit": 20}}) == ("DAILY_LEDGER_UNAVAILABLE",)


def test_gates_have_no_arm_or_capability_blockers_but_keep_maintenance(monkeypatch):
    live = fd.DemoLive(SimpleNamespace(state=SimpleNamespace(http=None)), "u1", None)
    app = SimpleNamespace(state=SimpleNamespace(maintenance=None, v25_execution={}))
    gates = fd.DemoGates(app, live, {"plans": {}}, "u1", None)
    monkeypatch.setattr(fd.DemoLive, "guard_new_entry", lambda self, m: None)
    result = gates.entry_gates()
    assert result.arm_blocked == () and result.safety_blocked == ()
    monkeypatch.setattr(fd.DemoLive, "guard_new_entry", lambda self, m: (_ for _ in ()).throw(RuntimeError("bakım")))
    assert gates.entry_gates().safety_blocked == ("MAINTENANCE",)
    assert isinstance(gates, LiveGates)


def test_foreign_occupied_sees_active_demo_plans_and_grids(monkeypatch):
    live = fd.DemoLive(SimpleNamespace(state=SimpleNamespace(http=None)), "u1", None)
    app = SimpleNamespace(state=SimpleNamespace(maintenance=None, v25_execution={}))
    state = {"plans": {"a": {"symbol": "BTCUSDT", "status": "AKTİF"}, "b": {"symbol": "ETHUSDT", "status": "KAPANDI"}}}
    gates = fd.DemoGates(app, live, state, "u1", None)
    monkeypatch.setattr(fd.demo, "demo_grid_busy", lambda *a: False)
    assert gates.foreign_occupied("BTCUSDT") == ("ACTIVE_PLAN",)
    assert gates.foreign_occupied("ETHUSDT") == ()
    monkeypatch.setattr(fd.demo, "demo_grid_busy", lambda *a: True)
    assert "OTHER_BOT" in gates.foreign_occupied("SOLUSDT")


def test_shim_isolated_margin_and_reservation_owner(monkeypatch):
    live = fd.DemoLive(SimpleNamespace(state=SimpleNamespace(http=None)), "u9", None)
    calls = []

    async def fake_margin(client, symbol):
        calls.append(symbol)
    monkeypatch.setattr(fd.demo, "set_isolated_margin", fake_margin)
    assert asyncio.run(live.set_live_isolated_margin(object(), "BTCUSDT")) == "ISOLATED" and calls == ["BTCUSDT"]
    assert live.live_reservation_owner({}, None) == "DEMO:u9"
    assert live.is_armed({}) is True and live.live_execution_blocked({}) is False
