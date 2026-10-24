"""Original V2 LIVE teminat modları (Güvenli %30 / Orta %50 / Agresif %65): hesap bazlı, onaylı, dürüst."""
import asyncio
import pathlib
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app import original_v2_live as original, v25_execution as live
from test_original_live_risk import _build, context, high_choice, save

SIZED = ("quantity", "margin_usdt", "notional_usdt", "stop_loss", "targets", "estimated_stop_loss_usdt", "original_risk_budget")


def _spec(mode=None, wallet=150):
    request, state, _ = context()
    save(request, state, high_choice())
    if mode:
        state["original_v2_live"].update(
            margin_mode=mode, margin_mode_acknowledged=mode != "guvenli", margin_mode_confirmed=mode == "agresif")
    snapshot = {"wallet_balance": wallet, "available_balance": wallet}
    return asyncio.run(original.build_spec(original_entry_client(), original_result(), state, snapshot)), state


def original_entry_client():
    from test_original_v2_demo_execution import EntryClient
    return EntryClient()


def original_result():
    from test_original_v2_demo_execution import result
    return result()


def test_mode_percents_are_30_50_65_with_total_80():
    assert original.margin_mode_percents() == {"guvenli": 30.0, "orta": 50.0, "agresif": 65.0}
    assert {original.margin_percent_limits(mode)[1] for mode in original.MARGIN_MODES} == {80.0}


def test_default_call_equals_guvenli_so_existing_behavior_is_unchanged():
    assert original.margin_percent_limits() == original.margin_percent_limits("guvenli") == (30.0, 80.0)


def test_total_cap_still_clamps_every_mode(monkeypatch):
    monkeypatch.setenv(original.TOTAL_MARGIN_ENV, "40")
    assert original.margin_mode_percents() == {"guvenli": 30.0, "orta": 40.0, "agresif": 40.0}


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        original.margin_percent_limits("kumar")


def test_account_without_a_mode_is_sized_exactly_like_guvenli():
    default, _ = _spec(None)
    guvenli, _ = _spec("guvenli")
    for key in SIZED:
        assert default[key] == guvenli[key], key


def test_higher_modes_only_change_size_never_stop_or_targets():
    base, _ = _spec("guvenli")
    for mode, cap in (("orta", 50), ("agresif", 65)):
        spec, _ = _spec(mode)
        assert spec["stop_loss"] == base["stop_loss"] and spec["targets"] == base["targets"]
        assert spec["margin_usdt"] >= base["margin_usdt"]
        assert spec["margin_usdt"] <= 150 * cap / 100 + 1e-9
        assert spec["margin_usdt"] <= min(150 * 0.80, 350 / 3) + 1e-9
        assert spec["estimated_stop_loss_usdt"] <= spec["original_risk_budget"] + 1e-8


def test_total_margin_cap_still_blocks_even_in_agresif():
    request, state, _ = context()
    save(request, state, high_choice())
    state["original_v2_live"].update(margin_mode="agresif", margin_mode_acknowledged=True, margin_mode_confirmed=True)
    state["plans"]["open-1"] = {"status": "KORUMA AKTİF", "margin_usdt": 790, "initial_risk_usdt": 1, "source": "MANUAL"}
    with pytest.raises(live.LiveExchangeError) as error:
        asyncio.run(original.build_spec(original_entry_client(), original_result(), state,
                                        {"wallet_balance": 1000, "available_balance": 1000}))
    assert error.value.check_key == "total_margin"


@pytest.mark.parametrize("fields", [
    {"margin_mode": "orta"},
    {"margin_mode": "agresif", "margin_mode_acknowledged": True},
    {"margin_mode": "agresif", "margin_mode_confirmed": True},
    {"margin_mode": "bozuk"},
])
def test_unconfirmed_or_corrupt_stored_mode_blocks_entry_instead_of_silently_downgrading(fields):
    request, state, _ = context()
    save(request, state, high_choice())
    state["original_v2_live"].update(fields)
    with pytest.raises(HTTPException) as error:
        original.execution_policy(state, {"wallet_balance": 1000})
    assert error.value.status_code == 423


