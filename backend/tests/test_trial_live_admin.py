import asyncio
from unittest import mock

from app import trial_live_admin as module

ROWS = [{"user_id": "u1", "email": "ali@example.com", "today": 5, "week": 9},
        {"user_id": "u2", "email": "veli@example.com", "today": 2, "week": 2}]


class Conn:
    def __init__(self, rows=ROWS, error=None):
        self.rows, self.error, self.args = rows, error, None

    async def fetch(self, sql, *args):
        self.args = args
        if self.error:
            raise self.error
        return self.rows


def test_owner_sees_full_email_and_limit_flag():
    payload = module._payload(asyncio.run(module._rows(Conn(), True, 50)), mask=False)
    assert payload["daily_limit"] == 5
    assert payload["items"][0] == {"user_id": "u1", "email": "ali@example.com", "today": 5, "week": 9, "limit_reached": True}
    assert payload["items"][1]["limit_reached"] is False


def test_moderator_gets_masked_email_and_customers_only():
    conn = Conn()
    payload = module._payload(asyncio.run(module._rows(conn, False, 20)), mask=True)
    assert conn.args == (False, 20)
    assert "ali@example.com" not in str(payload) and payload["items"][0]["email"]


def test_missing_table_gives_empty_list():
    from asyncpg import UndefinedTableError
    assert asyncio.run(module._rows(Conn(error=UndefinedTableError("x")), True, 10)) == []


def test_routes_are_read_only_and_wired_in_main():
    paths = {(r.path, tuple(sorted(r.methods))) for r in module.admin_router.routes} | {(r.path, tuple(sorted(r.methods))) for r in module.mod_router.routes}
    assert paths == {("/api/v22/admin/trial-live", ("GET",)), ("/api/mod/trial-live", ("GET",))}
    import app.main as main
    assert main.trial_live_admin_router is module.admin_router and main.trial_live_mod_router is module.mod_router
