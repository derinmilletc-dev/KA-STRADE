"""GET /api/v22/operations and GET /api/v22/readiness are OWNER-only.

Both endpoints report application-level state (shared Demo/paper state, platform readiness evidence), not the
caller's own data.  Members get their own Demo view from /api/v27/operations.  Only invented sentinel values are
used; nothing touches the network, a real database or any credential: ``binance_demo.load_demo_credentials`` is
pinned to empty values so no vault, ``.env`` file or environment variable can influence the result.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app import binance_demo
from test_moderator_role import headers, setup  # noqa: F401  (fixture re-export, same pattern as test_moderator_access)

ROLES = [("owner", "OWNER"), ("moderator", "MODERATOR"), ("customer", "CUSTOMER")]
NON_OWNER = [("moderator", "MODERATOR"), ("customer", "CUSTOMER")]

SHARED_WALLET = "987654.321"
SHARED_AVAILABLE = "123456.789"
SHARED_EVENT = "SHARED-EVENT-MARKER"
SHARED_ERROR = "SHARED-ERROR-MARKER"
SHARED_DECISION = "SHARED-DECISION-MARKER"
SHARED_NOTE = "SHARED-EVIDENCE-NOTE"
SHARED_MARKERS = (SHARED_WALLET, SHARED_AVAILABLE, SHARED_EVENT, SHARED_ERROR, SHARED_DECISION)

USER_WALLET = {"owner": "111111.111", "moderator": "222222.222", "customer": "333333.333"}
USER_MARKER = {"owner": "USER-OWNER-MARKER", "moderator": "USER-MODERATOR-MARKER", "customer": "USER-CUSTOMER-MARKER"}


def _future():
    return (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


def _snapshot(wallet, available, symbol):
    return {
        "wallet_balance": float(wallet), "available_balance": float(available),
        "positions": [{"symbol": symbol}], "open_orders": [], "open_algo_orders": [],
    }


@pytest.fixture
def scoped(setup, monkeypatch):  # noqa: F811
    """The shared fixture plus invented application-level and per-user Demo state."""
    client, _pool = setup
    monkeypatch.setattr(binance_demo, "load_demo_credentials", lambda *args, **kwargs: ("", ""))
    state = client.app.state
    state.v21_demo = {
        "snapshot": _snapshot(SHARED_WALLET, SHARED_AVAILABLE, "SHAREDUSDT"),
        "auto": {"enabled": False, "cycles": 7, "last_decision": SHARED_DECISION},
    }
    state.binance_demo = {
        "connected": True, "armed_until": 0, "last_error": SHARED_ERROR,
        "events": [{"kind": "SHARED", "message": SHARED_EVENT}],
    }
    state.paper = {"balance": 4321.5, "positions": [], "limit_orders": [], "trades": [], "emergency_brake": {}}
    state.paper_bot = {"enabled": False, "cycles": 0}
    state.infrastructure = {"api": "BAGLI"}
    state._v21_demo_user_state = {
        uid: {
            "snapshot": _snapshot(USER_WALLET[uid], USER_WALLET[uid], USER_MARKER[uid] + "USDT"),
            "auto": {"enabled": False, "cycles": 1, "last_decision": USER_MARKER[uid]},
        } for uid, _role in ROLES
    }
    state._binance_demo_user_state = {
        uid: {
            "connected": True, "armed_until": 0, "last_error": USER_MARKER[uid],
            "events": [{"kind": "USER", "message": USER_MARKER[uid]}],
        } for uid, _role in ROLES
    }
    return client


def _make_premium(client, user_id):
    client.app.state.v22_commercial["state"].setdefault("subscriptions", []).append({
        "user_id": user_id, "plan": "MASTER_MODE", "status": "ACTIVE", "current_period_end": _future(),
    })


def _get(client, path, uid, role, *, premium=False):
    if premium:
        _make_premium(client, uid)
    return client.get(path, headers=headers(uid, role))


def _gate(body, key):
    return next(item for item in body["gates"] if item["key"] == key)


def _seed_readiness(client):
    client.app.state.v22_commercial["state"]["release_evidence"] = {
        "backup": {"status": "RECORDED", "note": SHARED_NOTE + "-BACKUP"},
        "legal": {"status": "PENDING", "note": SHARED_NOTE + "-LEGAL"},
    }


# ---------------------------------------------------------------- access control
@pytest.mark.parametrize("path", ["/api/v22/operations", "/api/v22/readiness"])
@pytest.mark.parametrize("premium", [False, True], ids=["non-premium", "premium"])
@pytest.mark.parametrize("uid,role", NON_OWNER, ids=[role for _uid, role in NON_OWNER])
def test_non_owner_roles_are_forbidden_and_see_no_shared_state(scoped, path, uid, role, premium):
    _seed_readiness(scoped)
    response = _get(scoped, path, uid, role, premium=premium)
    assert response.status_code == 403, response.text
    text = response.text
    for marker in SHARED_MARKERS + (SHARED_NOTE,):
        assert marker not in text, f"{role} premium={premium}: {marker!r} leaked through a 403"


@pytest.mark.parametrize("path", ["/api/v22/operations", "/api/v22/readiness"])
def test_missing_or_revoked_session_is_401(scoped, path):
    assert scoped.get(path).status_code == 401
    assert scoped.get(path, headers={"Authorization": "Bearer not-a-real-token"}).status_code == 401


# ---------------------------------------------------------------- operations (OWNER)
def test_owner_operations_reports_application_level_demo_state(scoped):
    response = scoped.get("/api/v22/operations", headers=headers("owner", "OWNER"))
    assert response.status_code == 200, response.text
    text = response.text
    for marker in SHARED_MARKERS:
        assert marker in text, f"shared marker {marker!r} not reported"
    body = response.json()
    assert body["demo_only"] is True
    assert body["demo_account"]["wallet_balance"] == float(SHARED_WALLET)
    assert body["demo_account"]["available_balance"] == float(SHARED_AVAILABLE)


def test_owner_operations_never_reads_per_user_demo_state(scoped):
    text = scoped.get("/api/v22/operations", headers=headers("owner", "OWNER")).text
    for uid, _role in ROLES:
        assert USER_WALLET[uid] not in text and USER_MARKER[uid] not in text, f"per-user state of {uid} leaked"


# ---------------------------------------------------------------- readiness (OWNER)
def test_owner_readiness_reports_platform_evidence(scoped):
    _seed_readiness(scoped)
    response = scoped.get("/api/v22/readiness", headers=headers("owner", "OWNER"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["production_ready"] is False
    assert SHARED_NOTE + "-BACKUP" in response.text and SHARED_NOTE + "-LEGAL" in response.text
    assert body["release_evidence"]["legal"]["status"] == "PENDING"
    assert all("detail" in item for item in body["gates"]), "owner response must keep gate details"


def test_owner_readiness_license_and_agent_gates_follow_the_owner_only(scoped):
    commercial = scoped.app.state.v22_commercial["state"]
    owner = headers("owner", "OWNER")
    before = scoped.get("/api/v22/readiness", headers=owner).json()
    assert _gate(before, "license")["passed"] is False and _gate(before, "agent")["passed"] is False
    # A license and an agent that belong to somebody else never turn the owner's gates green.
    commercial.setdefault("licenses", []).append({"id": "lic-customer", "user_id": "customer", "status": "ACTIVE", "expires_at": _future()})
    commercial.setdefault("agents", []).append({
        "id": "agent-customer", "user_id": "customer", "status": "ACTIVE", "last_seen_at": datetime.now(timezone.utc).isoformat(),
    })
    other = scoped.get("/api/v22/readiness", headers=owner).json()
    assert _gate(other, "license")["passed"] is False and _gate(other, "agent")["passed"] is False
    commercial["licenses"].append({"id": "lic-owner", "user_id": "owner", "status": "ACTIVE", "expires_at": _future()})
    commercial["agents"].append({
        "id": "agent-owner", "user_id": "owner", "status": "ACTIVE", "last_seen_at": datetime.now(timezone.utc).isoformat(),
    })
    own = scoped.get("/api/v22/readiness", headers=owner).json()
    assert _gate(own, "license")["passed"] is True and _gate(own, "agent")["passed"] is True