def test_policy_uses_this_accounts_mode_only():
    request, state, _ = context()
    save(request, state, high_choice())
    guvenli = original.execution_policy(state, {"wallet_balance": 150})
    state["original_v2_live"].update(margin_mode="orta", margin_mode_acknowledged=True)
    orta = original.execution_policy(state, {"wallet_balance": 150})
    assert guvenli["max_margin_per_trade"] == pytest.approx(45) and orta["max_margin_per_trade"] == pytest.approx(75)
    # Toplam sınır her modda aynı: bakiyenin %80'i ve sert tavan (350 USDT maruziyet / 3x) arasında küçük olan.
    assert guvenli["max_total_margin_usdt"] == orta["max_total_margin_usdt"] == pytest.approx(min(120, 350 / 3))


def test_screen_limits_follow_the_selected_mode():
    from app import original_live_risk as risk
    request, state, _ = context()
    save(request, state, high_choice())
    state["snapshot"] = {"wallet_balance": 150}
    assert risk.limits(state)["max_margin_usdt"] == pytest.approx(45)
    state["original_v2_live"].update(margin_mode="agresif", margin_mode_acknowledged=True, margin_mode_confirmed=True)
    limits = risk.limits(state)
    assert limits["max_margin_usdt"] == pytest.approx(97.5) and limits["margin_mode"] == "agresif"
    assert limits["max_total_margin_usdt"] == pytest.approx(min(120, 350 / 3))


def _endpoint(monkeypatch, *, persist=None):
    request, state, _ = context()
    state["recovery_ready"] = True
    monkeypatch.setenv(original.FLAG, "true")
    monkeypatch.setattr(original.live_access, "require_live_member", Mock(return_value={"id": "owner", "role": "OWNER"}))
    monkeypatch.setattr(live, "execution_owner", Mock())
    understanding = AsyncMock()
    monkeypatch.setattr(live, "require_live_understanding", understanding)
    monkeypatch.setattr(live, "persist_state", Mock())
    monkeypatch.setattr(original, "persist_required", persist or AsyncMock())
    monkeypatch.setattr(original, "status", AsyncMock(side_effect=lambda req: original.public_config(state)))
    return request, state, understanding


def _post(request, **fields):
    return asyncio.run(original.update_margin_mode(request, original.MarginModeUpdate(understood=True, **fields)))


def test_orta_needs_acknowledgement_and_agresif_needs_the_phrase(monkeypatch):
    request, state, _ = _endpoint(monkeypatch)
    for fields in ({"mode": "orta"}, {"mode": "agresif", "acknowledged": True},
                   {"mode": "agresif", "acknowledged": True, "confirmation": "kabul"}, {"mode": "kumar", "acknowledged": True}):
        with pytest.raises(HTTPException) as error:
            _post(request, **fields)
        assert error.value.status_code == 422
    assert "margin_mode" not in state["original_v2_live"]


def test_owner_can_switch_modes_anytime_and_only_this_account_changes(monkeypatch):
    request, state, understanding = _endpoint(monkeypatch)
    other = {"original_v2_live": {"margin_mode": "guvenli"}}
    response = _post(request, mode="agresif", acknowledged=True, confirmation=original.AGGRESSIVE_MARGIN_CONFIRMATION)
    assert response["margin_mode"] == "agresif"
    config = state["original_v2_live"]
    assert config["margin_mode_acknowledged"] is True and config["margin_mode_confirmed"] is True
    assert other["original_v2_live"]["margin_mode"] == "guvenli"
    # Düşürmek onay istemez ve onay bayraklarını temizler.
    response = _post(request, mode="guvenli")
    assert response["margin_mode"] == "guvenli"
    assert config["margin_mode_acknowledged"] is False and config["margin_mode_confirmed"] is False
    kinds = [event["kind"] for event in state["events"]]
    assert kinds.count("ORIGINAL_MARGIN_MODE_CHANGED") == 2
    assert understanding.await_count == 2


