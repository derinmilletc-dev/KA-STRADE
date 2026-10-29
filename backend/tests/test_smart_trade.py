"""Smart Trade (40 USDT, yalnız elle işlem): plan tutardan çıkar, otomasyon kapalı, gerçek emir açık."""
import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from app import binance_manual_payments as payments
from app import live_access
from app import member_entitlements as entitlements
from app.premium_access import AUTOMATION_LOCKED_MESSAGE, SMART_TRADE_AUTOMATION_PATHS, automation_locked
from fastapi import HTTPException
from test_binance_manual_entitlements import EntitlementConnection, decide
from test_binance_manual_payments import Connection

MIGRATION = Path(__file__).parents[1] / "migrations" / "20261029_001_smart_trade_amount.sql"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("BINANCE_MANUAL_ACCESS_ENABLED", "true")


def test_plan_comes_only_from_the_exact_known_amounts():
    assert payments.plan_for_amount(Decimal("119.90")) == "MASTER_MODE"
    assert payments.plan_for_amount("119.90") == "MASTER_MODE"
    assert payments.plan_for_amount(Decimal("40.00")) == "SMART_TRADE"
    assert payments.plan_for_amount(Decimal("40")) == "SMART_TRADE"
    for bad in (None, "", "abc", Decimal("0"), Decimal("39.99"), Decimal("40.01"), Decimal("119.91"), Decimal("159.90")):
        assert payments.plan_for_amount(bad) is None


def test_create_request_charges_the_plan_amount_and_rejects_unknown_plans():
    async def run():
        master = await payments.create_request(Connection(), "user-a")
        assert master.amount == Decimal("119.90") and master.plan == "MASTER_MODE"
        smart = await payments.create_request(Connection(), "user-a", "SMART_TRADE")
        assert smart.amount == Decimal("40.00") and smart.plan == "SMART_TRADE"
        with pytest.raises(HTTPException) as error:
            await payments.create_request(Connection(), "user-a", "FREE")
        assert error.value.status_code == 422
    asyncio.run(run())


async def approved_smart_trade(conn, user="user-a"):
    row = await payments.create_request(conn, user, "SMART_TRADE")
    row = await payments.submit_transaction(conn, user, row.id, "smart-number")
    await decide(conn, row)
    return row


def test_approved_smart_trade_gives_manual_trading_but_never_automation():
    conn = EntitlementConnection()

    async def run():
        row = await approved_smart_trade(conn)
        period = conn.periods[row.id]
        assert (period["period_end"] - period["period_start"]).days == 30
        subscription = await entitlements.resolve(conn, {"subscriptions": []}, "user-a")
        assert subscription["plan"] == "SMART_TRADE" and subscription["master_trade_access"] is True
        assert subscription["mode"] == "BINANCE_MANUAL" and subscription["status"] == "ACTIVE"
        assert subscription["entitlements"]["canUseAutomation"] is False
        rights = entitlements.access(subscription, {"id": "user-a", "role": "CUSTOMER"})
        assert rights["isPremium"] and rights["canExecuteMasterTrade"]
        assert rights["entitlements"]["canUseAutomation"] is False
        assert live_access.entitled(subscription) is True  # gerçek emir (elle) açık
    asyncio.run(run())


def test_master_mode_and_owner_keep_automation():
    conn = EntitlementConnection()

    async def run():
        row = await payments.create_request(conn, "user-a")
        row = await payments.submit_transaction(conn, "user-a", row.id, "master-number")
        await decide(conn, row)
        subscription = await entitlements.resolve(conn, {"subscriptions": []}, "user-a")
        assert subscription["plan"] == "MASTER_MODE"
        assert entitlements.access(subscription, {"id": "user-a", "role": "CUSTOMER"})["entitlements"]["canUseAutomation"] is True
        owner = entitlements.access(subscription, {"id": "owner", "role": "OWNER"})
        assert owner["entitlements"]["canUseAutomation"] is True
    asyncio.run(run())


def test_unknown_amount_in_an_approved_record_is_an_error_not_access():
    conn = EntitlementConnection()

    async def run():
        row = await approved_smart_trade(conn)
        conn.rows[row.id]["amount"] = Decimal("12.34")
        with pytest.raises(HTTPException) as error:
            await entitlements.resolve(conn, {"subscriptions": []}, "user-a")
        assert error.value.status_code == 503
    asyncio.run(run())


def test_automation_start_paths_are_locked_but_stop_and_manual_paths_are_not():
    for path in ("/api/v25/auto/start", "/api/v25/original-v2/start", "/api/v25/original-v2/extend-session",
                 "/api/paper/bot/start", "/api/v7/orchestrator/start"):
        assert path in SMART_TRADE_AUTOMATION_PATHS and automation_locked(path, "POST")
        assert not automation_locked(path, "GET")
    for path in ("/api/v25/auto/stop", "/api/v25/original-v2/stop", "/api/v25/disarm", "/api/v25/emergency",
                 "/api/v25/order", "/api/v25/arm", "/api/v25/grid/live/start", "/api/v25/position/close"):
        assert not automation_locked(path, "POST")
    assert "MASTER MODE" in AUTOMATION_LOCKED_MESSAGE


def test_migration_allows_exactly_the_two_amounts_and_is_atomic():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.lstrip().splitlines()[0].startswith("--") and "BEGIN;" in sql and "COMMIT;" in sql
    assert "CHECK (amount IN (119.90, 40.00))" in sql and "binance_manual_amount_plans" in sql
    assert "DELETE" not in sql.upper().replace("DROP CONSTRAINT", "")
    rollback = (MIGRATION.parents[0] / "rollback" / "20261029_smart_trade_amount.sql").read_text(encoding="utf-8")
    assert "CHECK (amount = 119.90)" in rollback
