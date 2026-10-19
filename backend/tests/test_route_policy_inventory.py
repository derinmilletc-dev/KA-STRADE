"""Route-policy inventory for the routes that ``test_owner_inventory_covers_every_registered_admin_route`` does not see.

That test only walks ``/api/v22/admin/*``.  This file closes three gaps:

* ``/api/mod/*`` and ``/api/v27/*``: every registered route must be listed below with the permission or policy it is
  supposed to enforce, and the listing must not contain routes that no longer exist.  A new route cannot be added
  without a conscious decision here.
* OWNER-guarded routes that live OUTSIDE ``/api/v22/admin/`` (``/customers``, ``/plans``, ``/commerce/*``,
  ``/operations`` ...).  Any handler that calls ``authenticated_user(..., owner=True)``, ``monitoring_admin``,
  ``is_owner`` or ``owner_connection`` must be in ``OWNER_OUTSIDE_ADMIN_PREFIX``.  Limitation: only the handler's own
  source is scanned, not helpers it calls.
* Behaviour (negative paths only, so every request stops at the authorization dependency before any handler or
  storage code runs): CUSTOMER is rejected everywhere an OWNER or moderator permission is required, a moderator needs
  exactly the declared permission and an enabled second factor.  The CUSTOMER role is also run against the whole
  ``OWNER_ENDPOINTS`` list from ``test_moderator_access`` (which only exercises MODERATOR).

Only invented users and an in-memory fake pool are used; nothing touches the network or a real credential.
"""
import copy
import inspect
import re

import pytest

from app import main
from app.moderator_access import PERMISSIONS
from test_moderator_access import OWNER_ENDPOINTS
from test_moderator_role import headers, setup  # noqa: F401  (fixture re-export, same pattern as test_moderator_access)

# (method, path template) -> permission required through ``require_permission``; ``None`` = any moderator/owner.
MOD_ROUTES = {
    ("GET", "/api/mod/me"): None,
    ("GET", "/api/mod/customers"): "customers.view",
    ("GET", "/api/mod/customers/{user_id}"): "customers.view",
    ("GET", "/api/mod/customers/{user_id}/subscription"): "subscriptions.view",
    ("GET", "/api/mod/customers/{user_id}/payments"): "payments.view",
    ("GET", "/api/mod/customers/{user_id}/events"): "events.view",
    ("GET", "/api/mod/payment-requests"): "payments.view",
    ("POST", "/api/mod/payment-requests/{request_id}/recommend"): "approvals.create",
    ("GET", "/api/mod/support/cases"): "support.view",
    ("GET", "/api/mod/support/summary"): "support.view",
    ("GET", "/api/mod/support/cases/{case_id}"): "support.view",
    ("POST", "/api/mod/support/cases/{case_id}/take"): "support.manage",
    ("POST", "/api/mod/support/cases/{case_id}/release"): "support.manage",
    ("POST", "/api/mod/support/cases/{case_id}/status"): "support.manage",
    ("POST", "/api/mod/support/cases/{case_id}/notes"): "support.manage",
    ("POST", "/api/mod/support/cases/{case_id}/reply"): "support.manage",
    ("POST", "/api/mod/approvals"): "approvals.create",
    ("GET", "/api/mod/approvals"): "approvals.create",
    ("POST", "/api/mod/approvals/{approval_id}/cancel"): "approvals.create",
    ("POST", "/api/mod/premium-requests"): "approvals.create",
    ("GET", "/api/mod/premium-requests"): "approvals.create",
    ("POST", "/api/mod/premium-requests/{request_id}/cancel"): "approvals.create",
    ("GET", "/api/mod/campaigns"): "campaigns.manage",
    ("POST", "/api/mod/campaigns"): "campaigns.manage",
    ("POST", "/api/mod/campaigns/preview"): "campaigns.manage",
    ("GET", "/api/mod/campaigns/{campaign_id}"): "campaigns.manage",
    ("PUT", "/api/mod/campaigns/{campaign_id}"): "campaigns.manage",
    ("POST", "/api/mod/campaigns/{campaign_id}/preview"): "campaigns.manage",
    ("POST", "/api/mod/campaigns/{campaign_id}/test"): "campaigns.manage",
    ("POST", "/api/mod/campaigns/{campaign_id}/request-send"): "campaigns.manage",
    ("POST", "/api/mod/campaigns/{campaign_id}/send"): "campaigns.manage",  # handler additionally requires role OWNER
    ("POST", "/api/mod/campaigns/{campaign_id}/cancel"): "campaigns.manage",
}

