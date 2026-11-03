"""Pozisyon kartı güven puanı: yalnız okuma, her kapanmış 15 dk mumda bir kez, Akıllı Kâr Koruma'dan bağımsız."""
import asyncio

import pytest

from app import original_profit_guard as guard
from app import position_live_score as score

BASE = 1_800_000_000 // 900 * 900  # bir 15 dk mumunun açılışı


def at(candle: int, seconds: int = 30) -> float:
    return BASE + candle * 900 + seconds


class NoCallClient:
    """Puan modülü emir/hesap isteği yapmamalı: istemciye dokunulursa test düşer."""
    def __getattr__(self, name):
        raise AssertionError(f"istemci kullanılmamalı: {name}")


def plan(**extra):
    return {"id": "p1", "symbol": "XMRUSDT", "direction": "LONG", "status": "KORUMA AKTİF", "source": "ORIGINAL_V2_LIVE", **extra}


def snapshot(direction="LONG", amount="0.05"):
    sign = "" if direction == "LONG" else "-"
    return {"positions": [{"symbol": "XMRUSDT", "positionAmt": f"{sign}{amount}", "entryPrice": "350", "markPrice": "352"}]}


@pytest.fixture
def analyses(monkeypatch):
    calls = []
    script = []

    async def fake(client, symbol, candle, kline_fetch):
        calls.append((symbol, candle))
        item = script.pop(0) if script else ("LONG", 76)
        if item is None:
            return None, "mum_verisi_eksik_veya_bayat"
        return {"direction": item[0], "confidence": item[1]}, "ok"

    monkeypatch.setattr(guard, "fetch_analysis", fake)
    return calls, script


def run(state, snap, now):
    return asyncio.run(score.tick(state, snap, NoCallClient(), None, now=now))


def test_score_is_written_once_per_closed_candle(analyses):
    calls, script = analyses
    script.extend([("LONG", 76), ("LONG", 64)])
    state = {"plans": {"p1": plan()}}
    assert run(state, snapshot(), at(0)) is True
    live = state["plans"]["p1"][score.KEY]
    assert live["score"] == 76 and live["mismatch"] is False and live["candle"] == BASE and live["basis"] == "15m_candle_close"
    assert run(state, snapshot(), at(0, 400)) is False and len(calls) == 1  # aynı mum: yeniden hesap yok
    assert run(state, snapshot(), at(1)) is True and state["plans"]["p1"][score.KEY]["score"] == 64 and len(calls) == 2


def test_opposite_signal_has_no_score_but_records_opposite_confidence(analyses):
    _, script = analyses
    script.append(("SHORT", 81))
    state = {"plans": {"p1": plan()}}
    run(state, snapshot(), at(0))
    live = state["plans"]["p1"][score.KEY]
    assert live["score"] is None and live["mismatch"] is True and live["opposite_confidence"] == 81


def test_wait_signal_keeps_its_own_low_score(analyses):
    _, script = analyses
    script.append(("BEKLE", 52))
    state = {"plans": {"p1": plan()}}
    run(state, snapshot(), at(0))
    assert state["plans"]["p1"][score.KEY]["score"] == 52 and state["plans"]["p1"][score.KEY]["note"] == "bekle"


def test_missing_data_keeps_previous_score_and_retries_later(analyses):
    calls, script = analyses
    script.extend([("LONG", 76), None, ("LONG", 70)])
    state = {"plans": {"p1": plan()}}
    run(state, snapshot(), at(0))
    run(state, snapshot(), at(1))                       # yeni mum, veri eksik
    live = state["plans"]["p1"][score.KEY]
    assert live["score"] == 76 and live["candle"] == BASE and live["last_failure"] == "mum_verisi_eksik_veya_bayat"
    run(state, snapshot(), at(1, 50))                   # bekleme süresi dolmadı
    assert len(calls) == 2
    run(state, snapshot(), at(1, 30 + score.RETRY_SECONDS + 1))
    assert state["plans"]["p1"][score.KEY]["score"] == 70 and state["plans"]["p1"][score.KEY]["candle"] == BASE + 900


def test_manual_plans_also_get_a_score_but_closed_and_unmatched_do_not(analyses):
    calls, _ = analyses
    state = {"plans": {"m": plan(id="m", source="MANUAL")}}
    run(state, snapshot(), at(0))
    assert state["plans"]["m"][score.KEY]["score"] == 76
    for extra, snap in (({"status": "KAPANDI"}, snapshot()), ({}, snapshot("SHORT")), ({}, {"positions": []})):
        state = {"plans": {"p1": plan(**extra)}}
        before = len(calls)
        assert run(state, snap, at(0)) is False and score.KEY not in state["plans"]["p1"] and len(calls) == before


def test_one_calculation_per_coin_per_tick_and_errors_never_raise(analyses, monkeypatch):
    calls, _ = analyses
    state = {"plans": {"a": plan(id="a"), "b": plan(id="b")}}
    run(state, snapshot(), at(0))
    assert len(calls) == 1 and state["plans"]["a"][score.KEY]["score"] == state["plans"]["b"][score.KEY]["score"] == 76

    async def boom(*_args):
        raise RuntimeError("ağ hatası")
    monkeypatch.setattr(guard, "fetch_analysis", boom)
    state = {"plans": {"p1": plan()}}
    assert run(state, snapshot(), at(2)) is False  # hata yutulur, döngü düşmez


def test_smart_guard_records_are_never_touched(analyses):
    state = {"plans": {"p1": plan(**{guard.GUARD_KEY: {"mode": "NORMAL", "last_score": 90}})}}
    run(state, snapshot(), at(0))
    assert state["plans"]["p1"][guard.GUARD_KEY] == {"mode": "NORMAL", "last_score": 90}
