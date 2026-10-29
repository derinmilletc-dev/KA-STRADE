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


def test_original_status_maps_percent_and_marks_demo():
    v21 = {
        "settings": {"max_loss_per_trade": 2.0, "daily_loss_limit": 40.0, "max_margin_per_trade": 100, "allowed_symbols": ["BTCUSDT"]},
        "auto": {"enabled": False},
        "scanner": {},
        "daily": {"date": "2026-10-29", "realized_pnl": -5.0},
    }
    result = twin._original_status(v21, 1000.0)
    assert result["demo"] is True and result["enabled"] is False
    assert result["risk_percent"] == 0.2 and result["daily_loss_percent"] == 4.0
    assert result["daily"]["loss"] == 5.0 and result["daily"]["locked"] is False
    assert result["session"]["remaining_seconds"] == 0 and result["symbols"] == ["BTCUSDT"]