# /api/v27: operations is role-scoped inside the handler (OWNER = platform view, others = own Demo view).
V27_ROUTES = {
    ("GET", "/api/v27/operations"): "MEMBER_SCOPED",
    ("POST", "/api/v27/evidence/sync"): "OWNER",
}

# OWNER-guarded routes outside /api/v22/admin/ (handler calls a standard OWNER guard).
OWNER_OUTSIDE_ADMIN_PREFIX = {
    ("POST", "/api/v22/customers"),
    ("POST", "/api/v22/customers/{user_id}/status"),
    ("POST", "/api/v22/subscriptions/activate-demo"),
    ("POST", "/api/v22/licenses/{license_id}/revoke"),
    ("PUT", "/api/v22/plans/{plan_code}"),
    ("PUT", "/api/v22/release-evidence/{evidence_key}"),
    ("GET", "/api/v22/operations"),
    ("GET", "/api/v22/readiness"),
    ("GET", "/api/v22/commerce/overview"),
    ("PUT", "/api/v22/commerce/settings"),
    ("POST", "/api/v22/commerce/leads"),
    ("PUT", "/api/v22/commerce/leads/{lead_id}/status"),
    ("PUT", "/api/v22/commerce/support/{ticket_id}"),
}

OWNER_GUARD_MARKERS = re.compile(r"owner\s*=\s*True|monitoring_admin\(|is_owner\(|owner_connection\(")
SAMPLE_ID = "a" * 32


def _is_api_route(route):
    # Duck-typed on purpose: some FastAPI versions wrap included routers in lazy ``_IncludedRouter`` objects.
    return hasattr(route, "path") and hasattr(route, "endpoint") and bool(getattr(route, "methods", None))


def _flatten(routes, prefix=""):
    """Yield (full_path, route) for every endpoint route, descending into included-router wrappers."""
    for route in routes:
        if _is_api_route(route):
            path = route.path
            yield (path if path.startswith(prefix) else prefix + path), route
            continue
        inner = getattr(route, "router", None) or getattr(route, "original_router", None)
        children = getattr(inner, "routes", None) if inner is not None else getattr(route, "routes", None)
        if children is None:
            continue
        extra = getattr(route, "prefix", "") or ""
        yield from _flatten(children, prefix + extra if not prefix.endswith(extra) else prefix)


def _api_routes():
    for path, route in _flatten(main.app.routes):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, path, route


def test_route_walk_matches_openapi():
    """Guards the guards: an incomplete walk would make the inventory assertions below vacuous."""
    walked = {path for _method, path, _route in _api_routes()}
    documented = set(main.app.openapi()["paths"])
    kinds = {type(route).__name__: sorted(a for a in vars(route) if not a.startswith("__"))[:12] for route in main.app.routes}
    assert walked == documented, (
        f"route walk differs from openapi: only walked={sorted(walked - documented)[:8]} only openapi={sorted(documented - walked)[:8]} "
        f"(types: {kinds})")


def _concrete(path):
    return re.sub(r"\{[^}]+\}", SAMPLE_ID, path)


def _call(client, method, path, who, body=None):
    uid, role = who
    kwargs = {} if method == "GET" else {"json": {} if body is None else body}
    return client.request(method, _concrete(path), headers=headers(uid, role), **kwargs)


# ------------------------------------------------------------------ inventory (no HTTP)
def test_every_mod_and_v27_route_is_classified():
    actual = {(method, path) for method, path, _ in _api_routes() if path.startswith(("/api/mod/", "/api/v27/"))}
    expected = set(MOD_ROUTES) | set(V27_ROUTES)
    assert actual - expected == set(), f"unclassified routes (add them to the manifest): {sorted(actual - expected)}"
    assert expected - actual == set(), f"manifest lists routes that no longer exist: {sorted(expected - actual)}"


def test_owner_guarded_routes_outside_the_admin_prefix_are_inventoried():
    found = set()
    for method, path, route in _api_routes():
        if path.startswith(("/api/v22/admin/", "/api/mod/", "/api/v27/")):
            continue
        try:
            source = inspect.getsource(inspect.unwrap(route.endpoint))
        except (OSError, TypeError):
            continue
        if OWNER_GUARD_MARKERS.search(source):
            found.add((method, path))
    assert found - OWNER_OUTSIDE_ADMIN_PREFIX == set(), (
        f"OWNER-guarded routes missing from OWNER_OUTSIDE_ADMIN_PREFIX (and from the owner endpoint tests): {sorted(found - OWNER_OUTSIDE_ADMIN_PREFIX)}")
    assert OWNER_OUTSIDE_ADMIN_PREFIX - found == set(), (
        f"inventory lists routes that no longer carry an OWNER guard: {sorted(OWNER_OUTSIDE_ADMIN_PREFIX - found)}")


