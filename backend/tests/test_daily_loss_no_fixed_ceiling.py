"""Günlük zarar limiti için sabit 100 USDT tavanı kaldırıldı: limit kullanıcının/stratejinin kendi ayarıdır."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import execution_core, v25_execution  # noqa: E402


def test_policy_keeps_a_large_daily_loss_limit():
    policy = execution_core.sanitize_execution_policy({"daily_loss_limit": 5000})
    assert policy["daily_loss_limit"] == 5000


def test_policy_still_has_a_floor_and_survives_garbage():
    assert execution_core.sanitize_execution_policy({"daily_loss_limit": 1})["daily_loss_limit"] == 5
    assert execution_core.sanitize_execution_policy({"daily_loss_limit": "abc"})["daily_loss_limit"] == 10
    huge = execution_core.sanitize_execution_policy({"daily_loss_limit": float("inf")})["daily_loss_limit"]
    assert huge == execution_core.HARD_MAX_DAILY_LOSS_USDT and huge < float("inf")


def test_the_other_per_trade_ceilings_are_untouched():
    ceilings = execution_core.system_ceilings()
    assert ceilings["max_loss_per_trade_usdt"] == 25.0
    assert ceilings["max_margin_per_trade_usdt"] == 300.0
    assert ceilings["max_total_exposure_usdt"] == 1000.0


def test_v25_policy_request_accepts_more_than_100():
    assert v25_execution.PolicyUpdate(daily_loss_limit=2500).daily_loss_limit == 2500
