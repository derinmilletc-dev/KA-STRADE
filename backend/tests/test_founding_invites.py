import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import founding, founding_invites


async def noop(*_a, **_k):
    return None


def run(coro):
    return asyncio.run(coro)


class Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class GateConn:
    """registration_allowed için: yönetici anahtarı kapalı, davet satırı isteğe bağlı."""

    def __init__(self, invite_hash=None, email="ada@example.com", open_flag=False):
        self.invite_hash, self.email, self.open_flag, self.calls = invite_hash, email, open_flag, []

    async def fetchval(self, sql, *args):
        return self.open_flag

    async def fetchrow(self, sql, *args):
        self.calls.append(args)
        email, token_hash = args
        ok = self.invite_hash is not None and email == self.email and (token_hash is None or token_hash == self.invite_hash)
        return {"id": 1} if ok else None


def req(conn):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=Pool(conn))), client=SimpleNamespace(host="203.0.113.9"),
                           headers={}, state=SimpleNamespace())


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("FOUNDING_REGISTRATION", raising=False)


def allowed(conn, email=None, token=None):
    with patch.object(founding, "ensure_schema", noop):
        return run(founding.registration_allowed(req(conn), email, token))


def test_no_invite_no_account():
    assert allowed(GateConn(), "ada@example.com") is False
    assert allowed(GateConn(invite_hash=founding._hash("t" * 40)), None) is False


def test_invite_only_for_its_own_email():
    conn = GateConn(invite_hash=founding._hash("t" * 40))
    assert allowed(conn, "ada@example.com", "t" * 40) is True
    assert allowed(conn, "mehmet@example.com", "t" * 40) is False  # davet başka e-postayla kullanılamaz


def test_password_registration_needs_the_exact_token():
    conn = GateConn(invite_hash=founding._hash("t" * 40))
    assert allowed(conn, "ada@example.com", "x" * 40) is False


def test_google_registration_matches_verified_email_without_token():
    conn = GateConn(invite_hash=founding._hash("t" * 40))
    assert allowed(conn, "ada@example.com") is True
    assert conn.calls[-1] == ("ada@example.com", None)


def test_admin_open_flag_and_env_override(monkeypatch):
    assert allowed(GateConn(open_flag=True), "anyone@example.com") is True
    monkeypatch.setenv("FOUNDING_REGISTRATION", "closed")
    assert allowed(GateConn(open_flag=True), "anyone@example.com") is False


def test_mail_language_follows_country():
    assert founding.mail_language("TR") == "tr"
    assert founding.mail_language("ZA") == "en" and founding.mail_language("DE") == "en"
    assert founding.mail_language(None, "Türkiye") == "tr" and founding.mail_language(None, "South Africa") == "en"


class ViewConn:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, sql, *args):
        return self.row


def view(row):
    with patch("app.v22_commercial.enforce_auth_limit", noop), patch.object(founding_invites, "ensure_schema", noop):
        return run(founding_invites.invite_view(req(ViewConn(row)), t="t" * 40))


def test_invite_view_rejects_used_and_expired_links():
    with pytest.raises(HTTPException) as err:
        view(None)
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        view({"status": "account_created"})
    assert err.value.status_code == 404
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    with pytest.raises(HTTPException) as err:
        view({"status": "invited", "invite_expires_at": past})
    assert err.value.status_code == 410


def test_invite_view_returns_locked_email():
    future = datetime.now(timezone.utc) + timedelta(hours=10)
    out = view({"status": "invited", "invite_expires_at": future, "email": "ada@example.com", "name": "Ada", "reference": "KT-F30-ABCDE", "lang": "en"})
    assert out["email"] == "ada@example.com" and out["reference"] == "KT-F30-ABCDE"


class ConsumeConn:
    def __init__(self, row):
        self.row, self.sql = row, []

    async def fetchrow(self, sql, *args):
        self.sql.append((sql, args))
        return self.row