def test_failed_durable_save_keeps_the_old_mode(monkeypatch):
    failing = AsyncMock(side_effect=live.LiveExchangeError("kayıt yok", http_status=503))
    request, state, _ = _endpoint(monkeypatch, persist=failing)
    state["original_v2_live"].update(margin_mode="orta", margin_mode_acknowledged=True)
    with pytest.raises(HTTPException) as error:
        _post(request, mode="agresif", acknowledged=True, confirmation=original.AGGRESSIVE_MARGIN_CONFIRMATION)
    assert error.value.status_code == 503
    config = state["original_v2_live"]
    assert config["margin_mode"] == "orta" and config["margin_mode_acknowledged"] is True
    assert not config.get("margin_mode_confirmed")


def test_other_users_cannot_change_this_accounts_mode(monkeypatch):
    request, state, _ = _endpoint(monkeypatch)
    state["original_v2_live"]["owner_id"] = "baska-hesap"
    with pytest.raises(HTTPException) as error:
        _post(request, mode="orta", acknowledged=True)
    assert error.value.status_code == 403
    assert "margin_mode" not in state["original_v2_live"]


def test_status_lists_all_three_modes_with_percentages():
    _, state, _ = context()
    config = original.public_config(state)
    assert config["margin_mode"] == "guvenli"
    assert [(row["mode"], row["per_trade_percent"], row["total_percent"]) for row in config["margin_modes"]] == [
        ("guvenli", 30.0, 80.0), ("orta", 50.0, 80.0), ("agresif", 65.0, 80.0)]


def test_unreached_target_is_announced_after_fill(monkeypatch):
    monkeypatch.setattr(original, "persist_required", AsyncMock())
    _, state, _ = context()
    plan = {
        "id": "p1", "symbol": "BTCUSDT", "direction": "LONG", "leverage": 3,
        "initial_quantity": "1", "quantity": "1", "initial_stop_loss": "98", "original_risk_budget": 10,
        "original_cost_bps": 16,
        "risk_adjustment": {"breakdown": {"reachable": False, "target_percent": 6.0, "effective_percent": 2.5,
                                          "unreached_reason": "işlem başı teminat sınırı"}},
    }
    asyncio.run(original.audit_fill(state, plan, {"entry_price": "100", "quantity": "1"}))
    notices = [event for event in state["events"] if event["kind"] == "ORIGINAL_RISK_TARGET_UNREACHED"]
    assert len(notices) == 1
    assert "%6.00" in notices[0]["message"] and "%2.50" in notices[0]["message"] and "teminat" in notices[0]["message"]


def test_reached_target_adds_no_notice(monkeypatch):
    monkeypatch.setattr(original, "persist_required", AsyncMock())
    _, state, _ = context()
    plan = {
        "id": "p2", "symbol": "BTCUSDT", "direction": "LONG", "leverage": 3,
        "initial_quantity": "1", "quantity": "1", "initial_stop_loss": "98", "original_risk_budget": 10,
        "original_cost_bps": 16, "risk_adjustment": {"breakdown": {"reachable": True}},
    }
    asyncio.run(original.audit_fill(state, plan, {"entry_price": "100", "quantity": "1"}))
    assert not [event for event in state["events"] if event["kind"] == "ORIGINAL_RISK_TARGET_UNREACHED"]


def test_demo_and_backtest_do_not_know_margin_modes():
    app_dir = pathlib.Path(original.__file__).parent
    users = sorted(path.name for path in app_dir.rglob("*.py")
                   if "margin_mode" in path.read_text(encoding="utf-8") or "MARGIN_MODES" in path.read_text(encoding="utf-8"))
    assert users == ["original_live_risk.py", "original_v2_live.py"]