def test_owner_endpoint_list_covers_every_owner_route_outside_the_admin_prefix():
    listed = {(method, re.sub(r"[^/]+", "X", path)) for method, path, _ in OWNER_ENDPOINTS}
    missing = []
    for method, path in sorted(OWNER_OUTSIDE_ADMIN_PREFIX):
        pattern = re.sub(r"\{[^}]+\}", "X", path)
        if (method, re.sub(r"[^/]+", "X", pattern)) not in listed and (method, pattern) not in listed:
            # compare by shape: same segment count and same literal segments
            shapes = [(m, p) for m, p, _ in OWNER_ENDPOINTS if m == method and len(p.split("/")) == len(path.split("/"))
                      and all(a == b or a.startswith("{") for a, b in zip(path.split("/"), p.split("/")))]
            if not shapes:
                missing.append((method, path))
    assert missing == [], f"OWNER routes outside /api/v22/admin/ without a negative test in OWNER_ENDPOINTS: {missing}"


# ------------------------------------------------------------------ behaviour (negative paths only)
@pytest.fixture
def full(setup):  # noqa: F811
    """The shared fixture with every ``/api/`` route of the real application mounted."""
    client, pool = setup
    present = {(route.path, frozenset(route.methods or ())) for route in client.app.routes if hasattr(route, "path")}
    for route in main.app.routes:  # whole entries, so wrapped (lazily included) routers are mounted too
        if not _is_api_route(route) and not any(route is existing for existing in client.app.routes):
            client.app.router.routes.append(route)
        elif _is_api_route(route) and route.path.startswith("/api/") \
                and (route.path, frozenset(route.methods or ())) not in present:
            client.app.router.routes.append(route)
    pool.settings["moderator"] = {"two_factor_enabled": True}
    pool.permissions["moderator"] = dict.fromkeys(PERMISSIONS, {})
    yield client, pool


MOD_CASES = [(method, path, permission) for (method, path), permission in sorted(MOD_ROUTES.items()) if permission]
MOD_IDS = [f"{method} {path}" for method, path, _ in MOD_CASES]


@pytest.mark.parametrize("method,path,permission", MOD_CASES, ids=MOD_IDS)
def test_customer_is_forbidden_on_every_moderator_route(full, method, path, permission):
    client, pool = full
    before = copy.deepcopy(pool.users)
    response = _call(client, method, path, ("customer", "CUSTOMER"))
    assert response.status_code == 403, response.text
    assert pool.users == before


@pytest.mark.parametrize("method,path,permission", MOD_CASES, ids=MOD_IDS)
def test_moderator_needs_exactly_the_declared_permission(full, method, path, permission):
    client, pool = full
    pool.permissions["moderator"] = {name: {} for name in PERMISSIONS if name != permission}
    response = _call(client, method, path, ("moderator", "MODERATOR"))
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("method,path,permission", MOD_CASES, ids=MOD_IDS)
def test_moderator_without_second_factor_is_rejected(full, method, path, permission):
    client, pool = full
    pool.settings["moderator"] = {"two_factor_enabled": False}
    response = _call(client, method, path, ("moderator", "MODERATOR"))
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "mfa_required"


@pytest.mark.parametrize("uid,role", [("customer", "CUSTOMER"), ("moderator", "MODERATOR")])
def test_v27_evidence_sync_is_owner_only(full, uid, role):
    client, pool = full
    before = copy.deepcopy(pool.users)
    response = client.post("/api/v27/evidence/sync", headers=headers(uid, role))
    assert response.status_code == 403, response.text
    assert pool.users == before


@pytest.mark.parametrize("method,path,payload", OWNER_ENDPOINTS, ids=[f"{m} {p}" for m, p, _ in OWNER_ENDPOINTS])
def test_customer_cannot_enter_any_owner_endpoint(full, method, path, payload):
    """Same list the moderator test uses, run as CUSTOMER."""
    client, pool = full
    before = copy.deepcopy(pool.users)
    response = client.request(method, path, headers=headers("customer", "CUSTOMER"), json=payload)
    assert response.status_code == 403, response.text
    assert pool.users == before
    assert all(sql.startswith("SELECT auth_version, security FROM commercial_auth_users") for sql in pool.queries)
