"""KAIS AI erişimi: ücretli üyelik açıkken planı olmayan üye kullanamaz; deneme, ücretli plan ve sahip kullanır."""
import pytest

from app import member_entitlements as entitlements

CUSTOMER = {"id": "u", "role": "CUSTOMER"}


@pytest.fixture(autouse=True)
def paid_model(monkeypatch):
    monkeypatch.setenv("BINANCE_MANUAL_ACCESS_ENABLED", "true")
    monkeypatch.setenv("FREE_TRIAL_ENABLED", "false")


def test_member_without_plan_cannot_use_kais():
    assert entitlements.access({"master_trade_access": False}, CUSTOMER)["assistantAllowed"] is False


@pytest.mark.parametrize("plan", ["SMART_TRADE", "MASTER_MODE"])
def test_paid_plans_use_kais(plan):
    subscription = {"master_trade_access": True, "plan": plan, "status": "ACTIVE"}
    assert entitlements.access(subscription, CUSTOMER)["assistantAllowed"] is True


def test_active_trial_uses_kais_and_expired_trial_does_not():
    active = {"master_trade_access": True, "plan": "TRIAL", "mode": "FREE_TRIAL", "status": "TRIALING"}
    expired = {"master_trade_access": False, "plan": "TRIAL", "mode": "FREE_TRIAL", "status": "EXPIRED"}
    assert entitlements.access(active, CUSTOMER)["assistantAllowed"] is True
    assert entitlements.access(expired, CUSTOMER)["assistantAllowed"] is False


def test_owner_always_uses_kais():
    assert entitlements.access({"master_trade_access": False}, {"id": "o", "role": "OWNER"})["assistantAllowed"] is True
