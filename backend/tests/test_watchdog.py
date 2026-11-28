from app import watchdog

T = 1_000_000.0


def run(state, failed, now):
    return watchdog.decide(state, failed, now)


def test_one_or_two_failures_do_not_alert():
    state = {}
    for step in range(2):
        state, kind = run(state, ["Web sitesi"], T + step * 60)
        assert kind is None


def test_third_failure_alerts_once_then_stays_quiet_until_reminder():
    state = {}
    kinds = []
    for step in range(6):
        state, kind = run(state, ["Sunucu"], T + step * 60)
        kinds.append(kind)
    assert kinds == [None, None, "down", None, None, None]
    state, kind = run(state, ["Sunucu"], T + 2 * 60 + watchdog.REMIND_SECONDS)
    assert kind == "remind"


def test_recovery_sends_one_mail_and_resets():
    state = {}
    for step in range(3):
        state, _ = run(state, ["Sunucu"], T + step * 60)
    state, kind = run(state, [], T + 400)
    assert kind == "recovered" and state["fails"] == 0 and state["alerted"] is False
    assert run(state, [], T + 460)[1] is None


def test_short_blip_never_mails():
    state, _ = run({}, ["Sunucu"], T)
    state, kind = run(state, [], T + 60)
    assert kind is None and state["fails"] == 0


def test_messages_are_turkish_and_escape_names():
    subject, text, html = watchdog.message("down", ["<b>x</b>"], None, T)
    assert subject.startswith("KaisTrade DÜŞTÜ") and "<b>x</b>" not in html
    subject, text, _ = watchdog.message("recovered", [], T - 600, T)
    assert "tekrar çalışıyor" in subject and "10 dakika" in text


def test_recipient_prefers_alert_email(monkeypatch):
    monkeypatch.setenv("FOUNDING_NOTIFY_EMAIL", "b@example.com")
    monkeypatch.setenv("ALERT_EMAIL", "a@example.com")
    assert watchdog.recipient() == "a@example.com"
    monkeypatch.delenv("ALERT_EMAIL")
    assert watchdog.recipient() == "b@example.com"


def test_failed_send_is_retried_next_minute(monkeypatch, tmp_path):
    import json
    from app import email_service
    monkeypatch.setattr(watchdog, "STATE_FILE", tmp_path / "s.json")
    monkeypatch.setattr(watchdog, "checks", lambda: [("x", "http://x")])
    monkeypatch.setattr(watchdog, "probe", lambda url: False)
    monkeypatch.setenv("ALERT_EMAIL", "a@example.com")
    sent = []
    def boom(**kw):
        sent.append(kw["subject"]); raise RuntimeError("mail down")
    monkeypatch.setattr(email_service, "send_email_message", boom)
    (tmp_path / "s.json").write_text(json.dumps({"fails": 2}))
    assert watchdog.run() == 1 and len(sent) == 1
    assert watchdog.run() == 1 and len(sent) == 2   # ilk gönderim başarısızdı, tekrar denendi
