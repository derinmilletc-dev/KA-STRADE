from app.kais_shadow_score import shadow_score


def _a(**kw):
    base = {"entry": 100.0, "ema": {"ema20": 99.0, "ema50": 97.0, "ema200": 90.0}, "macd": 1.0, "rsi": 60.0,
            "adx": 25.0, "volume_ratio": 1.2, "atr": 1.0, "resistance": 110.0}
    base.update(kw)
    return base


def test_clean_trend_has_no_penalty():
    r = shadow_score(_a())
    assert r["penalty"] == 0 and r["score"] == 94 and r["would_pass"]


def test_overextended_price_is_penalised():
    r = shadow_score(_a(entry=103.5))
    assert r["penalty"] == 14 and not r["would_pass"]


def test_near_resistance_is_penalised():
    assert shadow_score(_a(resistance=100.5))["penalty"] == 6


def test_missing_or_bad_data_returns_none():
    assert shadow_score(_a(atr=0)) is None
    assert shadow_score(_a(macd=None)) is None
    assert shadow_score({}) is None
