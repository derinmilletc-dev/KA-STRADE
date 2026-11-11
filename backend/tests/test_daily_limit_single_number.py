"""Günlük zarar limiti tek sayı: Risk limitleri ekranındaki USDT değeri otomatik (Original) işlemde de geçerli."""
from app import original_live_risk as risk


def test_policy_usdt_is_the_single_limit():
    state = {"policy": {"daily_loss_limit": 12.0}}
    assert risk.daily_limit_usdt(state, 75.0, {"daily_loss_percent": 5}) == 12.0


def test_falls_back_to_percent_when_policy_missing_or_invalid():
    for policy in ({}, {"daily_loss_limit": None}, {"daily_loss_limit": 0}, {"daily_loss_limit": float("nan")}, {"daily_loss_limit": True}):
        assert abs(risk.daily_limit_usdt({"policy": policy}, 80.0, {"daily_loss_percent": 5}) - 4.0) < 1e-9
    assert abs(risk.daily_limit_usdt({}, 80.0, {"daily_loss_percent": 5}) - 4.0) < 1e-9


def test_system_ceiling_always_applies():
    from app.execution_core import system_ceilings
    ceiling = system_ceilings()["max_daily_loss_usdt"]
    assert risk.daily_limit_usdt({"policy": {"daily_loss_limit": ceiling * 10}}, 1e9, {}) == ceiling
