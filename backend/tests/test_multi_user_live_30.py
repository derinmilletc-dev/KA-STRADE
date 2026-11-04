"""30 eşzamanlı üye: canlı işlem güvenliği doğrulaması (PR #491).

Burada yalnız paylaşım/izolasyon katmanı sınanır; strateji kuralları, risk kapıları ve emir mantığı
gerçek kodla çalışır ve hiçbir test onları değiştirmez.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import binance_demo
from app import binance_kline_stream as kline_module
from app import original_v2_demo_execution as execution
from app import v25_execution
from app.binance_kline_stream import BinanceKlineStream
from app.execution_core import MIN_CONFIDENCE_ENV, MTF_ALLOW_EITHER_TIMEFRAME_ENV
from app.live_registry import CURRENT_USER, LiveRegistry, UserAwareState
from app.strategies import original_v2_demo as strategy
from app.strategies import original_v2_pullback as pullback

import test_original_v2_demo as decisions

MEMBERS = [f"member-{index:02d}" for index in range(30)]
APP = Path(__file__).parents[1] / "app"


def run(coro):
    return asyncio.run(coro)


async def as_member(user_id, coro_factory):
    token = CURRENT_USER.set(user_id)
    try:
        return await coro_factory()
    finally:
        CURRENT_USER.reset(token)


def fingerprint(result):
    return json.dumps(dataclasses.asdict(result), sort_keys=True, default=str)


# ---------------------------------------------------------------------------------------------
# 1) Strateji önbellek anahtarı sonucu etkileyen her girdiyi kapsar
# ---------------------------------------------------------------------------------------------

def _constant(module_source: str, name: str) -> str | None:
    match = re.search(rf"^{name}\s*=\s*\"([^\"]+)\"", module_source, re.M)
    return match.group(1) if match else None


def test_every_setting_the_strategy_reads_is_part_of_the_cache_key():
    """Kayma koruması: strateji koduna yeni bir ortam ayarı eklenirse bu test anahtar güncellenene kadar kırılır."""
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in [
            APP / "strategies" / "original_v2_demo.py", APP / "strategies" / "original_v2_pullback.py",
            APP / "strategies" / "kais_original.py", APP / "analysis.py",
        ]
    }
    read = set()
    for source in sources.values():
        for name in re.findall(r"os\.getenv\(\s*([A-Z0-9_]+)", source):
            read.add(_constant(source, name) or name)
    # Anahtardaki ham ayarlar + anahtarın başka alanlarıyla temsil edilenler.
    in_key_raw = {MIN_CONFIDENCE_ENV, strategy.KAIS2_GATE_FLAG, MTF_ALLOW_EITHER_TIMEFRAME_ENV}
    represented = {
        strategy.LIVE_SIGNAL_INTERVAL_FLAG: "signal_interval anahtarda",
        pullback.MODEL_ENV: "kind (pullback/universe) anahtarda",
        strategy.FEATURE_FLAG: "çağrıda enabled=True sabit; ayar okunmaz",
    }
    assert read <= in_key_raw | set(represented), read - in_key_raw - set(represented)
    # Stratejinin execution_core'dan okuduğu ayar fonksiyonları da yalnız bunlar.
    used = set()
    for source in sources.values():
        used |= set(re.findall(r"\b(configured_[a-z_]+)\(", source))
    assert used <= {"configured_min_confidence", "configured_mtf_allow_either_timeframe", "configured_model"}, used
    key_source = (APP / "original_v2_demo_execution.py").read_text(encoding="utf-8")
    for name in ("MIN_CONFIDENCE_ENV", "strategy.KAIS2_GATE_FLAG", "MTF_ALLOW_EITHER_TIMEFRAME_ENV"):
        assert name in key_source


@pytest.mark.parametrize("env_name, first, second", [
    (MIN_CONFIDENCE_ENV, "80", "90"),
    ("PROTREBOT_KAIS2_LIVE_GATE", "true", "false"),
    (MTF_ALLOW_EITHER_TIMEFRAME_ENV, "false", "true"),
])
def test_changing_any_setting_recomputes(monkeypatch, env_name, first, second):
    frames = {"15m": [{"open_time": 1, "close": "1"}]}
    calls = []
    monkeypatch.setenv(env_name, first)
    execution.shared_strategy_result("universe", "30m", "BTCUSDT", 900, frames, (True, True), lambda: calls.append(1))
    execution.shared_strategy_result("universe", "30m", "BTCUSDT", 900, frames, (True, True), lambda: calls.append(1))
    monkeypatch.setenv(env_name, second)
    execution.shared_strategy_result("universe", "30m", "BTCUSDT", 900, frames, (True, True), lambda: calls.append(1))
    assert len(calls) == 2


def test_key_fields_each_separate_results():
    frames = {"15m": [{"open_time": 1, "close": "1"}]}
    base = dict(kind="universe", signal_interval="30m", symbol="BTCUSDT", at=900, frames=frames, allowed=(True, True))
    variants = [
        {}, {"kind": "pullback"}, {"kind": "demo"}, {"signal_interval": "15m"}, {"symbol": "ETHUSDT"}, {"at": 1800},
        {"allowed": (True, False)}, {"allowed": (False, True)},
        {"frames": {"15m": [{"open_time": 1, "close": "1.0000001"}]}},
        {"frames": {"15m": [{"open_time": 2, "close": "1"}]}},
        {"frames": {"15m": [{"open_time": 1, "close": "1"}], "1h": []}},
    ]
    seen = []
    for change in variants:
        arguments = {**base, **change}
        value = execution.shared_strategy_result(*arguments.values(), lambda: object())
        seen.append(id(value))
    assert len(execution.STRATEGY_CACHE_STATS) and execution.STRATEGY_CACHE_STATS["misses"] >= len(variants)
    assert len(execution._STRATEGY_CACHE) == len(variants)  # her varyant ayrı anahtar


def test_invalid_setting_still_fails_only_where_it_is_read(monkeypatch):
    """Anahtar ayarların ham metnini kullanır: geçersiz KAIS2 ayarı pullback/demo yolunu bozmaz (önceki davranış)."""
    monkeypatch.setenv("PROTREBOT_KAIS2_LIVE_GATE", "maybe")
    assert execution.shared_strategy_result("pullback", "15m", "BTCUSDT", 900, {}, (True, True), lambda: "ok") == "ok"


# ---------------------------------------------------------------------------------------------
# Sentetik çok coinli piyasa (her coin ve aralık için farklı, belirlenimci rastgele yürüyüş)
# ---------------------------------------------------------------------------------------------

AT = 1_800_000_000 // 14_400 * 14_400  # tüm aralıkların açılışıyla hizalı karar anı
STEP = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400}
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT"]


def synthetic_klines(symbol: str, interval: str, limit: int) -> list[list]:
    rnd = random.Random(f"{symbol}:{interval}")
    step = STEP[interval]
    drift = rnd.choice([-0.004, -0.002, 0.002, 0.004])
    price = 100.0 + rnd.random() * 50
    rows = []
    for index in range(limit):
        open_time = AT - (limit - 1 - index) * step  # son satır: şu an açık mum
        close = price * (1 + drift + rnd.gauss(0, 0.006))
        high = max(price, close) * (1 + abs(rnd.gauss(0, 0.003)))
        low = min(price, close) * (1 - abs(rnd.gauss(0, 0.003)))
        rows.append([open_time * 1000, f"{price:.6f}", f"{high:.6f}", f"{low:.6f}", f"{close:.6f}",
                     "1000", (open_time + step) * 1000 - 1, "50000000"])
        price = close
    return rows


class MarketClient:
    """Üye başına ayrı istemci; aynı halka açık piyasa verisini döndürür."""

    reference = None
    total_kline_calls = 0

    def __init__(self, member: str):
        self.member = member
        if MarketClient.reference is None:
            info = decisions.fixtures.open_dataset().metadata["exchange_info"]
            MarketClient.reference = next(row for row in info["symbols"] if row["symbol"] == "BTCUSDT")

    async def public_get(self, path, params=None):
        if path == "/fapi/v1/exchangeInfo":
            return {"symbols": [{**json.loads(json.dumps(self.reference)), "symbol": symbol} for symbol in SYMBOLS]}
        assert path == "/fapi/v1/klines"
        MarketClient.total_kline_calls += 1
        await asyncio.sleep(0.002)  # ağ gecikmesi: eşzamanlı istekler gerçekten çakışsın
        return synthetic_klines(params["symbol"], params["interval"], params["limit"])


def shared_stream() -> BinanceKlineStream:
    stream = BinanceKlineStream(wall_clock=lambda: AT + 5)
    stream._subscribed = frozenset((symbol, interval) for symbol in SYMBOLS for interval in strategy.LIVE_KLINE_INTERVALS)
    return stream


async def member_scan(member: str, stream: BinanceKlineStream, occupied: set[str]):
    client = MarketClient(member)

    async def fetch(symbol, interval, limit):
        return await stream.get_candles(symbol, interval, limit, lambda params: client.public_get("/fapi/v1/klines", params))

    return await as_member(member, lambda: execution.live_candidates(
        client, AT, occupied, list(SYMBOLS), require_complete=True, kline_fetch=fetch,
    ))


def scan_summary(eligible, results):
    return (
        [(candidate["symbol"], json.dumps(candidate, sort_keys=True, default=str)) for candidate, _ in eligible],
        {symbol: fingerprint(value) for symbol, value in sorted(results.items())},
    )


# ---------------------------------------------------------------------------------------------
# 2) 30 üye aynı anda tarar: sonuçlar önbelleksiz hesapla birebir aynı, indirme tek, kopyalar bağımsız
# ---------------------------------------------------------------------------------------------

def test_thirty_members_scan_concurrently_with_identical_decisions(monkeypatch):
    occupied_for = {member: ({"BTCUSDT"} if index % 3 == 0 else set()) for index, member in enumerate(MEMBERS)}

    # Önbelleksiz referans: her açık-pozisyon varyantı için tek başına, paylaşım katmanı kapalı.
    with patch.object(execution, "shared_strategy_result", lambda *args: args[-1]()):
        baseline = {
            frozenset(occupied): scan_summary(*run(member_scan("solo", shared_stream(), set(occupied))))
            for occupied in ({"BTCUSDT"}, set())
        }
    execution._STRATEGY_CACHE.clear()
    execution.STRATEGY_CACHE_STATS.update(hits=0, misses=0)
    MarketClient.total_kline_calls = 0
    stream = shared_stream()

    async def everyone():
        return await asyncio.gather(*(member_scan(member, stream, occupied_for[member]) for member in MEMBERS))

    outputs = run(everyone())

    for member, (eligible, results) in zip(MEMBERS, outputs):
        assert scan_summary(eligible, results) == baseline[frozenset(occupied_for[member])], member
    # Strateji: coin başına bir hesap, kalan 29 üye kopya aldı.
    assert execution.STRATEGY_CACHE_STATS == {"misses": len(SYMBOLS), "hits": len(SYMBOLS) * (len(MEMBERS) - 1)}
    # Mum indirme: coin x aralık başına tek REST çağrısı (30 üye için).
    assert MarketClient.total_kline_calls == len(SYMBOLS) * len(strategy.LIVE_KLINE_INTERVALS)
    assert stream.rest_backfills == MarketClient.total_kline_calls
    assert not stream._backfills

    # Bir üyenin kendi sonucunda yaptığı değişiklik diğerlerine ve önbelleğe sızmaz.
    first_results = outputs[0][1]
    for value in first_results.values():
        value.legacy["analysis"] = {"direction": "LONG", "confidence": 999, "tampered": True}
        value.legacy["tampered"] = True
    for member_results in (outputs[1][1], outputs[29][1]):
        assert all("tampered" not in value.legacy for value in member_results.values())
    late = run(member_scan("member-late", stream, set()))
    assert scan_summary(*late) == baseline[frozenset()]
    assert all(value.legacy is not first_results[symbol].legacy for symbol, value in late[1].items())


def test_scan_results_are_distinct_objects_per_member():
    stream = shared_stream()

    async def two():
        return await asyncio.gather(member_scan("a", stream, set()), member_scan("b", stream, set()))

    (_, first), (_, second) = run(two())
    for symbol in SYMBOLS:
        assert first[symbol] == second[symbol]
        assert first[symbol] is not second[symbol]
        assert first[symbol].legacy is not second[symbol].legacy


# ---------------------------------------------------------------------------------------------
# 3) 30 üyenin bakiye / pozisyon / hesap verisi karışmaz (gerçek v25 + binance_demo zinciri)
# ---------------------------------------------------------------------------------------------

def member_application():
    application = SimpleNamespace(state=UserAwareState())
    registry = LiveRegistry(application, v25_execution.new_member_state, None)
    application.state.live_registry = registry
    application.state.maintenance = {"mode": "NORMAL"}
    return application, registry


def account_of(index: int) -> dict:
    return {
        "wallet_balance": 1000.0 + index, "available_balance": 900.0 + index,
        "positions": [{"symbol": SYMBOLS[index % len(SYMBOLS)], "positionAmt": str(index + 1), "owner": index}],
        "open_orders": [{"clientOrderId": f"own-{index}"}], "open_algo_orders": [], "open_algo_orders_available": True,
        "algo_orders_quality": "VALID_EMPTY", "hedge_mode": False, "multi_assets_mode": False,
    }


def test_thirty_members_account_data_never_mix():
    application, registry = member_application()
    clients = {
        member: SimpleNamespace(application=application, api_key=f"key-{member}-xxxxxxxx", secret_key=f"secret-{member}")
        for member in MEMBERS
    }
    index_of = {client.api_key: index for index, client in enumerate(clients.values())}
    upstream = {member: 0 for member in MEMBERS}
    active = {"now": 0, "max": 0, "per_member_max": 0}
    per_member_active = {member: 0 for member in MEMBERS}

    async def fake_inner(client, correlation_id, evidence_state=None):
        member = CURRENT_USER.get()
        assert client is clients[member]  # istemci, bağlı üyenin kendi istemcisi
        upstream[member] += 1
        active["now"] += 1
        per_member_active[member] += 1
        active["max"] = max(active["max"], active["now"])
        active["per_member_max"] = max(active["per_member_max"], per_member_active[member])
        await asyncio.sleep(random.uniform(0.005, 0.02))
        active["now"] -= 1
        per_member_active[member] -= 1
        return account_of(index_of[client.api_key])

    async def member_reads(member):
        async def reads():
            rows = await asyncio.gather(*(
                v25_execution.account_snapshot(clients[member], cached=True) for _ in range(5)
            ))
            fresh = await v25_execution.account_snapshot(clients[member], cached=False)
            return [*rows, fresh]
        return await as_member(member, reads)

    async def everyone():
        return await asyncio.gather(*(member_reads(member) for member in MEMBERS))

    with patch.object(binance_demo, "_account_snapshot", fake_inner), patch.object(v25_execution, "persist_state"):
        started = time.perf_counter()
        outputs = run(everyone())
        elapsed = time.perf_counter() - started

    for index, (member, rows) in enumerate(zip(MEMBERS, outputs)):
        for row in rows:
            assert row["wallet_balance"] == 1000.0 + index, member
            assert row["positions"][0]["owner"] == index
            assert row["open_orders"] == [{"clientOrderId": f"own-{index}"}]
        assert upstream[member] == 2  # 5 eşzamanlı önbellekli okuma -> 1 indirme, + 1 zorunlu taze okuma
    assert set(registry.account_caches) == set(MEMBERS)
    assert active["per_member_max"] == 1  # aynı üyenin okumaları sırayla
    assert active["max"] >= 10  # farklı üyeler birbirini beklemiyor
    assert elapsed < 1.5


def test_member_cache_invalidation_and_mutation_stay_private():
    application, registry = member_application()
    clients = {member: SimpleNamespace(application=application, api_key=f"key-{member}-xxxxxxxx", secret_key="s")
               for member in MEMBERS[:5]}
    index_of = {client.api_key: index for index, client in enumerate(clients.values())}
    upstream = {member: 0 for member in clients}

    async def fake_inner(client, correlation_id, evidence_state=None):
        upstream[CURRENT_USER.get()] += 1
        return account_of(index_of[client.api_key])

    async def scenario():
        for member in clients:
            await as_member(member, lambda m=member: v25_execution.account_snapshot(clients[m], cached=True))
        # Üye 0 kendi kopyasını bozar: önbellek ve diğer üyeler etkilenmez.
        tampered = await as_member(MEMBERS[0], lambda: v25_execution.account_snapshot(clients[MEMBERS[0]], cached=True))
        tampered["wallet_balance"] = -1
        tampered["positions"].clear()
        # Üye 1 emir gönderir (BinanceLiveClient'ın non-GET önbellek geçersizleştirmesiyle aynı kod).
        def invalidate():
            store = getattr(application.state, "live_account_cache", None)
            store.update(value=None, expires=0.0, generation=store.get("generation", 0) + 1)
        token = CURRENT_USER.set(MEMBERS[1]); invalidate(); CURRENT_USER.reset(token)
        return {member: await as_member(member, lambda m=member: v25_execution.account_snapshot(clients[m], cached=True))
                for member in clients}

    with patch.object(binance_demo, "_account_snapshot", fake_inner), patch.object(v25_execution, "persist_state"):
        final = run(scenario())

    assert final[MEMBERS[0]]["wallet_balance"] == 1000.0 and final[MEMBERS[0]]["positions"]
    assert upstream == {MEMBERS[0]: 1, MEMBERS[1]: 2, MEMBERS[2]: 1, MEMBERS[3]: 1, MEMBERS[4]: 1}


def test_unbound_context_never_sees_a_member_cache():
    application, registry = member_application()
    token = CURRENT_USER.set(MEMBERS[0])
    try:
        application.state.live_account_cache = {"value": "member-0"}
    finally:
        CURRENT_USER.reset(token)
    assert getattr(application.state, "live_account_cache", None) is None  # sistem/bağsız bağlam
    assert registry.current() is registry.system


# ---------------------------------------------------------------------------------------------
# 4) Aynı üyeye eşzamanlı mükerrer işlem açılamaz (gerçek execute_live_order kilidi)
# ---------------------------------------------------------------------------------------------

def test_same_member_entries_serialize_and_members_run_in_parallel():
    application, registry = member_application()
    for member in MEMBERS:
        state = registry.state_for(member)
        state.update(recovery_ready=True, real_trading_locked=False, armed_until=time.time() + 600, connected=True)
    inside = {member: 0 for member in MEMBERS}
    seen = {"max_member": 0, "now": 0, "max_total": 0, "states": set()}

    async def guarded_snapshot(client, *args, **kwargs):
        member = CURRENT_USER.get()
        state = application.state.v25_execution
        assert state is registry.state_for(member) and state["_live_user_id"] == member
        seen["states"].add(id(state))
        inside[member] += 1
        seen["now"] += 1
        seen["max_member"] = max(seen["max_member"], inside[member])
        seen["max_total"] = max(seen["max_total"], seen["now"])
        await asyncio.sleep(0.02)
        inside[member] -= 1
        seen["now"] -= 1
        # Gerçek akış burada hesap kapılarına geçer; test emir göndermeden durur.
        raise v25_execution.LiveExchangeError("test durdurma", http_status=423)

    body = v25_execution.LiveOrderRequest(
        symbol="BTCUSDT", direction="LONG", margin_usdt=5, leverage=1, stop_loss=98, tp1=102, tp2=104, tp3=106,
    )

    async def attempt(member):
        return await as_member(member, lambda: v25_execution.execute_live_order(
            application, body, source="MANUAL", credentials=("k" * 16, "s" * 16),
        ))

    async def everyone():
        # Her üye aynı coin için aynı anda 3 giriş dener (çift tıklama / iki sekme / yarış).
        return await asyncio.gather(*(attempt(member) for member in MEMBERS for _ in range(3)), return_exceptions=True)

    with patch.object(v25_execution, "readiness_for", return_value={"ready": True, "gates": []}), \
            patch.object(v25_execution, "client_for_with_credentials", return_value=SimpleNamespace()), \
            patch.object(v25_execution, "account_snapshot", guarded_snapshot), \
            patch.object(v25_execution, "persist_state"):
        started = time.perf_counter()
        outcomes = run(everyone())
        elapsed = time.perf_counter() - started

    assert all(isinstance(outcome, Exception) for outcome in outcomes)
    assert seen["max_member"] == 1  # aynı üyenin girişleri asla aynı anda kilit içinde değil
    assert seen["max_total"] >= 10  # farklı üyeler paralel
    assert len(seen["states"]) == len(MEMBERS)
    assert elapsed < 30 * 3 * 0.02  # tek global kilit olsaydı en az 1.8 sn sürerdi
    for member in MEMBERS:
        assert not registry.state_for(member)["intents"]  # gönderilmeyen niyet kaydı geride kalmaz


def test_entry_locks_are_per_member_objects():
    application, registry = member_application()
    locks = {member: registry.state_for(member)["lock"] for member in MEMBERS}
    assert len({id(lock) for lock in locks.values()}) == len(MEMBERS)
    assert all(registry.state_for(member)["lock"] is locks[member] for member in MEMBERS)


def test_auto_entry_intent_is_deterministic_per_candle():
    """Otomatik giriş niyet kimliği coin+aralık+mumdan türetilir; aynı mumda ikinci kez emir kimliği üretilmez."""
    source = (APP / "v25_execution.py").read_text(encoding="utf-8")
    assert 'intent_id = f"auto-{symbol}-{state[\'policy\'][\'interval\']}-{candle_id}"' in source
    assert "if intent_id in state[\"intents\"]:" in source
    assert v25_execution.client_id_for("ENTRY", "auto-BTCUSDT-15m-1") == v25_execution.client_id_for("ENTRY", "auto-BTCUSDT-15m-1")


# ---------------------------------------------------------------------------------------------
# 5) Tek indirme mekanizması hata, takılma ve iptal durumunda kilitlenmez
# ---------------------------------------------------------------------------------------------

def single_stream() -> BinanceKlineStream:
    stream = BinanceKlineStream(wall_clock=lambda: AT + 5)
    stream._subscribed = frozenset({("BTCUSDT", "15m")})
    return stream


def test_failed_shared_download_reaches_every_waiter_then_retries():
    stream = single_stream()
    calls = []

    async def failing(params):
        calls.append(params)
        await asyncio.sleep(0.01)
        if len(calls) == 1:
            raise v25_execution.LiveRateLimitError("Binance 429", retry_after=3)
        return synthetic_klines("BTCUSDT", "15m", params["limit"])

    async def scenario():
        first = await asyncio.gather(*(stream.get_candles("BTCUSDT", "15m", 260, failing) for _ in range(30)),
                                     return_exceptions=True)
        assert not stream._backfills  # hata sonrası kayıt temizlendi
        second = await stream.get_candles("BTCUSDT", "15m", 260, failing)
        return first, second

    loop_errors = []

    async def with_handler():
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: loop_errors.append(context))
        return await scenario()

    first, second = run(with_handler())
    assert all(isinstance(item, v25_execution.LiveRateLimitError) and item.retry_after == 3 for item in first)
    assert len(second) == 260 and len(calls) == 2
    assert not loop_errors


def test_hung_download_does_not_block_new_callers(monkeypatch):
    monkeypatch.setattr(kline_module, "BACKFILL_JOIN_MAX_SECONDS", 0.05)
    stream = single_stream()
    release = asyncio.Event
    calls = []

    async def scenario():
        gate = release()

        async def fetch(params):
            calls.append(params)
            if len(calls) == 1:
                await gate.wait()  # ilk indirme takılı
            return synthetic_klines("BTCUSDT", "15m", params["limit"])

        stuck = asyncio.ensure_future(stream.get_candles("BTCUSDT", "15m", 260, fetch))
        await asyncio.sleep(0.01)
        with pytest.raises(asyncio.TimeoutError):  # katılan, kendi zaman aşımıyla çıkabilir
            await asyncio.wait_for(stream.get_candles("BTCUSDT", "15m", 260, fetch), timeout=0.02)
        await asyncio.sleep(0.06)  # takılma süresi sınırı aşıldı
        fresh = await asyncio.wait_for(stream.get_candles("BTCUSDT", "15m", 260, fetch), timeout=1)
        gate.set()
        await asyncio.wait_for(stuck, timeout=1)
        await asyncio.sleep(0)
        return fresh

    assert len(run(scenario())) == 260
    assert len(calls) == 2
    assert not stream._backfills and not stream._backfill_started


def test_all_waiters_cancelled_download_finishes_quietly():
    stream = single_stream()
    loop_errors = []

    async def scenario():
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: loop_errors.append(context))

        async def fetch(params):
            await asyncio.sleep(0.02)
            raise RuntimeError("upstream down")

        waiters = [asyncio.ensure_future(stream.get_candles("BTCUSDT", "15m", 260, fetch)) for _ in range(5)]
        await asyncio.sleep(0.005)
        for waiter in waiters:
            waiter.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        await asyncio.sleep(0.05)

    run(scenario())
    assert not stream._backfills
    assert not loop_errors  # "Task exception was never retrieved" yok


def test_other_symbols_are_not_blocked_by_one_slow_download():
    stream = BinanceKlineStream(wall_clock=lambda: AT + 5)
    stream._subscribed = frozenset({("BTCUSDT", "15m"), ("ETHUSDT", "15m")})

    async def scenario():
        gate = asyncio.Event()

        async def fetch(params):
            if params["symbol"] == "BTCUSDT":
                await gate.wait()
            return synthetic_klines(params["symbol"], "15m", params["limit"])

        slow = asyncio.ensure_future(stream.get_candles("BTCUSDT", "15m", 260, fetch))
        await asyncio.sleep(0)
        fast = await asyncio.wait_for(stream.get_candles("ETHUSDT", "15m", 260, fetch), timeout=0.5)
        gate.set()
        await slow
        return fast

    assert len(run(scenario())) == 260


def test_ban_is_checked_per_caller_before_joining(monkeypatch):
    stream = single_stream()
    monkeypatch.setattr(kline_module.BINANCE_GLOBAL_BAN, "remaining_seconds", lambda: 60)
    monkeypatch.setattr(kline_module, "protected_lane_available", lambda: False)

    async def forbidden(params):
        raise AssertionError("ban sırasında REST çağrısı yapılmamalı")

    with pytest.raises(kline_module.KlineStreamUnavailable):
        run(stream.get_candles("BTCUSDT", "15m", 260, forbidden))
    assert not stream._backfills


@pytest.mark.parametrize("model", [pullback.MODEL_LEGACY, pullback.MODEL_PULLBACK])
def test_thirty_members_real_fixture_signals_identical_with_and_without_sharing(monkeypatch, model):
    """Gerçek fikstür verisi (8 uygun BUY adayı): 30 üye, farklı açık pozisyonlarla, iki giriş modelinde."""
    import test_original_v2_live as live_tests

    monkeypatch.setenv(pullback.MODEL_ENV, model)
    client = live_tests.CandleClient()
    symbols = list(decisions.SYMBOLS)
    occupied_for = {member: set(symbols[: index % 4]) for index, member in enumerate(MEMBERS)}

    async def scan(member, occupied):
        return await as_member(member, lambda: execution.live_candidates(
            client, decisions.fixtures.AT, occupied, symbols, require_complete=True,
        ))

    with patch.object(execution, "shared_strategy_result", lambda *args: args[-1]()):
        baseline = {
            frozenset(occupied): scan_summary(*run(scan("solo", set(occupied))))
            for occupied in {frozenset(value) for value in occupied_for.values()}
        }
    execution._STRATEGY_CACHE.clear()
    execution.STRATEGY_CACHE_STATS.update(hits=0, misses=0)

    async def everyone():
        return await asyncio.gather(*(scan(member, occupied_for[member]) for member in MEMBERS))

    outputs = run(everyone())
    for member, (eligible, results) in zip(MEMBERS, outputs):
        assert scan_summary(eligible, results) == baseline[frozenset(occupied_for[member])], member
        assert not {candidate["symbol"] for candidate, _ in eligible} & occupied_for[member]
    assert execution.STRATEGY_CACHE_STATS == {"misses": len(symbols), "hits": len(symbols) * (len(MEMBERS) - 1)}
    if model == pullback.MODEL_LEGACY:
        assert len(outputs[0][0]) == len(symbols)  # boş hesap (üye 0): 8 uygun aday


# ---------------------------------------------------------------------------------------------
# 6) Binance hatası (429 / zaman aşımı) yalnız ilgili üyenin hesabını bekletir; mevcut retry kuralları aynen
# ---------------------------------------------------------------------------------------------

def test_one_members_binance_errors_do_not_stall_the_others():
    import httpx

    application, registry = member_application()
    members = MEMBERS[:6]
    clients = {member: SimpleNamespace(application=application, api_key=f"key-{member}-xxxxxxxx", secret_key="s")
               for member in members}
    index_of = {client.api_key: index for index, client in enumerate(clients.values())}
    upstream = {member: 0 for member in members}
    failures = {members[0]: v25_execution.LiveRateLimitError("Binance 429", retry_after=20),
                members[1]: httpx.ReadTimeout("read timeout")}

    async def fake_inner(client, correlation_id, evidence_state=None):
        member = CURRENT_USER.get()
        upstream[member] += 1
        await asyncio.sleep(0.005)
        if member in failures and upstream[member] == 1:
            raise failures[member]
        return account_of(index_of[client.api_key])

    async def read(member, cached=True):
        return await as_member(member, lambda: v25_execution.account_snapshot(clients[member], cached=cached))

    async def scenario():
        first = await asyncio.gather(*(read(member) for member in members), return_exceptions=True)
        again = await asyncio.gather(*(read(member) for member in members), return_exceptions=True)
        return first, again

    with patch.object(binance_demo, "_account_snapshot", fake_inner), patch.object(v25_execution, "persist_state"):
        first, again = run(scenario())

    assert isinstance(first[0], v25_execution.LiveRateLimitError)
    assert isinstance(first[1], httpx.ReadTimeout)
    assert all(row["wallet_balance"] == 1000.0 + index for index, row in enumerate(first[2:], start=2))
    # 429: yalnız o üyenin durumu bekleme süresine girer; süre dolmadan yeni istek GÖNDERİLMEZ (mevcut kural).
    assert v25_execution.account_rate_limit_active(registry.state_for(members[0]))
    assert isinstance(again[0], v25_execution.LiveRateLimitError) and upstream[members[0]] == 1
    assert not any(v25_execution.account_rate_limit_active(registry.state_for(member)) for member in members[1:])
    # Zaman aşımı: önbellek boşaltılır, sonraki çağrı yeniden dener ve başarılı olur.
    assert again[1]["wallet_balance"] == 1001.0 and upstream[members[1]] == 2
    # Diğerleri önbellekten, ek istek olmadan.
    assert all(upstream[member] == 1 for member in members[2:])
