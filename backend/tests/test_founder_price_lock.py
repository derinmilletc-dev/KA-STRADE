import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from app import binance_manual_payments as payments

NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)


class Conn:
    def __init__(self, founder=None, last_end=None, table=True):
        self.founder, self.last_end, self.table, self.inserted = founder, last_end, table, None

    async def fetchval(self, sql, *args):
        if "to_regclass" in sql:
            return self.table
        if "clock_timestamp" in sql:
            return NOW
        if "MAX(p.period_end)" in sql:
            return self.last_end
        return None

    async def fetchrow(self, sql, *args):
        if "FROM founding_applications" in sql:
            return self.founder
        if "INSERT INTO binance_manual_payment_requests" in sql:
            self.inserted = args[3]
            return {"id": "a" * 32, "user_id": args[1], "reference_code": "KT-ABCDEF", "amount": args[3], "currency": "USDT",
                    "method": "binance_manual", "status": "created", "transaction_number": None, "created_at": NOW,
                    "expires_at": NOW + timedelta(hours=48), "submitted_at": None, "decided_by": None, "decided_at": None,
                    "decision_note": None, "version": 1}
        return None

    async def execute(self, sql, *args):
        return "OK"

    async def fetch(self, sql, *args):
        return []

    @asynccontextmanager
    async def transaction(self):
        yield


def lock(conn):
    return asyncio.run(payments.founder_price_lock(conn, "u1"))


def test_non_founder_has_no_lock():
    assert lock(Conn(founder=None)) is None
    assert lock(Conn(table=False)) is None


def test_first_payment_and_active_member_within_12_months_are_locked():
    assert lock(Conn(founder={"status": "payment_pending", "activated_at": None})) == NOW + timedelta(days=365)
    activated = NOW - timedelta(days=100)
    assert lock(Conn(founder={"status": "active", "activated_at": activated}, last_end=NOW + timedelta(days=5))) == activated + timedelta(days=365)


def test_lock_ends_after_12_months_or_after_a_lapse():
    assert lock(Conn(founder={"status": "active", "activated_at": NOW - timedelta(days=366)}, last_end=NOW)) is None
    lapsed = NOW - timedelta(days=payments.FOUNDER_GRACE_DAYS + 1)
    assert lock(Conn(founder={"status": "active", "activated_at": NOW - timedelta(days=60)}, last_end=lapsed)) is None
    within_grace = NOW - timedelta(days=payments.FOUNDER_GRACE_DAYS - 1)
    assert lock(Conn(founder={"status": "active", "activated_at": NOW - timedelta(days=60)}, last_end=within_grace)) is not None


def test_founder_keeps_old_price_when_master_price_rises():
    raised = {**payments.PLAN_AMOUNTS, "MASTER_MODE": Decimal("399.90")}
    async def noop(*_a, **_k):
        return None
    @asynccontextmanager
    async def no_storage(_conn):
        yield
    with patch.dict(payments.PLAN_AMOUNTS, raised), patch.object(payments, "ensure_no_paid_stripe", noop), \
            patch.object(payments, "expire_due", noop), patch.object(payments, "lock_user", noop), patch.object(payments, "storage", no_storage):
        founder = Conn(founder={"status": "active", "activated_at": NOW - timedelta(days=30)}, last_end=NOW)
        asyncio.run(payments.create_request(founder, "u1", "MASTER_MODE"))
        regular = Conn(founder=None)
        asyncio.run(payments.create_request(regular, "u2", "MASTER_MODE"))
        assert payments.plan_for_amount(Decimal("119.90")) == "MASTER_MODE"
    assert founder.inserted == Decimal("119.90") and regular.inserted == Decimal("399.90")
