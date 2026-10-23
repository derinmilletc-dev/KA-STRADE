import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import scan_rejections as sr  # noqa: E402

NOW = 1_000_000.0


def item(at, rejections=None, checks=None, **extra):
    row = {"at": at, "candidates": 0, "trades": 0, **extra}
    if rejections is not None:
        row["rejections"] = rejections
    if checks is not None:
        row["checks"] = checks
    return row


def test_clean_counts_keeps_only_positive_integers_with_string_keys():
    assert sr.clean_counts({"a": 3, "b": 0, "c": -1, "d": True, "e": 1.5, "f": "2", 7: 4, "": 1}) == {"a": 3}
    assert sr.clean_counts(None) == {} and sr.clean_counts([1]) == {}
    assert len(sr.clean_counts({f"k{i}": 1 for i in range(100)})) == sr.MAX_KEYS


def test_checks_are_limited_to_the_signal_gates():
    # failed_checks bazen 15m/1h/4h gibi veri aralığı da olabilir; bunlar kapı sayılmaz.
    fields = sr.history_fields({"signal_conditions": 30}, {"direction": 30, "mtf": 4, "15m": 9, "bogus": 2})
    assert fields == {"rejections": {"signal_conditions": 30}, "checks": {"direction": 30, "mtf": 4}}


def test_a_scan_with_no_rejections_is_still_recorded_as_a_scan_with_data():
    assert sr.history_fields({}, {}) == {"rejections": {}}


def test_summary_adds_only_the_last_24_hours_and_only_scans_with_data():
    history = [
        item(NOW - 90_000, {"stop_too_wide": 99}),                     # 24 saatten eski
        item(NOW - 3_600, {"signal_conditions": 30, "stop_too_wide": 2}, {"direction": 30, "mtf": 2}),
        item(NOW - 1_800, {"signal_conditions": 28, "confidence_low": 4}, {"direction": 28, "confidence": 4}),
        item(NOW - 900),                                               # döküm tutulmamış eski kayıt
        item(NOW + 500, {"stop_too_wide": 50}),                        # gelecekte: yok sayılır
    ]
    summary = sr.summarize(history, NOW)
    assert summary["scans"] == 2
    assert summary["since_epoch"] == NOW - 3_600
    assert summary["reasons"] == {"signal_conditions": 58, "stop_too_wide": 2, "confidence_low": 4}
    assert summary["checks"] == {"direction": 58, "mtf": 2, "confidence": 4}


def test_summary_is_safe_on_missing_or_corrupt_history():
    for history in (None, [], "x", [None, 5, {"at": "bad", "rejections": {}}, {"at": float("nan"), "rejections": {}}, {"rejections": {}}]):
        assert sr.summarize(history, NOW) == {"scans": 0, "since_epoch": None, "reasons": {}, "checks": {}}


def test_summary_does_not_change_the_stored_history():
    history = [item(NOW - 10, {"a": 1}, {"direction": 1})]
    before = repr(history)
    sr.summarize(history, NOW)
    assert repr(history) == before


def test_scan_recorder_stores_the_breakdown_only_when_it_is_given_and_keeps_old_counters():
    import pytest

    pytest.importorskip("fastapi")
    from app import original_v2_live as live

    scanner: dict = {}
    live._record_scanner_scan(scanner, NOW, 0)
    assert "rejections" not in scanner["history"][-1]
    live._record_scanner_scan(scanner, NOW + 1, 0, {"signal_conditions": 3, "bad": 0}, {"direction": 3, "15m": 5})
    assert scanner["history"][-1]["rejections"] == {"signal_conditions": 3}
    assert scanner["history"][-1]["checks"] == {"direction": 3}
    # Mevcut sayaç biçimi değişmedi.
    assert live._scanner_history_summary(scanner, NOW + 2) == {"scans": 2, "candidates": 0, "trades": 0}
    assert sr.summarize(scanner["history"], NOW + 2)["scans"] == 1
