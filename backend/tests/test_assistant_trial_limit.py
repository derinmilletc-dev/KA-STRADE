from types import SimpleNamespace
from unittest.mock import patch

from app.assistant_api import limited_config
from app.assistant_config import AssistantConfig

USER = {"id": "user-a"}


def test_trial_member_gets_the_lower_daily_limit():
    config = AssistantConfig(ANTHROPIC_API_KEY="assistant-tests-not-real")
    with patch("app.member_entitlements.subscription_for_request", return_value={"mode": "FREE_TRIAL"}):
        limited = limited_config(SimpleNamespace(), USER, config)
    assert limited.daily_limit == config.trial_daily_limit == 5
    assert config.daily_limit > config.trial_daily_limit  # original configuration is not modified


def test_paid_member_keeps_the_normal_daily_limit():
    config = AssistantConfig(ANTHROPIC_API_KEY="assistant-tests-not-real")
    with patch("app.member_entitlements.subscription_for_request", return_value={"mode": "STRIPE"}):
        assert limited_config(SimpleNamespace(), USER, config).daily_limit == config.daily_limit


def test_trial_limit_never_raises_a_lower_configured_daily_limit():
    config = AssistantConfig(ANTHROPIC_API_KEY="assistant-tests-not-real", daily_limit=3)
    with patch("app.member_entitlements.subscription_for_request", return_value={"mode": "FREE_TRIAL"}):
        assert limited_config(SimpleNamespace(), USER, config).daily_limit == 3
