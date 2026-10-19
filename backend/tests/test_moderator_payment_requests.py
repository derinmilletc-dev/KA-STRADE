"""Offline tests for the read-only moderator payment-request queue (fake pool, no network)."""
import copy
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.moderator_payment_requests import router
from test_moderator_customers import CustomerReadPool
from test_moderator_role import headers, setup  # noqa: F401  (fixture re-export)

PATH = "/api/mod/payment-requests"
FULL_TRANSACTION = "BINANCE-ORDER-0001234567"


def request_row(request_id, user_id, status="pending", number=FULL_TRANSACTION):
    now = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
    return {
        "id": request_id, "user_id": user_id, "reference_code": "KT-BC2345", "amount": Decimal("119.90"),
        "currency": "USDT", "status": status, "transaction_number": number, "created_at": now,
        "submitted_at": now, "decided_at": None, "decision_note": None, "erased_at": None,
        "user_active": True, "email_verified": True, "mfa_enabled": False, "registered_at": now,
        "plan": "MASTER_MODE", "subscription_status": "ACTIVE", "plan_period_end": None,
        "membership_end": datetime(2026, 11, 10, tzinfo=timezone.utc),
        "approved_count": 1, "rejected_count": 0, "expired_count": 2, "recommended_at": None,
    }


class QueuePool(CustomerReadPool):
    def __init__(self, users):
        super().__init__(users)
        self.requests = []
        self.recommendations = {}

    def selected(self, actor, status):
        return [row for row in self.requests if row["user_id"] in self.visible(actor)
                and row["erased_at"] is None and (status is None or row["status"] == status)]

    async def fetch(self, sql, *args):
        if "FROM binance_manual_payment_requests r" in sql and "ORDER BY r.created_at" in sql:
            self.check(sql)
            actor, status, limit, offset = args
            rows = self.selected(actor, status)[offset:offset + limit]
            return [{**copy.deepcopy(row), "email": self.users[row["user_id"]]["security"]["email"],
                     "recommended_at": self.recommendations.get(row["id"], {}).get("created_at")} for row in rows]
        return await super().fetch(sql, *args)

    async def fetchval(self, sql, *args):
        if "COUNT(*) FROM binance_manual_payment_requests r" in sql:
            self.check(sql)
            return len(self.selected(*args))
        return await super().fetchval(sql, *args)

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE OF r" in sql:
            self.check(sql)
            match = [row for row in self.selected(args[0], None) if row["id"] == args[1]]
            return {"id": match[0]["id"], "status": match[0]["status"]} if match else None
        if "SELECT created_at FROM binance_manual_payment_recommendations" in sql:
            self.check(sql)
            return self.recommendations.get(args[0])
        if "INSERT INTO binance_manual_payment_recommendations" in sql:
            self.check(sql)
            assert args[0] not in self.recommendations
            self.recommendations[args[0]] = {"created_at": datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc),
                                             "recommended_by": args[1], "note": args[2]}
            return {"created_at": self.recommendations[args[0]]["created_at"]}
        if "FILTER (WHERE r.status = 'pending')" in sql:
            self.check(sql)
            rows = self.selected(args[0], None)
            return {name: sum(row["status"] == name for row in rows) for name in ("pending", "approved", "rejected")}
        return await super().fetchrow(sql, *args)


@pytest.fixture
def queue(setup):
    client, _ = setup
    users = client.app.state.v22_commercial["state"]["users"]
    pool = QueuePool(users)
    pool.settings["moderator"] = {"two_factor_enabled": True}
    pool.permissions["moderator"] = {"payments.view": {}, "approvals.create": {}}
    pool.requests = [request_row("a" * 32, "customer"), request_row("b" * 32, "customer", "approved", "OTHER-ORDER-9999")]
    client.app.state.db_pool = pool
    client.app.include_router(router)
    yield client, pool


@pytest.mark.parametrize("actor,role,status", [("moderator", "MODERATOR", 200), ("owner", "OWNER", 200), ("customer", "CUSTOMER", 403)])
def test_required_roles(queue, actor, role, status):
    client, _ = queue
    assert client.get(PATH, headers=headers(actor, role)).status_code == status


def test_missing_permission_and_disabled_mfa_fail_closed(queue):
    client, pool = queue
    h = headers("moderator", "MODERATOR")
    del pool.permissions["moderator"]["payments.view"]
    assert client.get(PATH, headers=h).status_code == 403
    pool.permissions["moderator"]["payments.view"] = {}
    pool.settings["moderator"]["two_factor_enabled"] = False
    response = client.get(PATH, headers=h)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "mfa_required"


