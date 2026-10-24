"""Hedef / uygulanabilir maksimum / gerçekleşecek risk ayrımı: saf hesap, boyutlandırmayı değiştirmez."""
from decimal import Decimal

from app import original_risk_capacity as capacity


def breakdown(**overrides):
    base = dict(
        wallet=144.78, selected_percent=6.0, budget_loss=144.78 * 0.06,
        entry=Decimal("100"), stop=Decimal("94"), cost_per_unit=Decimal("0.16"), leverage=3,
        margin_cap_usdt=144.78 * 0.30, total_margin_room_usdt=144.78 * 0.80,
        available_balance=144.78, fee_rate=0.0005, max_quantity=Decimal("1000000"),
        quantity_step=Decimal("0.1"), effective_loss=8.0,
    )
    base.update(overrides)
    return capacity.trade_risk_breakdown(**base)


def test_selected_risk_above_what_margin_cap_allows_is_reported_unreachable():
    result = breakdown()
    # %30 teminat x 3 kaldıraç = 130.3 USDT pozisyon; 1.3 adet x (6 + 0.16) = 8.008 USDT
    assert result["status"] == "verified"
    assert abs(result["target_loss_usdt"] - 8.6868) < 1e-6
    assert abs(result["max_applicable_loss_usdt"] - 8.008) < 1e-6
    assert result["reachable"] is False
    assert result["limiting_factor"] == "işlem başı teminat sınırı"
    assert result["unreached_reason"] == "işlem başı teminat sınırı"
    assert result["effective_loss_usdt"] == 8.0  # gerçekleşecek değer hedef gibi gösterilmez


def test_close_stop_makes_the_same_selection_far_less_reachable():
    near = breakdown(stop=Decimal("98"), effective_loss=2.8)
    assert abs(near["max_applicable_loss_usdt"] - 1.3 * 2.16) < 1e-6
    assert near["reachable"] is False
    assert near["max_applicable_percent"] < 2.5


def test_small_selection_is_reachable():
    result = breakdown(selected_percent=1.0, budget_loss=1.4478, effective_loss=1.4)
    assert result["reachable"] is True
    assert result["unreached_reason"] is None


def test_open_positions_shrink_the_applicable_maximum():
    free = breakdown(selected_percent=1.0, budget_loss=1.4478)
    crowded = breakdown(selected_percent=1.0, budget_loss=1.4478, total_margin_room_usdt=5.0)
    assert crowded["max_applicable_loss_usdt"] < free["max_applicable_loss_usdt"]
    assert crowded["limiting_factor"] == "toplam teminatın kalan payı"
    assert crowded["reachable"] is False


def test_available_balance_is_a_limit_and_unknown_balance_is_flagged_not_assumed():
    low = breakdown(available_balance=10.0)
    assert low["limiting_factor"] == "kullanılabilir bakiye"
    assert low["max_applicable_loss_usdt"] < breakdown()["max_applicable_loss_usdt"]
    unknown = breakdown(available_balance=None)
    assert unknown["status"] == "partial"
    assert unknown["unverified"] == ["kullanılabilir bakiye"]


def test_system_ceiling_below_selection_is_named_when_capacity_is_larger():
    result = breakdown(selected_percent=10.0, budget_loss=3.0, margin_cap_usdt=100.0, total_margin_room_usdt=100.0, available_balance=1000)
    assert result["reachable"] is False
    assert result["unreached_reason"] == "sistem güvenlik tavanı"


def test_exchange_max_quantity_and_step_floor():
    result = breakdown(max_quantity=Decimal("0.5"))
    assert result["limiting_factor"] == "Binance üst miktar sınırı"
    assert abs(result["max_applicable_loss_usdt"] - 0.5 * 6.16) < 1e-6


def test_honest_labels_state_what_is_not_included():
    result = breakdown()
    assert "stop_kayma_boşluk" in result["excludes"] and "fonlama" in result["excludes"]
    assert result["liquidation_checked_separately"] is True


def test_bad_inputs_report_unavailable_and_never_raise():
    assert breakdown(entry=Decimal("0"))["status"] == "unavailable"
    assert breakdown(wallet=0)["status"] == "unavailable"
    assert breakdown(stop=Decimal("100"), cost_per_unit=Decimal("0"))["status"] == "unavailable"
    assert breakdown(quantity_step="x")["status"] == "unavailable"


def test_capacity_preview_matches_the_hand_calculation():
    preview = capacity.capacity_preview(wallet=144.78, max_margin_usdt=43.434, leverage=3, cost_pct=0.16)
    assert abs(preview["max_notional_usdt"] - 130.302) < 1e-6
    six = preview["by_stop_distance"][-1]
    assert six["stop_pct"] == 6.0
    assert abs(six["max_loss_usdt"] - 130.302 * 6.16 / 100) < 1e-6
    assert six["max_percent"] < 6
    assert capacity.capacity_preview(wallet=0, max_margin_usdt=1, leverage=3, cost_pct=0.16) is None
