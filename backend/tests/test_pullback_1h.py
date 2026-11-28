import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parents[1]
sys.path.insert(0, str(BACKEND))

from app.strategies import original_v2_pullback as pb  # noqa: E402


def row(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1.0}


def test_model_env_accepts_1h(monkeypatch):
    monkeypatch.setenv(pb.MODEL_ENV, "pullback_1h")
    assert pb.configured_model() == pb.MODEL_PULLBACK_1H
    assert pb.pullback_enabled() and pb.is_1h_model()
    monkeypatch.setenv(pb.MODEL_ENV, "pullback_v1")
    assert pb.pullback_enabled() and not pb.is_1h_model()
    monkeypatch.setenv(pb.MODEL_ENV, "legacy")
    assert not pb.pullback_enabled()


def test_invalid_model_rejected(monkeypatch):
    monkeypatch.setenv(pb.MODEL_ENV, "bilinmeyen")
    with pytest.raises(Exception):
        pb.configured_model()


def test_daily_from_4h_only_complete_days():
    rows = [row(86400 * 10 + i * 14400, 10 + i, 20 + i, 5 + i, 11 + i) for i in range(6)]
    rows += [row(86400 * 11 + i * 14400, 1, 2, 0.5, 1.5) for i in range(3)]  # eksik gün atılır
    days = pb.daily_from_4h(rows)
    assert len(days) == 1
    assert days[0]["time"] == 86400 * 10
    assert days[0]["open"] == 10 and days[0]["close"] == 16
    assert days[0]["high"] == 25 and days[0]["low"] == 5
    assert days[0]["volume"] == 6.0


def test_frames_for_model_shifts_only_for_1h(monkeypatch):
    four = [row(86400 * 3 + i * 14400, 1, 2, 0.5, 1.5) for i in range(6)]
    frames = {"15m": ["a"], "1h": ["b"], "4h": four}
    monkeypatch.setenv(pb.MODEL_ENV, "pullback_v1")
    assert pb.frames_for_model(frames) is frames
    monkeypatch.setenv(pb.MODEL_ENV, "pullback_1h")
    shifted = pb.frames_for_model(frames)
    assert shifted["15m"] == ["b"] and shifted["1h"] == four and len(shifted["4h"]) == 1


def test_1h_parameters_do_not_change_default():
    assert pb.PARAMETERS["room_min_atr"] == 0.5
    assert pb.PARAMETERS_1H["room_min_atr"] == 2.31 and pb.PARAMETERS_1H["target_r"] == 8.0
    assert pb.INTERVAL_SECONDS_1H == {"15m": 3600, "1h": 14400, "4h": 86400}


def test_default_is_1h_model_and_legacy_still_selectable(monkeypatch):
    monkeypatch.delenv(pb.MODEL_ENV, raising=False)
    assert pb.configured_model() == pb.MODEL_PULLBACK_1H and pb.pullback_enabled()
    monkeypatch.setenv(pb.MODEL_ENV, "")
    assert pb.configured_model() == pb.MODEL_PULLBACK_1H
    monkeypatch.setenv(pb.MODEL_ENV, "legacy")
    assert pb.configured_model() == pb.MODEL_LEGACY and not pb.pullback_enabled()


def test_1h_model_waits_between_hour_marks(monkeypatch):
    from app.strategies.contracts import StrategyInput

    monkeypatch.setenv(pb.MODEL_ENV, "pullback_1h")
    monkeypatch.setattr(pb, "_closed_windows", lambda request, seconds=None: ({"15m": [], "1h": [], "4h": []}, {}, None, []))
    request = StrategyInput("BTCUSDT", 3600 * 100 + 900, {"15m": [], "1h": [], "4h": []})
    result = pb.evaluate_live_pullback(request, allowed_symbols=["BTCUSDT"], enabled=True)
    assert result.signal.strategy_eligible is False
    assert result.signal.reason == "WAIT_NEXT_HOUR"