def test_queue_masks_identity_and_transaction_number_and_exposes_account_facts(queue):
    client, _ = queue
    response = client.get(PATH, headers=headers("moderator", "MODERATOR"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2 and body["summary"] == {"pending": 1, "approved": 1, "rejected": 0}
    first = body["items"][0]
    assert first["transaction_hint"] == "••••4567" and first["amount"] == "119.90"
    assert first["customer"]["email_masked"] == "c***@example.test"
    assert first["customer"]["plan"] == "MASTER_MODE" and first["customer"]["approved_count"] == 1
    assert first["customer"]["membership_end"].startswith("2026-11-10")
    assert FULL_TRANSACTION not in response.text and "customer@example.test" not in response.text


def test_status_filter_and_paging_limits(queue):
    client, _ = queue
    h = headers("moderator", "MODERATOR")
    only = client.get(PATH, headers=h, params={"status": "approved"}).json()
    assert [item["status"] for item in only["items"]] == ["approved"]
    for params in ({"limit": 51}, {"limit": 0}, {"offset": -1}, {"status": "bogus"}):
        assert client.get(PATH, headers=h, params=params).status_code == 422


def test_queue_is_read_only_and_each_viewed_customer_is_audited_once(queue):
    client, pool = queue
    h = headers("moderator", "MODERATOR")
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(PATH, headers=h).status_code in (401, 403, 404, 405)
    assert client.get(PATH, headers=h).status_code == 200
    viewed = [audit for audit in pool.audits if audit["action"] == "customer.payments.viewed"]
    assert [audit["target_id"] for audit in viewed] == ["customer"]
    assert all(audit["actor_user_id"] == "moderator" for audit in viewed)


def test_erased_requests_and_non_customer_accounts_are_not_listed(queue):
    client, pool = queue
    pool.requests[0]["erased_at"] = datetime(2026, 10, 11, tzinfo=timezone.utc)
    pool.requests.append(request_row("c" * 32, "owner", number="OWNER-ORDER-1"))
    body = client.get(PATH, headers=headers("moderator", "MODERATOR")).json()
    assert [item["id"] for item in body["items"]] == ["b" * 32]


RECOMMEND = PATH + "/" + "a" * 32 + "/recommend"


def test_recommend_sends_pending_request_to_owner_without_changing_status_or_granting(queue):
    client, pool = queue
    h = headers("moderator", "MODERATOR")
    response = client.post(RECOMMEND, headers=h, json={"note": "  Ödeme Binance'te göründü.  "})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "a" * 32
    assert pool.recommendations["a" * 32]["note"] == "Ödeme Binance'te göründü."
    assert pool.recommendations["a" * 32]["recommended_by"] == "moderator"
    assert [row["status"] for row in pool.requests] == ["pending", "approved"]
    recommended = [audit for audit in pool.audits if audit["action"] == "payment.binance.recommended"]
    assert len(recommended) == 1 and recommended[0]["target_type"] == "BINANCE_PAYMENT" and recommended[0]["actor_role"] == "MODERATOR"
    listed = client.get(PATH, headers=h).json()["items"][0]
    assert listed["recommended_at"] is not None and listed["status"] == "pending"


def test_recommend_is_idempotent_and_blank_note_is_dropped(queue):
    client, pool = queue
    h = headers("moderator", "MODERATOR")
    first = client.post(RECOMMEND, headers=h, json={"note": "   "})
    second = client.post(RECOMMEND, headers=h, json={"note": "other"})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert pool.recommendations["a" * 32]["note"] is None
    assert len([audit for audit in pool.audits if audit["action"] == "payment.binance.recommended"]) == 1


def test_recommend_rejects_decided_missing_and_oversized_requests(queue):
    client, _ = queue
    h = headers("moderator", "MODERATOR")
    assert client.post(PATH + "/" + "b" * 32 + "/recommend", headers=h, json={}).status_code == 409
    assert client.post(PATH + "/" + "d" * 32 + "/recommend", headers=h, json={}).status_code == 404
    assert client.post(RECOMMEND, headers=h, json={"note": "x" * 501}).status_code == 422
    assert client.post(RECOMMEND, headers=h, json={"unknown": 1}).status_code == 422


def test_recommend_requires_approvals_create_mfa_and_a_moderator_or_owner(queue):
    client, pool = queue
    assert client.post(RECOMMEND, headers=headers("customer", "CUSTOMER"), json={}).status_code == 403
    h = headers("moderator", "MODERATOR")
    del pool.permissions["moderator"]["approvals.create"]
    assert client.post(RECOMMEND, headers=h, json={}).status_code == 403
    pool.permissions["moderator"]["approvals.create"] = {}
    pool.settings["moderator"]["two_factor_enabled"] = False
    response = client.post(RECOMMEND, headers=h, json={})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "mfa_required"
    assert pool.recommendations == {} and pool.audits == []
