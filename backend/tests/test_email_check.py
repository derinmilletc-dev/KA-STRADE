"""E-posta denetimi: site ile aynı örnek dosyası (email_check_cases.json) üzerinden."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.email_check import check_email, rejection_message, rules  # noqa: E402

CASES = json.loads(Path(__file__).with_name("email_check_cases.json").read_text(encoding="utf-8"))


def test_shared_cases():
    failures = []
    for raw, status, suggestion in CASES:
        got = check_email(raw)
        if (got["status"], got["suggestion"]) != (status, suggestion):
            failures.append((raw, status, suggestion, got["status"], got["suggestion"]))
    assert not failures, failures


def test_rules_file_is_sane():
    r = rules()
    assert {"com", "net", "org", "tr", "io"} <= r["tlds"] and not {"con", "cmo", "ogr"} & r["tlds"]
    assert set(r["fuzzy"]) <= set(r["providers"])


def test_message_includes_suggestion():
    assert "ali@gmail.com" in rejection_message(check_email("ali@gmail.con"))
