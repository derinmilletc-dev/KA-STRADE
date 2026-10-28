import pytest

from app.manual_grid import GridError, plan_grid

POLICY = {"max_leverage": 30, "daily_loss_limit": 100, "max_total_exposure_usdt": 100000, "max_stop_distance_pct": 5}


def base(**extra):
    args = dict(direction="LONG", start_price=100, end_price=96, levels=5, stop_loss=95,
                total_capital_usdt=1000, total_risk_usdt=50, leverage=5, policy=POLICY)
    args.update(extra)
    return plan_grid(**args)


def test_total_risk_is_split_and_every_level_loses_its_share_at_stop():
    result = base(cost_bps=0)
    assert len(result["levels"]) == 5
    for row in result["levels"]:
        assert abs(row["quantity"] * abs(row["entry_price"] - 95) - 10) < 1e-6
    assert result["within_account_limits"] and abs(result["total_risk_usdt"] - 50) < 1e-9


def test_short_grid_and_take_profit_direction():
    result = base(direction="SHORT", start_price=100, end_price=104, stop_loss=106)
    assert all(r["take_profit"] < r["entry_price"] for r in result["levels"])
    assert result["levels"][0]["take_profit"] < 100


def test_wrong_stop_side_or_level_count_rejected():
    with pytest.raises(GridError):
        base(stop_loss=97)
    with pytest.raises(GridError):
        base(levels=21)
    with pytest.raises(GridError):
        base(total_risk_usdt=2000)


def test_account_limits_are_reported_not_ignored():
    assert base(total_capital_usdt=60)["blocked_by"]
    assert any("Kaldıraç" in b for b in base(leverage=40)["blocked_by"])
    assert any("günlük" in b for b in base(policy={**POLICY, "daily_loss_limit": 10})["blocked_by"])
    assert any("stop mesafesi" in b for b in base(stop_loss=80, start_price=100, end_price=96)["blocked_by"])
