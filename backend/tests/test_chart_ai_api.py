"""Canonical auth/entitlement middleware with offline quota and account stores."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app import chart_ai_api, main
from app.commercial_core import default_commercial_state, issue_token
from app.chart_ai_models import NOTICE
from app.chart_ai_store import POLICY_VERSION, ChartAiStore, user_hash
from test_chart_ai_store import FakePool

USAGE = "/api/chart-ai/usage"
ACK = "/api/chart-ai/acknowledgement"
BODY = {"policy_version": POLICY_VERSION, "accepted": True}
SECRET = b"chart-ai-offline-auth-fixture-secret"


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    monkeypatch.setenv("CHART_AI_ENABLED", "true")
    monkeypatch.setenv("CHART_AI_OWNER_ONLY", "false")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def chart_api(monkeypatch):
    application = FastAPI()
    application.middleware("http")(main.owner_preview_gate)
    application.include_router(chart_ai_api.router)
    state = default_commercial_state()
    state["users"] = [
        {"id": "customer", "role": "CUSTOMER", "active": True, "email_verified": True, "auth_version": 1},
        {"id": "owner", "role": "OWNER", "active": True, "email_verified": True, "auth_version": 1},
        {"id": "moderator", "role": "MODERATOR", "active": True, "email_verified": True, "auth_version": 1},
    ]
    application.state.v22_commercial = {"state": state, "secret": SECRET, "lock": asyncio.Lock()}
    application.state.db_pool = None
    ledger_pool = FakePool()
    ledger_pool.now = datetime.now(timezone.utc)
    ledger = ChartAiStore(ledger_pool)
    application.state.chart_ai_store = ledger
    client = TestClient(application, base_url="https://chart-ai.example.test")
    monkeypatch.setattr(chart_ai_api, "store", lambda _request: ledger)
    monkeypatch.setattr(main, "WEB_REQUIRE_AUTH", False)
    monkeypatch.setattr(main, "WEB_ACCESS_TOKEN", "")
    monkeypatch.setattr(main, "hydrate_authenticated_user_state", AsyncMock())
    monkeypatch.setenv("PROTREBOT_DURABLE_AUTH_REQUIRED", "false")
    return client, state, ledger_pool


def headers(user="customer", role=None, version=1):
    role = role or {"owner": "OWNER", "moderator": "MODERATOR"}.get(user, "CUSTOMER")
    return {"Authorization": "Bearer " + issue_token(user, role, SECRET, token_version=version)}


def start_trial(client, pool):
    start = pool.now
    end = start + timedelta(days=7)
    client.app.state.v22_commercial["state"]["subscriptions"] = [{
        "user_id": "customer", "plan": "TRIAL", "status": "TRIALING",
        "trial_start": start.isoformat(), "trial_end": end.isoformat(),
        "current_period_end": end.isoformat(),
    }]


def subscribe(client, *, status="ACTIVE", expired=False, grace=None, plan="MASTER_MODE"):
    now = datetime.now(timezone.utc)
    row = {"user_id": "customer", "plan": plan, "status": status,
           "current_period_end": (now + timedelta(days=-1 if expired else 30)).isoformat()}
    if grace is not None:
        row["grace_until"] = (now + timedelta(days=grace)).isoformat()
    client.app.state.v22_commercial["state"]["subscriptions"] = [row]


@pytest.mark.parametrize("value", [None, "", "false", "0"])
def test_feature_is_default_off_and_never_reads_chart_storage(chart_api, monkeypatch, value):
    client, _, pool = chart_api
    if value is None:
        monkeypatch.delenv("CHART_AI_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CHART_AI_ENABLED", value)
    for method, path in (("GET", USAGE), ("POST", ACK)):
        response = client.request(method, path, headers=headers("owner", "OWNER"), json=BODY if method == "POST" else None)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "feature_disabled"
    assert pool.queries == []
    main.hydrate_authenticated_user_state.assert_not_awaited()


def test_invalid_feature_setting_is_explicit_and_not_logged_raw(chart_api, monkeypatch, caplog):
    client, _, pool = chart_api
    monkeypatch.setenv("CHART_AI_ENABLED", "private-invalid-setting")
    response = client.get(USAGE, headers=headers("owner", "OWNER"))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "configuration_invalid"
    assert "private-invalid-setting" not in caplog.text
    assert pool.queries == []


def test_active_trial_has_fifteen_daily_rights_and_exact_warning(chart_api):
    client, _, pool = chart_api
    start_trial(client, pool)
    response = client.get(USAGE, headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["eligible"] and payload["access_type"] == "trial"
    assert payload["quota"] == {"limit": 15, "used": 0, "reserved": 0, "remaining": 15, "unlimited": False}
    assert payload["resets_at"] == payload["period_end"]
    assert payload["trial_expires_at"] is not None and payload["trial_expires_at"] != payload["period_end"]
    assert payload["acknowledgement"] == {
        "policy_version": POLICY_VERSION, "accepted_at": None, "required": True, "text": NOTICE,
    }
    assert "Resmin sitemizde saklanmaz; analiz için yapay zeka sağlayıcımıza gönderilir." in NOTICE
    assert "Bu analiz yatırım tavsiyesi değildir." in NOTICE
    assert payload["analysis_enabled"] is False and payload["availability"]["available"] is False
    assert response.headers["cache-control"] == "no-store"
    assert pool.state.requests == {} and pool.state.budgets == {}
    main.hydrate_authenticated_user_state.assert_not_awaited()


def test_acknowledgement_is_persistent_idempotent_user_scoped_and_does_not_spend(chart_api):
    client, _, pool = chart_api
    start_trial(client, pool)
    auth = headers("customer", "CUSTOMER")
    first = client.post(ACK, headers=auth, json=BODY)
    assert first.status_code == 200, first.text
    assert first.json()["quota_consumed"] is False
    assert first.json()["acknowledgement"]["required"] is False
    assert first.json()["acknowledgement"]["text"] == NOTICE
    pool.now += timedelta(seconds=1)
    again = client.post(ACK, headers=auth, json=BODY)
    assert again.json() == first.json()
    usage = client.get(USAGE, headers=auth).json()
    assert usage["acknowledgement"] == first.json()["acknowledgement"]
    assert usage["quota"]["remaining"] == 15
    other = client.get(USAGE, headers=headers("owner", "OWNER")).json()
    assert other["acknowledgement"]["accepted_at"] is None
    assert len(pool.state.acknowledgements) == 1
    assert (user_hash("customer"), POLICY_VERSION) in pool.state.acknowledgements
    assert pool.state.requests == {} and pool.state.budgets == {}


@pytest.mark.parametrize("change,status", [
    ({"accepted": False}, 422),
    ({"accepted": "true"}, 422),
    ({"accepted": 1}, 422),
    ({"policy_version": "old-version"}, 403),
    ({"policy_version": "x" * 65}, 422),
    ({"user_id": "owner"}, 422),
    ({"isPremium": True}, 422),
    ({"role": "OWNER"}, 422),
])
def test_invalid_or_forged_acknowledgement_never_writes(chart_api, change, status):
    client, _, pool = chart_api
    start_trial(client, pool)
    response = client.post(ACK, headers=headers("customer", "CUSTOMER"), json={**BODY, **change})
    assert response.status_code == status, response.text
    assert pool.state.acknowledgements == {} and pool.state.requests == {}


def test_paid_master_mode_uses_twenty_rights_and_utc_monday_period(chart_api):
    client, _, pool = chart_api
    subscribe(client)
    payload = client.get(USAGE, headers=headers("customer", "CUSTOMER")).json()
    assert payload["access_type"] == "subscriber"
    assert payload["quota"]["remaining"] == payload["quota"]["limit"] == 20
    start = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
    assert start.weekday() == 0 and start.hour == start.minute == start.second == 0
    assert start.utcoffset() == timedelta(0)
    assert payload["resets_at"] == payload["period_end"] and payload["trial_expires_at"] is None
    assert payload["server_now"] == pool.now.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("grace,access_type,limit", [(1, "subscriber", 20), (-1, "free", 3)])
def test_paid_past_due_uses_existing_canonical_grace_rules(chart_api, grace, access_type, limit):
    client, _, pool = chart_api
    subscribe(client, status="PAST_DUE", expired=True, grace=grace)
    response = client.get(USAGE, headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 200, response.text
    assert response.json()["access_type"] == access_type
    assert response.json()["quota"]["remaining"] == limit


def test_owner_is_unlimited_without_subscription_and_actual_role_is_authoritative(chart_api):
    client, _, pool = chart_api
    response = client.get(USAGE, headers=headers("owner", "OWNER"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["access_type"] == "owner" and payload["quota"]["unlimited"]
    assert payload["quota"]["remaining"] is None and payload["quota"]["limit"] is None
    assert payload["period_start"] is None and payload["period_end"] is None
    assert not pool.state.requests


def test_moderator_gets_twenty_weekly_analyses_without_subscription(chart_api):
    client, _, pool = chart_api
    response = client.get(USAGE, headers=headers("moderator"))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "access_type" in payload, payload
    assert payload["access_type"] == "subscriber"
    assert payload["quota"] == {"limit": 20, "used": 0, "reserved": 0, "remaining": 20, "unlimited": False}
    start = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
    assert start.weekday() == 0 and start.hour == start.minute == start.second == 0
    assert payload["resets_at"] == payload["period_end"]
    assert pool.state.periods


def test_claimed_owner_role_and_query_fields_cannot_promote_trial(chart_api):
    client, _, pool = chart_api
    start_trial(client, pool)
    response = client.get(USAGE + "?user_id=owner&isPremium=true&plan=MASTER_MODE",
                          headers=headers("customer", "OWNER"))
    assert response.status_code == 200, response.text
    assert response.json()["access_type"] == "trial" and response.json()["quota"]["limit"] == 15


@pytest.mark.parametrize("status", ["ACTIVE", "CANCELLED", "EXPIRED", "UNPAID"])
def test_expired_or_inactive_subscription_falls_back_to_free_allowance(chart_api, status):
    client, _, pool = chart_api
    subscribe(client, status=status, expired=True)
    response = client.get(USAGE, headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 200, response.text
    assert response.json()["access_type"] == "free"
    assert response.json()["quota"] == {"limit": 3, "used": 0, "reserved": 0, "remaining": 3, "unlimited": False}


def test_expired_trial_falls_back_to_free_allowance(chart_api):
    client, _, pool = chart_api
    start_trial(client, pool)
    trial = client.app.state.v22_commercial["state"]["subscriptions"][0]
    expired = (pool.now - timedelta(seconds=1)).isoformat()
    trial["trial_end"] = expired
    trial["current_period_end"] = expired
    response = client.get(USAGE, headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 200, response.text
    assert response.json()["access_type"] == "free"


def test_unverifiable_subscription_fails_closed(chart_api, monkeypatch):
    from fastapi import HTTPException

    client, _, pool = chart_api
    start_trial(client, pool)
    monkeypatch.setattr(
        chart_ai_api,
        "subscription_for_user",
        lambda _state, _user_id: (_ for _ in ()).throw(HTTPException(503, "unavailable")),
    )
    for method, path in (("GET", USAGE), ("POST", ACK)):
        response = client.request(method, path, headers=headers("customer", "CUSTOMER"),
                                  json=BODY if method == "POST" else None)
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "entitlement_unavailable"
    assert pool.queries == []


def test_authentication_verification_and_revocation_remain_required(chart_api):
    client, state, pool = chart_api
    assert client.get(USAGE).status_code == 401
    customer = next(user for user in state["users"] if user["id"] == "customer")
    customer["email_verified"] = False
    assert client.get(USAGE, headers=headers("customer", "CUSTOMER")).status_code == 403
    customer["email_verified"] = True
    customer["auth_version"] = 2
    assert client.get(USAGE, headers=headers("customer", "CUSTOMER")).status_code == 401
    assert pool.queries == []


def test_missing_quota_schema_is_explicit_without_fabricated_balance_or_raw_logs(chart_api, caplog):
    client, _, pool = chart_api
    pool.fail_on = "INSERT INTO chart_ai_quota_periods"
    response = client.get(USAGE, headers=headers("owner", "OWNER"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert "quota" not in response.json()
    assert "private@example.test" not in caplog.text
    assert "secret-provider-detail" not in caplog.text


def test_chart_metadata_never_hydrates_credentials_or_drains_trading_tasks(chart_api):
    client, _, pool = chart_api
    start_trial(client, pool)
    pending = {"customer": [object()]}
    client.app.state._binance_demo_persistence_tasks = pending
    assert client.get(USAGE, headers=headers("customer", "CUSTOMER")).status_code == 200
    assert client.post(ACK, headers=headers("customer", "CUSTOMER"), json=BODY).status_code == 200
    assert "customer" in pending and len(pending["customer"]) == 1
    main.hydrate_authenticated_user_state.assert_not_awaited()


def test_nonchart_paths_preserve_existing_hydration(chart_api):
    client, _, _ = chart_api

    async def handler():
        return {"status": "fixture"}

    client.app.add_api_route("/api/v22/chart-test-existing", handler, methods=["GET"])
    assert client.get("/api/v22/chart-test-existing", headers=headers("owner", "OWNER")).status_code == 200
    main.hydrate_authenticated_user_state.assert_awaited_once()


def test_chart_endpoints_are_registered_and_not_public(chart_api):
    client, _, _ = chart_api
    paths = main.app.openapi()["paths"]
    assert {path for path in paths if path.startswith("/api/chart-ai/")} == {
        USAGE, ACK, "/api/chart-ai/analyze", "/api/chart-ai/requests/{request_key}",
    }
    assert USAGE not in main.MEMBER_PUBLIC_PATHS and ACK not in main.MEMBER_PUBLIC_PATHS
    assert client.post("/api/chart-ai/analyze", headers=headers("owner", "OWNER"), json={}).status_code == 422
    assert client.get("/api/chart-ai/requests/not-a-key", headers=headers("owner", "OWNER")).status_code == 422


def test_store_factory_uses_only_existing_server_pool():
    from fastapi import FastAPI
    application = FastAPI()
    pool = FakePool()
    application.state.db_pool = pool
    request = Request({"type": "http", "app": application})
    assert chart_ai_api.store(request).pool is pool


def test_owner_only_configuration_blocks_active_subscriber(chart_api, monkeypatch):
    client, _, pool = chart_api
    subscribe(client)
    monkeypatch.setenv("CHART_AI_OWNER_ONLY", "true")
    response = client.get(USAGE, headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "owner_only"
    assert pool.queries == []


def test_unsubscribed_account_cannot_use_premium_hint_as_entitlement(chart_api):
    client, _, pool = chart_api
    response = client.get(USAGE + "?isPremium=true", headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 200, response.text
    assert response.json()["access_type"] == "free" and response.json()["quota"]["limit"] == 3


@pytest.mark.parametrize("change", [{"trial_start": None}, {"trial_end": "not-a-date"},
                                   {"trial_end": "2030-01-01T00:00:00Z"}])
def test_malformed_trial_period_fails_closed(chart_api, change):
    client, _, pool = chart_api
    start_trial(client, pool)
    client.app.state.v22_commercial["state"]["subscriptions"][0].update(change)
    response = client.get(USAGE, headers=headers("customer", "CUSTOMER"))
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "entitlement_invalid" and pool.queries == []


@pytest.mark.parametrize("path", [USAGE, ACK])
def test_missing_chart_store_pool_is_explicit(chart_api, monkeypatch, path):
    client, _, _ = chart_api
    monkeypatch.setattr(chart_ai_api, "store", lambda _request: ChartAiStore(None))
    response = (client.get(path, headers=headers("owner", "OWNER")) if path == USAGE
                else client.post(path, headers=headers("owner", "OWNER"), json=BODY))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "storage_unavailable"
    assert response.json()["quota_consumed"] is False


def test_missing_acknowledgement_schema_never_claims_acceptance(chart_api):
    client, _, pool = chart_api
    pool.fail_on = "INSERT INTO chart_ai_acknowledgements"
    response = client.post(ACK, headers=headers("owner", "OWNER"), json=BODY)
    assert response.status_code == 503 and response.json()["error"]["code"] == "storage_unavailable"
    assert pool.state.acknowledgements == {}


def test_untrusted_browser_origin_cannot_write_acknowledgement(chart_api):
    client, _, pool = chart_api
    response = client.post(ACK, headers={**headers("owner", "OWNER"), "X-Requested-With": "XMLHttpRequest",
                                      "Origin": "https://untrusted.example.test"}, json=BODY)
    assert response.status_code == 403 and pool.queries == []


def test_usage_reports_existing_used_and_reserved_counts(chart_api):
    client, _, pool = chart_api
    start_trial(client, pool)
    auth = headers("customer", "CUSTOMER")
    assert client.get(USAGE, headers=auth).status_code == 200
    period = next(iter(pool.state.periods.values()))
    period.update(used=1, reserved=1)
    payload = client.get(USAGE, headers=auth).json()
    assert payload["quota"] == {"limit": 15, "used": 1, "reserved": 1, "remaining": 13, "unlimited": False}
