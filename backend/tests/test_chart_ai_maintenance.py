import asyncio
import json
from unittest.mock import patch

from app import chart_ai_maintenance as maintenance
from app.email_service import EmailDeliveryError


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class Connection:
    def __init__(self, *, outbox=None, recipient=None, recovered=0):
        self.outbox = outbox
        self.recipient = recipient
        self.recovered = recovered
        self.queries = []
        self.parameters = []

    def transaction(self):
        return Transaction()

    async def fetchrow(self, query, *_):
        if "SELECT id,recipient_user_id,payload,attempts" in query:
            row, self.outbox = self.outbox, None
            return row
        if "SELECT security->>'email' AS email" in query:
            return self.recipient
        raise AssertionError(query)

    async def execute(self, query, *parameters):
        self.queries.append(query)
        self.parameters.append(parameters)
        if "WITH expired AS" in query:
            return f"UPDATE {self.recovered}"
        return "UPDATE 1"


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return None


class Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self, *, timeout):
        assert timeout == 3
        return Acquire(self.connection)


def outbox(payload=None, *, attempts=0):
    return {
        "id": "notification-fixture",
        "recipient_user_id": "owner-fixture",
        "payload": json.dumps(payload or {
            "month": "2026-10", "spent_microusd": 20_000_000, "budget_microusd": 25_000_000,
        }),
        "attempts": attempts,
    }


def test_budget_warning_delivery_marks_only_the_chart_ai_notice_sent(monkeypatch):
    connection = Connection(outbox=outbox(), recipient={"email": "owner@example.test"})
    monkeypatch.setattr(maintenance.email_service, "configured", lambda: True)
    with patch.object(maintenance.email_service, "send_chart_ai_budget_warning_email") as send:
        processed = asyncio.run(maintenance.process_budget_notifications(Pool(connection), limit=1))
    assert processed == 1
    send.assert_called_once_with(
        to_email="owner@example.test", month="2026-10",
        spent_microusd=20_000_000, budget_microusd=25_000_000,
        idempotency_key="notification-fixture",
    )
    assert any("status='sent'" in query for query in connection.queries)
    assert "owner@example.test" not in str(connection.queries)


def test_delivery_failures_are_safely_retried_without_logging_recipient(monkeypatch, caplog):
    connection = Connection(outbox=outbox(), recipient={"email": "owner@example.test"})
    monkeypatch.setattr(maintenance.email_service, "configured", lambda: True)
    with patch.object(
        maintenance.email_service, "send_chart_ai_budget_warning_email",
        side_effect=EmailDeliveryError("private transport detail", provider="smtp", code="timeout"),
    ):
        assert asyncio.run(maintenance.process_budget_notifications(Pool(connection), limit=1)) == 1
    failure_index = next(index for index, query in enumerate(connection.queries)
                         if "SET status=$3,last_error_code=$4" in query)
    assert connection.parameters[failure_index][2:4] == ("failed", "provider_timeout")
    assert "owner@example.test" not in caplog.text
    assert "private transport detail" not in caplog.text


def test_invalid_payload_is_dead_lettered_without_sending(monkeypatch):
    connection = Connection(outbox=outbox({"month": "2026-99", "spent_microusd": 20, "budget_microusd": 25}),
                            recipient={"email": "owner@example.test"})
    monkeypatch.setattr(maintenance.email_service, "configured", lambda: True)
    with patch.object(maintenance.email_service, "send_chart_ai_budget_warning_email") as send:
        assert asyncio.run(maintenance.process_budget_notifications(Pool(connection), limit=1)) == 1
    send.assert_not_called()
    assert any("status='dead',last_error_code='invalid_response'" in query for query in connection.queries)


def test_unavailable_owner_is_not_sent_mail(monkeypatch):
    connection = Connection(outbox=outbox(), recipient=None)
    monkeypatch.setattr(maintenance.email_service, "configured", lambda: True)
    with patch.object(maintenance.email_service, "send_chart_ai_budget_warning_email") as send:
        assert asyncio.run(maintenance.process_budget_notifications(Pool(connection), limit=1)) == 1
    send.assert_not_called()
    assert any("status='dead',last_error_code='recipient_unavailable'" in query for query in connection.queries)


def test_expired_delivery_lease_is_recovered_with_a_bounded_retry_count():
    connection = Connection(recovered=2)
    assert asyncio.run(maintenance._recover_expired_notifications(Pool(connection))) == 2
    assert "status='sending'" in connection.queries[0]
    assert "status=CASE WHEN e.attempts >= $2 THEN 'dead' ELSE 'pending' END" in connection.queries[0]


def test_missing_email_provider_does_not_consume_a_notification_attempt(monkeypatch):
    connection = Connection(outbox=outbox(), recipient={"email": "owner@example.test"})
    monkeypatch.setattr(maintenance.email_service, "configured", lambda: False)
    assert asyncio.run(maintenance.process_budget_notifications(Pool(connection), limit=1)) == 0
    assert len(connection.queries) == 1
    assert "WITH expired AS" in connection.queries[0]