def test_consume_marks_invite_used_once_and_emails():
    row = {"email": "ada@example.com", "status": "account_created"}
    conn, sent = ConsumeConn(row), []
    with patch.object(founding_invites, "ensure_schema", noop), patch.object(founding_invites, "_notify", lambda r, k, x: sent.append(k)):
        assert run(founding_invites.consume_invite(req(conn), "Ada@Example.com", "u1")) is True
    sql, args = conn.sql[0]
    assert "invite_token_hash=NULL" in sql and "status='invited'" in sql and args == ("ada@example.com", "u1")
    assert sent == ["account_created"]
    conn2 = ConsumeConn(None)
    with patch.object(founding_invites, "ensure_schema", noop), patch.object(founding_invites, "_notify", lambda r, k, x: sent.append(k)):
        assert run(founding_invites.consume_invite(req(conn2), "ada@example.com", "u2")) is False
    assert sent == ["account_created"]  # ikinci kez kullanılamaz, e-posta tekrarlanmaz


def test_advance_only_moves_forward():
    conn = ConsumeConn(None)
    with patch.object(founding_invites, "ensure_schema", noop):
        assert run(founding_invites.advance_member(req(conn).app, "u1", "active")) is False
    sql, args = conn.sql[0]
    assert args == ("u1", "active", ["account_created", "payment_pending"]) and "activated_at=now()" in sql


class SweepConn:
    def __init__(self, launch, approved=(), due=(), expired=()):
        self.launch, self.approved, self.due, self.expired, self.issued = launch, list(approved), list(due), list(expired), []

    async def execute(self, sql, *args):
        return None

    async def fetchrow(self, sql, *args):
        if "FROM founding_settings" in sql:
            return {"seats": 30, "applications_open": True, "launch_at": self.launch, "price_text": "", "registration_open": False}
        self.issued.append(args[0])
        return {"id": args[0], "email": "a@example.com", "name": "A", "reference": "KT-F30-ABCDE", "lang": "tr", "updated_at": None}

    async def fetch(self, sql, *args):
        if "status='approved'" in sql:
            return [{"id": i} for i in self.approved]
        if "invite_reminded_at IS NULL" in sql:
            return [{"id": i} for i in self.due]
        return [{"id": i, "email": "e@example.com", "name": "E", "reference": "KT-F30-EEEEE", "lang": "en", "updated_at": None} for i in self.expired]

    @asynccontextmanager
    async def transaction(self):
        yield


def sweep(conn, now):
    sent = []
    app = SimpleNamespace(state=SimpleNamespace(db_pool=Pool(conn)))
    with patch.object(founding_invites, "ensure_schema", noop), \
            patch.object(founding_invites, "_send_invite", lambda row, token, kind="invite": sent.append(kind) or True), \
            patch.object(founding_invites, "_notify", lambda r, k, row: sent.append(k)):
        out = run(founding_invites.sweep(app, now))
    return out, sent


def test_invites_go_out_one_week_before_launch_not_earlier():
    launch = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
    out, sent = sweep(SweepConn(launch, approved=[1, 2]), launch - timedelta(days=8))
    assert out["invited"] == 0 and sent == []
    out, sent = sweep(SweepConn(launch, approved=[1, 2]), launch - timedelta(days=7))
    assert out["invited"] == 2 and sent == ["invite", "invite"]


def test_sweep_reminds_and_expires():
    out, sent = sweep(SweepConn(None, due=[5], expired=[6]), datetime.now(timezone.utc))
    assert out == {"invited": 0, "reminded": 1, "expired": 1}
    assert sent == ["reminder", "invite_expired"]


def test_payment_approval_and_2fa_hooks_are_wired():
    import inspect
    from app import account_settings, binance_manual_admin
    assert 'advance_member(request.app, result.user_id, "active")' in inspect.getsource(binance_manual_admin.approve)
    assert 'advance_member(request.app, user["id"], "payment_pending")' in inspect.getsource(account_settings.enable_totp)


