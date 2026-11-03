import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import founding


class Conn:
    def __init__(self, applications_open=True, existing=None):
        self.applications_open, self.existing, self.executed = applications_open, existing, []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchrow(self, sql, *args):
        if "founding_settings" in sql:
            return {"seats": 30, "applications_open": self.applications_open, "launch_at": None, "price_text": ""}
        return self.existing

    @asynccontextmanager
    async def transaction(self):
        yield


class Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def request(conn):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=Pool(conn))), client=SimpleNamespace(host="203.0.113.5"),
                           headers={}, state=SimpleNamespace())


def body(**kw):
    return founding.Apply(**{"email": "A@Example.com", "name": "Ada", "country": "ZA", "futures_experience": "past",
                            "trading_experience": "6to12m", "reason": "Riskimi kontrol ederek işlem yapmak istiyorum.", **kw})


async def noop_limit(*_a, **_k):
    return None


class Unique(Exception):
    pass


@pytest.fixture(autouse=True)
def real_exception_class(monkeypatch):
    monkeypatch.setattr(founding, "UniqueViolationError", Unique)  # asyncpg yoksa/taklitse yakalama sınıfı geçerli kalsın


def run(coro):
    return asyncio.run(coro)


def test_honeypot_returns_ok_without_touching_database():
    conn = Conn()
    assert run(founding.apply(body(website="http://spam"), request(conn))) == {"ok": True}
    assert conn.executed == []


def test_invalid_email_is_rejected():
    with pytest.raises(HTTPException) as err:
        run(founding.apply(body(email="not-an-email"), request(Conn())))
    assert err.value.status_code == 422


def test_closed_applications_return_409():
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit):
        with pytest.raises(HTTPException) as err:
            run(founding.apply(body(), request(Conn(applications_open=False))))
    assert err.value.status_code == 409


def test_new_application_stores_hashed_token_and_sends_confirmation():
    conn, sent = Conn(), []
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit), \
            patch.object(founding, "_send_confirmation", lambda *a: sent.append(a)):
        assert run(founding.apply(body(), request(conn))) == {"ok": True}
    insert = [args for sql, args in conn.executed if "INSERT INTO founding_applications" in sql][0]
    assert insert[0] == "a@example.com" and len(insert[4]) == 64
    assert insert[7:10] == ("past", "6to12m", "Riskimi kontrol ederek işlem yapmak istiyorum.")
    assert sent and sent[0][0] == "a@example.com" and sent[0][2] not in insert  # düz belirteç saklanmaz


def test_existing_confirmed_applicant_gets_same_answer_and_no_mail():
    conn, sent = Conn(existing={"id": 7, "status": "approved"}), []
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit), \
            patch.object(founding, "_send_confirmation", lambda *a: sent.append(a)):
        assert run(founding.apply(body(), request(conn))) == {"ok": True}
    assert sent == [] and conn.executed == []


def test_hash_is_stable_and_clean_strips_control_characters():
    assert founding._hash("x") == founding._hash("x") and len(founding._hash("x")) == 64
    assert founding._clean("a\x00b\nc", 10) == "a b c"


def test_reason_and_experience_answers_are_required():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        founding.Apply(email="a@example.com", name="Ada", country="ZA", futures_experience="active", trading_experience="new", reason="kısa")
    with pytest.raises(ValidationError):
        founding.Apply(email="a@example.com", name="Ada", country="ZA", reason="Yeterince uzun bir gerekçe")


def test_unknown_experience_values_are_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        founding.Apply(email="a@example.com", name="Ada", country="ZA", futures_experience="yes", trading_experience="new", reason="Yeterince uzun bir gerekçe")
