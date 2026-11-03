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
    result = run(founding.apply(body(website="http://spam"), request(conn)))
    assert result["ok"] is True and result["reference"].startswith("KT-F30-")
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
        result = run(founding.apply(body(), request(conn)))
    insert = [args for sql, args in conn.executed if "INSERT INTO founding_applications" in sql][0]
    assert insert[0] == "a@example.com" and len(insert[4]) == 64
    assert insert[7:10] == ("past", "6to12m", "Riskimi kontrol ederek işlem yapmak istiyorum.")
    assert insert[10] == result["reference"]
    assert sent and sent[0][0] == "a@example.com" and sent[0][2] not in insert  # düz belirteç saklanmaz


def test_existing_applicant_cannot_apply_again():
    conn, sent = Conn(existing={"id": 7, "status": "approved"}), []
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit), \
            patch.object(founding, "_send_confirmation", lambda *a: sent.append(a)):
        with pytest.raises(HTTPException) as err:
            run(founding.apply(body(country_code="TR"), request(conn)))
    assert err.value.status_code == 409 and "yalnızca bir kez" in err.value.detail
    assert sent == [] and conn.executed == []  # bilgiler değişmez, e-posta gitmez


def test_unconfirmed_applicant_gets_link_resent_but_data_kept():
    class Pending(Conn):
        async def fetchrow(self, sql, *args):
            if "SELECT name, lang, reference" in sql:
                return {"name": "Eski Ad", "lang": "tr", "reference": "KT-F30-ABCDE"}
            return await super().fetchrow(sql, *args)
    conn, sent = Pending(existing={"id": 7, "status": "pending_email"}), []
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit), \
            patch.object(founding, "_send_confirmation", lambda *a: sent.append(a)):
        with pytest.raises(HTTPException) as err:
            run(founding.apply(body(name="Yeni Ad", country_code="TR"), request(conn)))
    assert err.value.status_code == 409 and "yeniden gönderdik" in err.value.detail
    assert sent and sent[0][1] == "Eski Ad" and sent[0][4] == "KT-F30-ABCDE"
    assert not any("SET name" in sql for sql, _ in conn.executed)


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


def test_reference_is_non_sequential_and_well_formed():
    import re
    refs = {founding.new_reference() for _ in range(200)}
    assert len(refs) > 190 and all(re.fullmatch(r"KT-F30-[2-9A-HJ-NP-Z]{5}", ref) for ref in refs)


def test_track_requires_matching_email_and_reference():
    class TrackConn(Conn):
        async def fetchrow(self, sql, *args):
            if "WHERE reference" in sql:
                return {"reference": "KT-F30-ABCDE", "name": "Ada", "status": "new", "created_at": None, "email_confirmed_at": None,
                        "decided_at": None} if args == ("KT-F30-ABCDE", "a@example.com") else None
            return await super().fetchrow(sql, *args)
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit):
        assert run(founding.track(founding.Track(email="A@example.com", reference="KT-F30-ABCDE"), request(TrackConn())))["status"] == "new"
        with pytest.raises(HTTPException) as err:
            run(founding.track(founding.Track(email="b@example.com", reference="KT-F30-ABCDE"), request(TrackConn())))
    assert err.value.status_code == 404


def test_status_link_is_signed_and_stable():
    from app import founding_mail
    key = founding_mail.status_key("s" * 32, "KT-F30-ABCDE")
    assert key == founding_mail.status_key("s" * 32, "KT-F30-ABCDE") and len(key) == 32
    assert key != founding_mail.status_key("t" * 32, "KT-F30-ABCDE")
    assert founding_mail.status_url("https://kaistrade.com", "s" * 32, "KT-F30-ABCDE") == f"https://kaistrade.com/basvuru-durumu?r=KT-F30-ABCDE&k={key}"
    assert founding_mail.status_url("https://kaistrade.com", None, "KT-F30-ABCDE") is None


