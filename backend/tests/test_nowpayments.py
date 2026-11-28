import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from app import nowpayments_payments as nowpay

SECRET = "test-ipn-secret"


def sign(body: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha512).hexdigest()


def row(status="waiting", amount="119.90"):
    return {"status": status, "price_amount": Decimal(amount)}


def data(**extra):
    base = {"payment_status": "finished", "price_amount": 119.9, "price_currency": "usd",
            "pay_amount": 120.0, "actually_paid": 120.0}
    base.update(extra)
    return base


# ---- imza

def test_signature_accepts_sorted_compact_json():
    payload = {"payment_status": "finished", "order_id": "a" * 32, "price_amount": 119.9,
               "nested": {"z": 1, "a": [3, 2, {"b": 1, "a": 2}]}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # Gövde farklı sırada ve boşluklu gelse de imza sıralı biçimden hesaplanır.
    raw = json.dumps(payload, indent=2).encode()
    assert nowpay.verify_signature(raw, sign(canonical), SECRET)


def test_signature_keeps_number_literals_as_written():
    raw = b'{"price_amount":119.90,"order_id":"x","payment_status":"finished"}'
    literal = '{"order_id":"x","payment_status":"finished","price_amount":119.90}'
    assert nowpay.verify_signature(raw, sign(literal), SECRET)


def test_signature_rejects_wrong_secret_tampered_body_and_missing_header():
    payload = {"payment_status": "finished", "order_id": "x"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    raw = json.dumps(payload).encode()
    assert not nowpay.verify_signature(raw, sign(canonical, "başka-gizli"), SECRET)
    tampered = json.dumps({**payload, "payment_status": "waiting"}).encode()
    assert not nowpay.verify_signature(tampered, sign(canonical), SECRET)
    assert not nowpay.verify_signature(raw, None, SECRET)
    assert not nowpay.verify_signature(raw, "", SECRET)
    assert not nowpay.verify_signature(raw, sign(canonical), "")


def test_signature_rejects_garbage_bodies():
    assert not nowpay.verify_signature(b"not json", "abc", SECRET)
    assert not nowpay.verify_signature(b"\xff\xfe", "abc", SECRET)
    assert not nowpay.verify_signature(b'{"a":NaN}', "abc", SECRET)


def test_signature_is_case_insensitive_hex():
    canonical = '{"a":1}'
    assert nowpay.verify_signature(b'{"a": 1}', sign(canonical).upper(), SECRET)


# ---- karar

def test_finished_with_matching_amount_grants():
    assert nowpay.evaluate(row(), data()).action == "grant"


def test_finished_twice_is_duplicate_and_never_grants_again():
    assert nowpay.evaluate(row("finished"), data()).action == "duplicate"
    # Geç gelen eski durum, tamamlanmış ödemeyi geri almaz.
    assert nowpay.evaluate(row("finished"), data(payment_status="confirming")).action == "duplicate"
    assert nowpay.evaluate(row("finished"), data(payment_status="failed")).action == "duplicate"


@pytest.mark.parametrize("price", [119.0, 39.9, 0, "abc", None, 119.91])
def test_wrong_amount_never_grants(price):
    decision = nowpay.evaluate(row(), data(price_amount=price))
    assert decision.action == "mismatch" and decision.status == "mismatch"


def test_wrong_currency_never_grants():
    assert nowpay.evaluate(row(), data(price_currency="eur")).action == "mismatch"
    assert nowpay.evaluate(row(), data(price_currency=None)).action == "mismatch"


def test_smart_trade_amount_matches_its_own_row():
    assert nowpay.evaluate(row(amount="39.90"), data(price_amount="39.90")).action == "grant"
    assert nowpay.evaluate(row(amount="39.90"), data(price_amount=119.9)).action == "mismatch"


def test_underpaid_finished_does_not_grant():
    decision = nowpay.evaluate(row(), data(actually_paid=50, pay_amount=120))
    assert decision.action == "mismatch" and decision.status == "partially_paid"


def test_progress_statuses_only_update():
    for status in ("waiting", "confirming", "confirmed", "sending", "partially_paid", "failed", "refunded", "expired"):
        decision = nowpay.evaluate(row(), data(payment_status=status))
        assert decision.action == "update" and decision.status == status


def test_unknown_status_is_ignored():
    assert nowpay.evaluate(row(), data(payment_status="hacked")).action == "ignore"
    assert nowpay.evaluate(row(), {"price_amount": 119.9}).action == "ignore"


# ---- fatura adresi

def test_invoice_url_must_be_https_nowpayments():
    assert nowpay.safe_invoice_url("https://nowpayments.io/payment/?iid=1")
    assert nowpay.safe_invoice_url("https://sandbox.nowpayments.io/payment/?iid=1")
    for bad in ("http://nowpayments.io/x", "https://evil.com/nowpayments.io", "https://nowpayments.io.evil.com/x",
                "javascript:alert(1)", "", None, 5, "https://" + "a" * 600 + ".nowpayments.io"):
        assert nowpay.safe_invoice_url(bad) is None


# ---- ayar

def test_disabled_without_both_keys(monkeypatch):
    monkeypatch.delenv("NOWPAYMENTS_API_KEY", raising=False)
    monkeypatch.delenv("NOWPAYMENTS_IPN_SECRET", raising=False)
    assert not nowpay.enabled()
    monkeypatch.setenv("NOWPAYMENTS_API_KEY", "k")
    assert not nowpay.enabled()
    monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", "s")
    assert nowpay.enabled()


def test_sandbox_and_custom_base(monkeypatch):
    monkeypatch.delenv("NOWPAYMENTS_API_BASE", raising=False)
    monkeypatch.delenv("NOWPAYMENTS_SANDBOX", raising=False)
    assert nowpay.api_base() == nowpay.PRODUCTION_API
    monkeypatch.setenv("NOWPAYMENTS_SANDBOX", "true")
    assert nowpay.api_base() == nowpay.SANDBOX_API
    monkeypatch.setenv("NOWPAYMENTS_API_BASE", "http://insecure.example")
    with pytest.raises(Exception):
        nowpay.api_base()


# ---- bildirimi işleme (sahte veritabanı bağlantısı) ve uç

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
ORDER = "a" * 32


class FakeConn:
    """nowpayments_payments / nowpayments_periods için yalnız bu modülün kullandığı sorguları taklit eder."""

    def __init__(self, status="waiting", user_ok=True, existing_end=None):
        self.payment = {"id": ORDER, "user_id": "u1", "plan": "MASTER_MODE", "price_amount": Decimal("119.90"),
                        "status": status, "erased_at": None, "note": None, "completed_at": None}
        self.periods = {}
        self.user_ok = user_ok
        self.binance_end = existing_end

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        if "pg_advisory_xact_lock" in sql:
            return "SELECT 1"
        if "INSERT INTO nowpayments_periods" in sql:
            if args[0] in self.periods:
                return "INSERT 0 0"
            self.periods[args[0]] = (args[2], args[3])
            return "INSERT 0 1"
        if "UPDATE nowpayments_payments SET status='finished'" in sql:
            self.payment["status"] = "finished"
            return "UPDATE 1"
        if "UPDATE nowpayments_payments SET status='mismatch'" in sql:
            self.payment["status"] = "mismatch"
            return "UPDATE 1"
        if "UPDATE nowpayments_payments SET status=$2" in sql:
            self.payment["status"], self.payment["note"] = args[1], args[2]
            return "UPDATE 1"
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        if "FROM nowpayments_payments WHERE id=$1 FOR UPDATE" in sql:
            return dict(self.payment) if args[0] == ORDER else None
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if "commercial_auth_users" in sql:
            return self.user_ok
        if "FROM subscriptions" in sql:
            return None
        if "binance_manual_periods" in sql:
            return self.binance_end
        if "nowpayments_periods" in sql:
            return max((end for _, end in self.periods.values()), default=None)
        if "clock_timestamp()" in sql:
            return NOW
        raise AssertionError(sql)


def test_grant_creates_one_30_day_period_and_replay_does_nothing():
    conn = FakeConn()
    first = asyncio.run(nowpay.process_notification(conn, {**data(), "order_id": ORDER, "payment_id": 77}))
    assert first == ("granted", "u1")
    start, end = conn.periods[ORDER]
    assert start == NOW and end - start == timedelta(days=30)
    again = asyncio.run(nowpay.process_notification(conn, {**data(), "order_id": ORDER, "payment_id": 77}))
    assert again == ("duplicate", None)
    assert len(conn.periods) == 1


def test_new_period_starts_after_existing_membership_ends():
    later = NOW + timedelta(days=10)
    conn = FakeConn(existing_end=later)
    asyncio.run(nowpay.process_notification(conn, {**data(), "order_id": ORDER}))
    start, end = conn.periods[ORDER]
    assert start == later and end == later + timedelta(days=30)


def test_wrong_amount_creates_no_period():
    conn = FakeConn()
    result = asyncio.run(nowpay.process_notification(conn, {**data(price_amount=1), "order_id": ORDER}))
    assert result == ("mismatch", None) and conn.periods == {} and conn.payment["status"] == "mismatch"


def test_unknown_or_malformed_order_is_ignored():
    conn = FakeConn()
    assert asyncio.run(nowpay.process_notification(conn, {**data(), "order_id": "b" * 32})) == ("unknown_order", None)
    assert asyncio.run(nowpay.process_notification(conn, {**data(), "order_id": "bad"})) == ("unknown_order", None)
    assert asyncio.run(nowpay.process_notification(conn, data())) == ("unknown_order", None)
    assert conn.periods == {}


def test_unavailable_account_is_flagged_not_granted():
    conn = FakeConn(user_ok=False)
    result = asyncio.run(nowpay.process_notification(conn, {**data(), "order_id": ORDER}))
    assert result == ("needs_review", None) and conn.periods == {} and conn.payment["status"] == "mismatch"


def test_progress_update_does_not_grant():
    conn = FakeConn()
    result = asyncio.run(nowpay.process_notification(conn, {**data(payment_status="confirming"), "order_id": ORDER}))
    assert result == ("update", None) and conn.periods == {} and conn.payment["status"] == "confirming"


def make_client(conn):
    from app import nowpayments_routes
    app = FastAPI()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield conn

    app.state.db_pool = Pool()
    app.include_router(nowpayments_routes.router)
    return TestClient(app)


def post_ipn(client, payload, secret=SECRET, signed=True):
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    if signed:
        headers["x-nowpayments-sig"] = sign(json.dumps(payload, sort_keys=True, separators=(",", ":")), secret)
    return client.post("/api/v22/payments/nowpayments/ipn", content=body, headers=headers)


def test_ipn_endpoint_requires_valid_signature(monkeypatch):
    monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", SECRET)
    conn = FakeConn()
    client = make_client(conn)
    payload = {**data(), "order_id": ORDER}
    assert post_ipn(client, payload, signed=False).status_code == 401
    assert post_ipn(client, payload, secret="yanlış").status_code == 401
    assert conn.periods == {}
    ok = post_ipn(client, payload)
    assert ok.status_code == 200 and ok.json()["result"] == "granted"
    assert post_ipn(client, payload).json()["result"] == "duplicate"
    assert len(conn.periods) == 1


def test_ipn_endpoint_closed_without_secret(monkeypatch):
    monkeypatch.delenv("NOWPAYMENTS_IPN_SECRET", raising=False)
    client = make_client(FakeConn())
    assert post_ipn(client, {**data(), "order_id": ORDER}).status_code == 503


def test_ipn_endpoint_rejects_oversized_body(monkeypatch):
    monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", SECRET)
    client = make_client(FakeConn())
    response = client.post("/api/v22/payments/nowpayments/ipn", content=b"{" + b" " * 70000 + b"}",
                           headers={"x-nowpayments-sig": "x"})
    assert response.status_code == 413


# ---------------------------------------------------------------- üyelik: Binance elle ödeme ayarından bağımsız

class PeriodConn:
    def __init__(self, with_period=True, plan="SMART_TRADE"):
        now = datetime.now(timezone.utc)
        self.period = {"payment_id": "a" * 32, "user_id": "user-a", "period_start": now - timedelta(days=1),
                       "period_end": now + timedelta(days=29), "plan": plan, "status": "finished",
                       "payment_user_id": "user-a"} if with_period else None
        self.queries = []

    async def fetchrow(self, sql, *args):
        self.queries.append(sql)
        return self.period if "nowpayments_periods" in sql else None


def resolve_for(conn):
    from app import member_entitlements as entitlements
    return asyncio.run(entitlements.resolve(conn, {"subscriptions": []}, "user-a"))


def test_crypto_membership_opens_even_when_binance_manual_access_is_off(monkeypatch):
    monkeypatch.setenv("BINANCE_MANUAL_ACCESS_ENABLED", "false")
    monkeypatch.setenv("FREE_TRIAL_ENABLED", "false")
    monkeypatch.setenv("NOWPAYMENTS_API_KEY", "key")
    monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", SECRET)
    subscription = resolve_for(PeriodConn())
    assert subscription["master_trade_access"] is True
    assert subscription["plan"] == "SMART_TRADE" and subscription["payment_method"] == "nowpayments"


def test_no_crypto_period_means_no_access_when_binance_manual_access_is_off(monkeypatch):
    monkeypatch.setenv("BINANCE_MANUAL_ACCESS_ENABLED", "false")
    monkeypatch.setenv("FREE_TRIAL_ENABLED", "false")
    monkeypatch.setenv("NOWPAYMENTS_API_KEY", "key")
    monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", SECRET)
    assert resolve_for(PeriodConn(with_period=False))["master_trade_access"] is False


def test_crypto_lookup_is_skipped_when_nowpayments_is_not_configured(monkeypatch):
    monkeypatch.setenv("BINANCE_MANUAL_ACCESS_ENABLED", "false")
    monkeypatch.setenv("FREE_TRIAL_ENABLED", "false")
    monkeypatch.delenv("NOWPAYMENTS_API_KEY", raising=False)
    monkeypatch.delenv("NOWPAYMENTS_IPN_SECRET", raising=False)
    conn = PeriodConn()
    assert resolve_for(conn)["master_trade_access"] is False
    assert conn.queries == []


# ---------------------------------------------------------------- ödeme e-postası

def _mail(language, start_offset_days=0, plan="SMART_TRADE", amount=Decimal("39.90")):
    from app import nowpayments_email as mailer
    now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    start = now + timedelta(days=start_offset_days)
    return mailer.email_content(language=language, plan=plan, amount=amount, period_start=start,
                                period_end=start + timedelta(days=30), link="https://kaistrade.com/billing", now=now)


def test_first_payment_email_turkish():
    subject, text, html = _mail("tr")
    assert "Ödemen alındı" in subject and "Smart Trade" in text and "39,90 USD" in text
    assert "28 Aralık 2026" in text and "Panele git" in html and "https://kaistrade.com/billing" in html


def test_first_payment_email_english():
    subject, text, html = _mail("en")
    assert "Payment received" in subject and "39.90 USD" in text and "28 December 2026" in text and "Go to panel" in html


def test_early_renewal_email_says_extended_with_new_end_date():
    subject, text, _ = _mail("tr", start_offset_days=3)
    assert "uzatıldı" in subject and "31 Aralık 2026" in text
    assert "extended" in _mail("en", start_offset_days=3)[0]


def test_unknown_language_falls_back_to_turkish_and_values_are_escaped():
    assert "Ödemen alındı" in _mail("de")[0]
    assert "<script>" not in _mail("tr", plan="<script>")[2]


def test_email_failure_never_breaks_payment(monkeypatch):
    from app import nowpayments_email as mailer

    class Boom:
        async def fetchrow(self, *a):
            raise RuntimeError("db down")

    asyncio.run(mailer.notify_paid(Boom(), "a" * 32))


# ---------------------------------------------------------------- bitiş hatırlatması

class ReminderConn:
    def __init__(self, rows, fail_table=False):
        self.rows, self.claimed, self.deleted, self.fail_table = rows, set(), [], fail_table

    async def fetch(self, sql, *args):
        if self.fail_table:
            from asyncpg import UndefinedTableError
            raise UndefinedTableError("missing")
        return self.rows

    async def execute(self, sql, *args):
        if sql.startswith("INSERT INTO nowpayments_reminders"):
            if args[0] in self.claimed:
                return "INSERT 0 0"
            self.claimed.add(args[0])
            return "INSERT 0 1"
        if sql.startswith("DELETE FROM nowpayments_reminders"):
            self.deleted.append(args[0])
            self.claimed.discard(args[0])
        return "OK"

    async def fetchval(self, sql, *args):
        return "tr"


def _due_row():
    end = datetime.now(timezone.utc) + timedelta(days=3, hours=1)
    return {"payment_id": "b" * 32, "user_id": "user-a", "period_end": end, "plan": "SMART_TRADE", "email": "a@example.com"}


def _reminder_env(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:5173")


def test_reminder_email_turkish_and_english_with_days_left():
    from app import nowpayments_reminder as reminder
    now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
    end = now + timedelta(days=3, hours=2)
    subject, text, html = reminder.reminder_content(language="tr", plan="SMART_TRADE", period_end=end, link="https://kaistrade.com/billing", now=now)
    assert "4 gün" in subject and "otomatik yenilenmez" in text and "Üyeliği yenile" in html and "Smart Trade" in text
    subject, text, _ = reminder.reminder_content(language="en", plan="MASTER_MODE", period_end=end, link="https://kaistrade.com/billing", now=now)
    assert "4 days" in subject and "do not renew automatically" in text and "Master Mode" in text


def test_reminder_is_sent_once_per_period(monkeypatch):
    from app import nowpayments_reminder as reminder
    _reminder_env(monkeypatch)
    sent = []
    monkeypatch.setattr(reminder.mail, "send_email_message", lambda **kw: sent.append(kw))
    conn = ReminderConn([_due_row()])
    assert asyncio.run(reminder.send_due(conn)) == 1
    assert asyncio.run(reminder.send_due(conn)) == 0
    assert len(sent) == 1 and sent[0]["to_email"] == "a@example.com"


def test_failed_reminder_is_retried_next_round(monkeypatch):
    from app import nowpayments_reminder as reminder
    _reminder_env(monkeypatch)

    def boom(**kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(reminder.mail, "send_email_message", boom)
    conn = ReminderConn([_due_row()])
    assert asyncio.run(reminder.send_due(conn)) == 0
    assert conn.deleted == ["b" * 32] and conn.claimed == set()


def test_missing_reminder_table_is_skipped_quietly(monkeypatch):
    from app import nowpayments_reminder as reminder
    assert asyncio.run(reminder.send_due(ReminderConn([], fail_table=True))) == 0


def test_reminder_disabled_without_keys_or_when_switched_off(monkeypatch):
    from app import nowpayments_reminder as reminder
    monkeypatch.delenv("NOWPAYMENTS_API_KEY", raising=False)
    monkeypatch.delenv("NOWPAYMENTS_IPN_SECRET", raising=False)
    assert reminder.enabled() is False
    monkeypatch.setenv("NOWPAYMENTS_API_KEY", "key")
    monkeypatch.setenv("NOWPAYMENTS_IPN_SECRET", SECRET)
    assert reminder.enabled() is True
    monkeypatch.setenv("NOWPAYMENTS_REMINDER_ENABLED", "false")
    assert reminder.enabled() is False