def test_registration_paths_check_invites():
    import inspect
    from app import google_oauth, v22_commercial
    assert "require_registration_open(request, normalize_email(payload.email), getattr(payload, \"invite_token\", None))" in inspect.getsource(v22_commercial.register_password_user)
    assert "consume_invite" in inspect.getsource(v22_commercial.register_password_user)
    src = inspect.getsource(google_oauth.complete)
    assert 'registration_allowed(request, attempt["identity"]["email"])' in src and "consume_invite" in src


def test_app_bound_emails_open_the_right_screen():
    from datetime import datetime, timezone
    from app import founding_mail
    captured = {}
    row = {"reference": "KT-F30-ABCDE", "email": "a@example.com", "name": "Ada", "lang": "tr", "updated_at": datetime.now(timezone.utc)}
    def fake_send(kind, **kw):
        captured[kind] = kw["action_url"]
    with patch.object(founding, "_app_base", lambda: "https://kaistrade.com"), patch("app.founding_mail.send", fake_send):
        for kind in ("payment_pending", "account_created", "active", "approved"):
            founding._notify(req(GateConn()), kind, row)
    assert captured["payment_pending"] == "https://kaistrade.com/billing"
    assert captured["account_created"] == "https://kaistrade.com/giris" and captured["active"] == "https://kaistrade.com/giris"
    assert captured["approved"] is None or "/basvuru-durumu" in captured["approved"]


def test_owner_gets_new_application_alert(monkeypatch):
    sent = []
    monkeypatch.setenv("FOUNDING_NOTIFY_EMAIL", "owner@example.com, bad-address")
    row = {"name": "Ada", "email": "a@example.com", "country": "Türkiye", "reference": "KT-F30-ABCDE", "futures_experience": "active",
           "trading_experience": "gt1y", "heard_from": "instagram", "reason": "Disiplinli işlem"}
    with patch("app.founding_mail.owner_alert", lambda **kw: sent.append(kw)):
        founding.notify_owner_new(row)
    assert [s["to_email"] for s in sent] == ["owner@example.com"]
    assert ("Binance Futures", "Aktif kullanıyor") in sent[0]["lines"]


def test_owner_alert_failure_never_raises(monkeypatch):
    monkeypatch.setenv("FOUNDING_NOTIFY_EMAIL", "owner@example.com")
    def boom(**_k):
        raise RuntimeError("mail down")
    with patch("app.founding_mail.owner_alert", boom):
        founding.notify_owner_new({"name": "A", "email": "a@x.co", "country": "-", "reference": None, "futures_experience": None,
                                   "trading_experience": None, "heard_from": None, "reason": ""})


class SeatConn:
    def __init__(self, seats, taken, waiting):
        self.seats, self.taken, self.waiting = seats, taken, waiting

    async def fetchrow(self, sql, *args):
        return {"seats": self.seats, "applications_open": True, "launch_at": None, "price_text": "", "registration_open": False}

    async def fetchval(self, sql, *args):
        return self.taken

    async def fetch(self, sql, *args):
        return self.waiting


def test_seat_freed_alert_suggests_oldest_waitlist(monkeypatch):
    sent = []
    monkeypatch.setenv("FOUNDING_NOTIFY_EMAIL", "owner@example.com")
    waiting = [{"name": "Lerato", "email": "l@example.com", "reference": "KT-F30-LLLLL"}]
    with patch("app.founding_mail.owner_alert", lambda **kw: sent.append(kw)):
        run(founding.notify_owner_seat_freed(SeatConn(30, 30, waiting), "test"))
        assert sent == []  # boş koltuk yok
        run(founding.notify_owner_seat_freed(SeatConn(30, 29, waiting), "test"))
    assert sent and ("Öneri 1", "Lerato · l@example.com · KT-F30-LLLLL") in sent[0]["lines"]