def test_emails_carry_reference_and_escape_name():
    from app import founding_mail
    for kind in ("received", "review", "approved", "waitlist", "rejected"):
        for lang in ("tr", "en"):
            subject, text, body = founding_mail.render(kind, name="<b>Ada</b> L", reference="KT-F30-ABCDE", lang=lang, action_url="https://kaistrade.com/x?a=1&b=2")
            assert "KT-F30-ABCDE" in subject and "KT-F30-ABCDE" in text and "KT-F30-ABCDE" in body
            assert "<b>Ada</b>" not in body and "&lt;b&gt;Ada&lt;/b&gt;" in body
            assert "https://kaistrade.com/x?a=1&amp;b=2" in body


def test_track_link_rejects_wrong_signature():
    state = SimpleNamespace(db_pool=Pool(Conn()), v22_commercial={"secret": "s" * 32})
    req = SimpleNamespace(app=SimpleNamespace(state=state), client=SimpleNamespace(host="203.0.113.5"), headers={}, state=SimpleNamespace())
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit):
        with pytest.raises(HTTPException) as err:
            run(founding.track_link(req, r="KT-F30-ABCDE", k="0" * 32))
    assert err.value.status_code == 404


def test_track_link_returns_status_for_valid_signature():
    from datetime import datetime, timezone
    from app import founding_mail

    class Row(Conn):
        async def fetchrow(self, sql, *args):
            if "WHERE reference" in sql:
                now = datetime(2026, 11, 3, tzinfo=timezone.utc)
                return {"reference": args[0], "name": "Ada Lovelace", "status": "new", "created_at": now, "email_confirmed_at": now, "decided_at": None}
            return await super().fetchrow(sql, *args)
    state = SimpleNamespace(db_pool=Pool(Row()), v22_commercial={"secret": "s" * 32})
    req = SimpleNamespace(app=SimpleNamespace(state=state), client=SimpleNamespace(host="203.0.113.5"), headers={}, state=SimpleNamespace())
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit):
        view = run(founding.track_link(req, r="KT-F30-ABCDE", k=founding_mail.status_key("s" * 32, "KT-F30-ABCDE")))
    assert view["status"] == "new" and view["first_name"] == "Ada" and "email" not in view


def test_status_email_failure_does_not_break_flow():
    from datetime import datetime, timezone
    row = {"reference": "KT-F30-ABCDE", "email": "a@example.com", "name": "Ada", "lang": "tr", "updated_at": datetime.now(timezone.utc)}
    def boom(*_a, **_k):
        raise RuntimeError("smtp down")
    req = request(Conn())
    with patch.object(founding, "_app_base", lambda: "https://kaistrade.com"), patch("app.founding_mail.send", boom):
        assert founding._notify(req, "approved", row) is False


def test_funnel_event_counts_without_personal_data():
    conn = Conn()
    with patch("app.v22_commercial.enforce_auth_limit", noop_limit), patch.object(founding, "ensure_schema", noop_limit):
        run(founding.funnel_event(founding.FunnelEvent(event="view"), request(conn)))
    sql, args = conn.executed[-1]
    assert "founding_funnel" in sql and args == ("view",)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        founding.FunnelEvent(event="hack")


def _reg_request(value=None, pool=True):
    class RegConn(Conn):
        async def fetchval(self, sql, *args):
            assert "registration_open" in sql
            return value
    state = SimpleNamespace(db_pool=Pool(RegConn()) if pool else None)
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_registration_closed_by_default_setting(monkeypatch):
    monkeypatch.delenv("FOUNDING_REGISTRATION", raising=False)
    with patch.object(founding, "ensure_schema", noop_limit):
        assert run(founding.registration_allowed(_reg_request(False))) is False
        assert run(founding.registration_allowed(_reg_request(True))) is True
        with pytest.raises(HTTPException) as err:
            run(founding.require_registration_open(_reg_request(False)))
    assert err.value.status_code == 403


def test_registration_env_override_and_no_database(monkeypatch):
    with patch.object(founding, "ensure_schema", noop_limit):
        monkeypatch.setenv("FOUNDING_REGISTRATION", "open")
        assert run(founding.registration_allowed(_reg_request(False))) is True
        monkeypatch.setenv("FOUNDING_REGISTRATION", "closed")
        assert run(founding.registration_allowed(_reg_request(True))) is False
        monkeypatch.delenv("FOUNDING_REGISTRATION")
        assert run(founding.registration_allowed(_reg_request(pool=False))) is True
