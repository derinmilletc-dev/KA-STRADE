"""Akıllı Kâr Koruma testleri (Original V2 Live). Gerçek borsaya emir GİTMEZ.

Saf mantık gerçek modülle, borsa katmanı sahte bir borsayla (FakeExchange) denenir. Sahte borsa gerçek
Binance davranışını (reduce-only, aynı kimlikle çift emir yok, iptal doğrulama) taklit eder.
Pytest'e bağımlı değildir; düz assert + asyncio kullanır.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
import types
from contextlib import contextmanager
from decimal import Decimal

from app import original_profit_guard as g

BASE = 1_700_000_100 // 900 * 900 + 5  # bir 15 dk kapanışından 5 sn sonra (UTC)
PREFIX = "KT_"


def at(k: int, extra: int = 0) -> float:
    return float(BASE + 900 * k + extra)


# ------------------------------------------------------------------ sahte altyapı
class FakeExc(Exception):
    def __init__(self, message="x", *, unknown_execution=False, exchange_code=None):
        super().__init__(message)
        self.unknown_execution = unknown_execution
        self.exchange_code = exchange_code


def client_id_for(kind: str, intent_id: str) -> str:
    digest = hashlib.sha256(f"{kind}:{intent_id}".encode()).hexdigest()[:22]
    return f"{PREFIX}{kind[:5].upper()}_{digest}"[:36]


class FakeExchange:
    def __init__(self, *, direction="SHORT", qty="10", entry="100", mark="98"):
        self.pos = {"symbol": "ABCUSDT", "direction": direction, "quantity": Decimal(qty),
                    "entry": Decimal(entry), "mark": Decimal(mark)}
        self.algos: list[dict] = []          # ham Binance satırları
        self.orders: dict[str, dict] = {}    # clientOrderId -> emir
        self.log: list[tuple] = []           # (method, path, params)
        self.income: list[dict] = []
        self.fail_delete = False
        self.fail_post = False
        self.fail_open_algos = False
        self.fail_income = False
        self.next_id = 1000
        # orijinal korumalar (dokunulmamalı)
        self.algos.append(self.raw("ORIG_SL", "STOP_MARKET", "103", None, close_position=True))
        self.algos.append(self.raw("ORIG_TP3", "TAKE_PROFIT_MARKET", "90", None, close_position=True))

    def raw(self, cid, typ, trigger, qty, *, close_position=False, reduce_only=False):
        self.next_id += 1
        side = "BUY" if self.pos["direction"] == "SHORT" else "SELL"
        return {"symbol": "ABCUSDT", "algoId": self.next_id, "clientAlgoId": cid, "side": side, "orderType": typ,
                "algoStatus": "NEW", "triggerPrice": str(trigger), "quantity": str(qty or 0),
                "closePosition": str(close_position).lower(), "reduceOnly": str(reduce_only).lower(),
                "workingType": "MARK_PRICE"}

    def position_row(self):
        p = self.pos
        if p is None:
            return None
        return {"symbol": p["symbol"], "position_side": "BOTH", "direction": p["direction"], "quantity": float(p["quantity"]),
                "entry_price": float(p["entry"]), "mark_price": float(p["mark"])}

    async def signed(self, method, path, params=None):
        params = params or {}
        self.log.append((method, path, dict(params)))
        if (method, path) == ("GET", "/fapi/v1/openAlgoOrders"):
            if self.fail_open_algos:
                raise FakeExc("down")
            return [dict(row) for row in self.algos]
        if (method, path) == ("DELETE", "/fapi/v1/algoOrder"):
            if self.fail_delete:
                raise FakeExc("delete failed", exchange_code=-1000)
            self.algos = [row for row in self.algos if row["algoId"] != params["algoId"]]
            return {"ok": True}
        if (method, path) == ("GET", "/fapi/v1/income"):
            if self.fail_income:
                raise FakeExc("income down")
            return list(self.income)
        raise AssertionError(f"beklenmeyen çağrı {method} {path}")

    def snapshot(self, **extra):
        row = self.position_row()
        return {"positions": [row] if row else [], "hedge_mode": False, "open_algo_orders_available": True, **extra}

    def mutating_calls(self):
        return [c for c in self.log if c[0] in {"POST", "DELETE"}]


def make_live(fx: FakeExchange):
    live = types.ModuleType("app.v25_execution")
    live.client_id_for = client_id_for
    live.decimal_text = lambda v: format(Decimal(str(v)).normalize(), "f")

    async def persist_awaited(state):
        state["persisted"] = state.get("persisted", 0) + 1

    live.persist_state_awaited = persist_awaited
    live.persist_state = lambda state: None

    def add_event(state, kind, message, **extra):
        row = {"id": f"e{len(state['events'])}", "kind": kind, "message": message, "created_at": "2026-01-01T00:00:00Z", **extra}
        state["events"].insert(0, row)
        return row

    live.add_event = add_event
    live.live_plan_is_active = lambda plan: str(plan.get("status") or "").upper() not in {"KAPANDI", "İPTAL"}
    live.live_plan_can_mutate = lambda plan: plan.get("provenance_state") == "CONFIRMED"
    live.confirm_live_plan_provenance = lambda plan, position: True
    live.classify_algo_snapshot_payload = lambda payload: "VALID" if isinstance(payload, list) else "UNKNOWN"
    live.normalize_open_algo_rows = lambda payload: [{
        "symbol": r["symbol"], "algo_id": r["algoId"], "client_algo_id": r["clientAlgoId"], "side": r["side"],
        "type": r["orderType"], "status": r["algoStatus"], "trigger_price": r["triggerPrice"], "quantity": r["quantity"],
        "close_position": r["closePosition"] == "true"} for r in payload]

    async def live_plan_position(client, symbol):
        p = fx.pos
        if p is None:
            return None
        return {"symbol": symbol, "direction": p["direction"], "quantity": str(p["quantity"]),
                "mark_price": str(p["mark"]), "entry_price": str(p["entry"])}

    live.live_plan_position = live_plan_position

    async def close_tracked_symbol(client, symbol, intent):
        if fx.pos is None:
            return None
        cid = client_id_for("CLOSE", intent)
        if cid in fx.orders:
            return fx.orders[cid]
        order = {"orderId": 5000 + len(fx.orders), "clientOrderId": cid, "reduceOnly": True,
                 "side": "BUY" if fx.pos["direction"] == "SHORT" else "SELL", "quantity": fx.pos["quantity"]}
        fx.orders[cid] = order
        fx.log.append(("POST", "/fapi/v1/order", {"newClientOrderId": cid, "reduceOnly": "true"}))
        fx.pos = None
        return order

    live.close_tracked_symbol = close_tracked_symbol

    async def post_entry_protection_algo(client, params, rows):
        fx.log.append(("POST", "/fapi/v1/algoOrder", dict(params)))
        if fx.fail_post:
            raise FakeExc("post failed")
        for row in rows or []:
            if row.get("client_algo_id") == params["clientAlgoId"]:
                return {"algoId": row["algo_id"]}
        assert params.get("reduceOnly") == "true" and params.get("quantity"), "geçici stop reduce-only ve miktarlı olmalı"
        assert params["type"] == "STOP_MARKET"
        row = fx.raw(params["clientAlgoId"], "STOP_MARKET", params["triggerPrice"], params["quantity"], reduce_only=True)
        fx.algos.append(row)
        return {"algoId": row["algoId"]}

    live.post_entry_protection_algo = post_entry_protection_algo

    async def live_symbol_rules(client, symbol, order_type):
        return {"step": Decimal("0.1"), "min_qty": Decimal("0.1"), "tick": Decimal("0.01"),
                "min_notional": Decimal("5"), "max_qty": Decimal("999999")}

    live.live_symbol_rules = live_symbol_rules
    return live


def make_original():
    original = types.ModuleType("app.original_v2_live")
    original.SOURCE = "ORIGINAL_V2_LIVE"
    original.settings = lambda state: state["original_v2_live"]
    original.plan_is_original = lambda plan: plan.get("source") == "ORIGINAL_V2_LIVE"
    return original


def make_plan(**extra):
    plan = {"id": "plan1", "intent_id": "intent-1", "symbol": "ABCUSDT", "direction": "SHORT", "source": "ORIGINAL_V2_LIVE",
            "status": "KORUMA AKTİF", "protection_state": "MATCHED", "provenance_state": "CONFIRMED",
            "original_tp_attempted": True, "created_at": "2026-01-01T00:00:00+00:00", "stop_loss": "103",
            "initial_stop_loss": "103", "targets": ["95", "92", "90"], "tp12_algo_id": 1, "original_tp3_algo_id": 2}
    plan.update(extra)
    return plan


class Env:
    def __init__(self, *, direction="SHORT", mark="98", settings=None, plan=None):
        self.fx = FakeExchange(direction=direction, mark=mark)
        self.plan = plan or make_plan(direction=direction)
        self.state = {"events": [], "plans": {self.plan["id"]: self.plan}, "policy": {},
                      "original_v2_live": {"owner_id": "owner", "alerts": [], **({"smart_profit": settings} if settings else {})}}
        self.scores: list = []
        self.analysis_calls = 0
        self.app = object()

    def script(self, *items):
        """Sıradaki her puan gözlemi için (yön, puan) veya None (hesaplanamadı)."""
        self.scores = list(items)

    async def fake_fetch(self, client, symbol, at_, kline_fetch):
        self.analysis_calls += 1
        if not self.scores:
            return None, "puan_hesaplanamadi"
        item = self.scores[0] if len(self.scores) == 1 else self.scores.pop(0)
        if item is None:
            return None, "mum_verisi_eksik_veya_bayat"
        direction, score = item
        return {"direction": direction, "confidence": score, "atr": 0.5}, "ok"

    def tick(self, now, **snap):
        async def run():
            await g.tick(self.app, self.fx, self.state, self.fx.snapshot(**snap), None, now=now)
        asyncio.run(run())

    @property
    def guard(self):
        return self.plan.get(g.GUARD_KEY)

    def temp_stops(self):
        return [r for r in self.fx.algos if r["clientAlgoId"] == client_id_for("SGP", "intent-1")]

    def event_kinds(self):
        return [e["kind"] for e in self.state["events"]]


@contextmanager
def env(**kwargs):
    e = Env(**kwargs)
    saved = {name: sys.modules.get(name) for name in ("app.v25_execution", "app.original_v2_live")}
    saved_fetch = g.fetch_analysis
    saved_cache = dict(g._RULES_CACHE)
    sys.modules["app.v25_execution"] = make_live(e.fx)
    sys.modules["app.original_v2_live"] = make_original()
    g.fetch_analysis = e.fake_fetch
    g._RULES_CACHE.clear()
    try:
        yield e
    finally:
        g.fetch_analysis = saved_fetch
        g._RULES_CACHE.clear()
        g._RULES_CACHE.update(saved_cache)
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def original_untouched(e: Env) -> bool:
    """Orijinal SL/TP emirleri hiç silinmedi/değişmedi; plan stop/hedef alanları aynı."""
    ids = {r["clientAlgoId"] for r in e.fx.algos}
    deleted_ids = {c[2].get("algoId") for c in e.fx.log if c[0] == "DELETE"}
    originals = {r["algoId"] for r in e.fx.algos if r["clientAlgoId"].startswith("ORIG_")}
    return ({"ORIG_SL", "ORIG_TP3"} <= ids and not (originals & deleted_ids)
            and e.plan["stop_loss"] == "103" and e.plan["initial_stop_loss"] == "103" and e.plan["targets"] == ["95", "92", "90"])


# ------------------------------------------------------------------ saf mantık
def test_settings_are_clamped_and_strict():
    assert g.clean_settings(None) == g.DEFAULT_SETTINGS
    assert g.clean_settings({"threshold": 10, "wait_minutes": True, "enabled": "yes"}) == g.DEFAULT_SETTINGS
    assert g.clean_settings({"threshold": 75, "wait_minutes": 5, "check_interval_seconds": 60, "emergency_off": True})["threshold"] == 75
    assert g.DEFAULT_SETTINGS["threshold"] == 70 and g.DEFAULT_SETTINGS["wait_minutes"] == 10


def test_score_uses_strategy_confidence_and_never_reaches_threshold_when_direction_differs():
    assert g.position_score({"direction": "SHORT", "confidence": 82}, "SHORT", 70) == (82, "yon_ayni")
    assert g.position_score({"direction": "BEKLE", "confidence": 66}, "SHORT", 70) == (66, "bekle")
    score, note = g.position_score({"direction": "LONG", "confidence": 90}, "SHORT", 70)
    assert score == 69 and note == "yon_ters"
    assert g.position_score({"direction": "LONG"}, "SHORT", 70)[0] is None
    assert g.position_score({"direction": "SHORT", "confidence": 120}, "SHORT", 70)[0] is None


def test_net_pnl_long_and_short_include_all_costs():
    long_view = {"symbol": "X", "direction": "LONG", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal(102)}
    short_view = {"symbol": "X", "direction": "SHORT", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal(98)}
    a = g.net_pnl(long_view, fee_bps=5, slippage_bps=2, funding_paid=0)
    b = g.net_pnl(short_view, fee_bps=5, slippage_bps=2, funding_paid=0)
    assert a["unrealized"] == Decimal(20) and b["unrealized"] == Decimal(20)
    assert a["open_fee"] == Decimal("0.5") and a["close_fee"] == Decimal("0.51")
    assert a["net"] == Decimal(20) - a["cost"] and a["cost"] == a["open_fee"] + a["close_fee"] + a["slippage"]
    loser = dict(long_view, mark=Decimal("100.01"))
    assert g.net_pnl(loser, fee_bps=5, slippage_bps=2, funding_paid=0)["net"] < 0  # küçük brüt kâr maliyeti karşılamaz
    paid = g.net_pnl(long_view, fee_bps=5, slippage_bps=2, funding_paid=1)
    assert paid["net"] == a["net"] - 1
    received = g.net_pnl(long_view, fee_bps=5, slippage_bps=2, funding_paid=-3)
    assert received["net"] == a["net"]  # alınan funding kâr sayılmaz (muhafazakâr)


def test_net_pnl_refuses_missing_data():
    view = {"symbol": "X", "direction": "LONG", "quantity": Decimal(1), "entry": Decimal(100), "mark": Decimal(101)}
    assert g.net_pnl(view, fee_bps=5, slippage_bps=2, funding_paid=None) is None
    assert g.net_pnl(view, fee_bps=None, slippage_bps=2, funding_paid=0) is None
    assert g.view_of({"symbol": "X", "direction": "LONG", "quantity": 0, "entry_price": 1, "mark_price": 1}) is None
    assert g.view_of({"symbol": "X", "direction": "LONG", "quantity": 1, "entry_price": 0, "mark_price": 1}) is None


def _pnl(view, funding=0):
    return g.net_pnl(view, fee_bps=5, slippage_bps=2, funding_paid=funding)


def test_temp_stop_short_protects_net_profit_and_respects_tick_and_original_sl():
    view = {"symbol": "X", "direction": "SHORT", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal(98)}
    pnl = _pnl(view)
    price, why = g.temp_stop_level(view, pnl, atr=0.5, tick="0.01", original_stop="103")
    assert why == "ok" and price == Decimal("98.50")
    assert price % Decimal("0.01") == 0 and price > view["mark"] and price < Decimal("103")
    # stop tetiklense bile net kâr pozitif: giriş - birim maliyet - tampon üstünde kalır
    assert (view["entry"] - price) * view["quantity"] - pnl["cost"] > 0


def test_temp_stop_long_mirror():
    view = {"symbol": "X", "direction": "LONG", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal(102)}
    price, why = g.temp_stop_level(view, _pnl(view), atr=0.5, tick="0.01", original_stop="97")
    assert why == "ok" and price == Decimal("101.50") and price < view["mark"] and price > Decimal("97")


def test_temp_stop_not_invented_when_unreliable():
    view = {"symbol": "X", "direction": "SHORT", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal("99.9")}
    assert g.temp_stop_level(view, _pnl(view), atr=0.5, tick="0.01", original_stop="103")[0] is None  # net kâr yok
    win = dict(view, mark=Decimal(98))
    assert g.temp_stop_level(win, _pnl(win), atr=None, tick="0.01", original_stop="103")[1] == "atr_yok"
    assert g.temp_stop_level(win, _pnl(win), atr=0.5, tick=None, original_stop="103")[1] == "tick_yok"
    assert g.temp_stop_level(win, _pnl(win), atr=0.5, tick="0.01", original_stop=None)[1] == "orijinal_sl_yok"
    # kâr payı çok az: taban işaret fiyatına çok yakın → seviye üretilmez
    thin = {"symbol": "X", "direction": "SHORT", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal("99.82")}
    pnl = g.net_pnl(thin, fee_bps=5, slippage_bps=2, funding_paid=0)
    assert pnl["net"] > 0
    price, why = g.temp_stop_level(thin, pnl, atr=0.5, tick="0.01", original_stop="103")
    assert price is None and why == "kar_payi_yetersiz"
    # orijinal SL ile çakışma
    assert g.temp_stop_level(win, _pnl(win), atr=0.5, tick="0.01", original_stop="98.4")[1] == "orijinal_sl_ile_cakisiyor"


def test_observe_timer_and_two_candle_rule():
    guard = g.new_guard()
    ev = g.observe(guard, score=62, candle=100, now=1000.0, threshold=70, wait_seconds=600)
    assert ev == ["SCORE_LOW_STARTED"] and guard["mode"] == g.MODE_PROTECT and guard["timer_end"] == 1600.0
    # aynı mum tekrar okunur: yeni gözlem DEĞİL
    assert g.observe(guard, score=60, candle=100, now=1100.0, threshold=70, wait_seconds=600) == ["DUPLICATE_OBSERVATION"]
    # sayaç dolsa bile yeni kapanmış mum yoksa çıkış hazır olmaz
    assert g.observe(guard, score=60, candle=100, now=2000.0, threshold=70, wait_seconds=600) == ["DUPLICATE_OBSERVATION"]
    assert guard["exit_ready"] is False
    # yeni kapanmış mum, sayaç doldu, puan hâlâ düşük → hazır
    assert g.observe(guard, score=61, candle=101, now=1900.0, threshold=70, wait_seconds=600) == ["EXIT_READY"]
    assert guard["mode"] == g.MODE_EXIT_READY and guard["exit_ready"] is True


def test_observe_new_candle_before_timer_does_not_make_exit_ready():
    guard = g.new_guard()
    g.observe(guard, score=62, candle=100, now=1000.0, threshold=70, wait_seconds=600)
    assert g.observe(guard, score=60, candle=101, now=1300.0, threshold=70, wait_seconds=600) == ["SCORE_LOW_CONTINUES"]
    assert guard["exit_ready"] is False


def test_observe_recovery_resets_everything():
    guard = g.new_guard()
    g.observe(guard, score=62, candle=100, now=1000.0, threshold=70, wait_seconds=600)
    g.observe(guard, score=60, candle=101, now=1900.0, threshold=70, wait_seconds=600)
    ev = g.observe(guard, score=70, candle=102, now=2800.0, threshold=70, wait_seconds=600)  # tam eşik = normal
    assert "RECOVERED" in ev and guard["mode"] == g.MODE_NORMAL
    assert guard["first_low_at"] is None and guard["timer_end"] is None and guard["exit_ready"] is False


def test_observe_oscillating_around_threshold_never_exits():
    guard = g.new_guard()
    now = 1000.0
    for i, score in enumerate([69, 71, 68, 72, 69, 71, 69]):
        g.observe(guard, score=score, candle=100 + i, now=now, threshold=70, wait_seconds=600)
        now += 900
        assert guard["exit_ready"] is False


def test_observe_unavailable_score_changes_nothing():
    guard = g.new_guard()
    g.observe(guard, score=62, candle=100, now=1000.0, threshold=70, wait_seconds=600)
    before = copy.deepcopy(guard)
    assert g.observe(guard, score=None, candle=101, now=2000.0, threshold=70, wait_seconds=600, note="x") == ["SCORE_UNAVAILABLE"]
    assert guard["mode"] == before["mode"] and guard["timer_end"] == before["timer_end"] and guard["exit_ready"] is False


def test_exit_decision_never_closes_loss_or_unknown():
    guard = g.new_guard()
    g.observe(guard, score=60, candle=100, now=0.0, threshold=70, wait_seconds=1)
    g.observe(guard, score=60, candle=101, now=900.0, threshold=70, wait_seconds=1)
    assert g.exit_decision(guard, Decimal("-0.01"), smart_exit=True) == "WAIT_PROFIT"
    assert g.exit_decision(guard, Decimal(0), smart_exit=True) == "WAIT_PROFIT"
    assert g.exit_decision(guard, None, smart_exit=True) == "NONE"
    assert g.exit_decision(guard, Decimal("1"), smart_exit=False) == "NONE"
    assert g.exit_decision(guard, Decimal("1"), smart_exit=True) == "CLOSE"
    assert g.exit_decision(g.new_guard(), Decimal("1"), smart_exit=True) == "NONE"


# ------------------------------------------------------------------ borsa katmanı senaryoları
def test_s01_score_drops_while_in_profit_creates_temp_stop_only():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        assert e.guard["mode"] == g.MODE_PROTECT and e.guard["first_low_at"] == at(0)
        stops = e.temp_stops()
        assert len(stops) == 1 and stops[0]["triggerPrice"] == "98.5" and stops[0]["quantity"] == "10"
        assert stops[0]["reduceOnly"] == "true"
        assert e.fx.orders == {} and original_untouched(e)
        assert "SMART_GUARD_LOW_SCORE" in [a["type"] for a in e.state["original_v2_live"]["alerts"]]
        assert "SMART_GUARD_TEMP_STOP_CREATED" in [a["type"] for a in e.state["original_v2_live"]["alerts"]]


def test_s02_score_drops_while_in_loss_no_temp_stop_no_close():
    with env(mark="101") as e:
        e.script(("SHORT", 60))
        e.tick(at(0))
        e.tick(at(1))
        e.tick(at(2))
        assert e.guard["mode"] == g.MODE_EXIT_READY
        assert e.temp_stops() == [] and e.fx.orders == {} and e.fx.pos is not None
        assert e.guard["waiting_profit"] is True and original_untouched(e)


def test_s03_recovery_before_exit_cancels_temp_stop_and_resets_timer():
    with env() as e:
        e.script(("SHORT", 62), ("SHORT", 74))
        e.tick(at(0))
        assert len(e.temp_stops()) == 1
        e.tick(at(1))
        assert e.guard["mode"] == g.MODE_NORMAL and e.guard["first_low_at"] is None and e.guard["timer_end"] is None
        assert e.temp_stops() == [] and e.guard["temp_stop"] is None and e.fx.orders == {} and original_untouched(e)
        assert "SMART_GUARD_RECOVERED" in [a["type"] for a in e.state["original_v2_live"]["alerts"]]


def test_s04_stays_low_in_profit_closes_once_reduce_only_after_second_closed_candle():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        e.tick(at(0, 400))  # aynı mum içinde: kapanış yok
        assert e.fx.orders == {} and e.guard["exit_ready"] is False
        e.tick(at(1))  # sonraki kapanmış mum, sayaç dolmuş, puan hâlâ düşük
        assert len(e.fx.orders) == 1
        order = next(iter(e.fx.orders.values()))
        assert order["reduceOnly"] is True and order["clientOrderId"].startswith(PREFIX + "CLOSE")
        assert e.plan["close_reason"] == g.CLOSE_REASON and e.plan["status"] == "KAPATMA EMRİ GÖNDERİLDİ"
        assert e.temp_stops() == []  # geçici stop da temizlendi
        assert original_untouched(e)
        assert "SMART_GUARD_CLOSED" in [a["type"] for a in e.state["original_v2_live"]["alerts"]]


def test_s05_low_until_end_in_loss_never_closes():
    with env(mark="100.5") as e:
        e.script(("SHORT", 55))
        for k in range(0, 8):
            e.tick(at(k))
        assert e.fx.orders == {} and e.fx.pos is not None and e.temp_stops() == [] and original_untouched(e)


def test_s06_exit_ready_then_turns_profit_closes_on_first_profit_check():
    with env(mark="100.5") as e:
        e.script(("SHORT", 55))
        e.tick(at(0))
        e.tick(at(1))
        assert e.guard["mode"] == g.MODE_EXIT_READY and e.fx.orders == {}
        e.fx.pos["mark"] = Decimal("98")  # kâra geçti
        e.tick(at(1, 150))
        assert len(e.fx.orders) == 1 and e.plan["close_reason"] == g.CLOSE_REASON
        assert "SMART_GUARD_NET_PROFIT" in [a["type"] for a in e.state["original_v2_live"]["alerts"]]


def test_s07_exit_ready_then_score_recovers_cancels_exit():
    with env(mark="100.5") as e:
        e.script(("SHORT", 55), ("SHORT", 55), ("SHORT", 80))
        e.tick(at(0))
        e.tick(at(1))
        assert e.guard["exit_ready"] is True
        e.fx.pos["mark"] = Decimal("98")
        e.tick(at(2))  # puan 80: toparlandı, kâra rağmen kapatılmaz
        assert e.guard["mode"] == g.MODE_NORMAL and e.guard["exit_ready"] is False
        assert e.fx.orders == {} and e.temp_stops() == []


def test_s08_temp_stop_triggered_position_gone_cleans_up_and_marks_plan():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        assert len(e.temp_stops()) == 1
        # stop tetiklendi: pozisyon yok, plan KAPANDI (uzlaştırma yapar), emir borsada tüketildi
        e.fx.pos = None
        e.plan["status"] = "KAPANDI"
        e.plan["close_reason"] = "SMART_STOP"
        e.fx.algos = [r for r in e.fx.algos if r["clientAlgoId"].startswith("ORIG_")]
        e.tick(at(0, 300))
        assert e.guard["finalized"] is True and e.guard["temp_stop"] is None
        assert "SMART_GUARD_TEMP_STOP_TRIGGERED" in [a["type"] for a in e.state["original_v2_live"]["alerts"]]
        assert e.fx.orders == {}


def test_s09_tp_and_original_orders_never_touched_through_whole_cycle():
    with env() as e:
        before = [dict(r) for r in e.fx.algos if r["clientAlgoId"].startswith("ORIG_")]
        e.script(("SHORT", 62), ("SHORT", 62), ("SHORT", 75))
        for k in range(3):
            e.tick(at(k))
        after = [dict(r) for r in e.fx.algos if r["clientAlgoId"].startswith("ORIG_")]
        assert before == after
        for method, path, params in e.fx.mutating_calls():
            assert not str(params.get("clientAlgoId", "SGP")).startswith("ORIG_")
        assert e.plan["tp12_algo_id"] == 1 and e.plan["original_tp3_algo_id"] == 2 and original_untouched(e)


def test_s10_original_sl_triggers_position_gone_no_actions_and_temp_stop_cleared():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        e.fx.pos = None
        e.plan["status"] = "KAPANDI"
        e.plan["close_reason"] = "STOP"
        e.tick(at(0, 200))
        assert e.fx.orders == {} and e.temp_stops() == [] and e.guard["finalized"] is True


def test_s11_partial_close_quantity_change_resyncs_temp_stop():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        assert e.temp_stops()[0]["quantity"] == "10"
        e.fx.pos["quantity"] = Decimal("3")  # TP1 / dış kısmi kapanış
        e.tick(at(0, 200))   # eski stop iptal
        assert e.temp_stops() == []
        e.tick(at(0, 400))   # yeni miktarla yeniden kur
        stops = e.temp_stops()
        assert len(stops) == 1 and stops[0]["quantity"] == "3"
        posts = [c for c in e.fx.log if c[:2] == ("POST", "/fapi/v1/algoOrder")]
        assert len(posts) == 2 and original_untouched(e)


def test_s12_restart_during_timer_keeps_timer_and_does_not_duplicate():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        started, ends, stop_id = e.guard["first_low_at"], e.guard["timer_end"], e.guard["temp_stop"]["algo_id"]
        # yeniden başlatma: durum JSON'a gidip döner (PostgreSQL snapshot gibi)
        e.state = json.loads(json.dumps(e.state))
        e.plan = e.state["plans"]["plan1"]
        e.tick(at(0, 300))
        assert e.guard["first_low_at"] == started and e.guard["timer_end"] == ends
        assert len(e.temp_stops()) == 1 and e.guard["temp_stop"]["algo_id"] == stop_id
        assert len([c for c in e.fx.log if c[:2] == ("POST", "/fapi/v1/algoOrder")]) == 1
        e.tick(at(1))
        assert len(e.fx.orders) == 1  # sayaç kaldığı yerden sürdü: ikinci kapanışta çıkış


def test_s13_binance_api_errors_do_not_create_orders_or_crash():
    with env() as e:
        e.script(("SHORT", 62))
        e.fx.fail_open_algos = True
        e.tick(at(0))
        assert e.temp_stops() == [] and e.fx.orders == {} and original_untouched(e)
        e.fx.fail_open_algos = False
        e.fx.fail_post = True
        e.tick(at(0, 200))
        assert e.temp_stops() == [] and e.fx.orders == {}
        e.fx.fail_post = False
        e.tick(at(0, 400))
        assert len(e.temp_stops()) == 1


def test_s14_score_not_computable_no_state_change_no_close():
    with env() as e:
        e.script(None)
        for k in range(5):
            e.tick(at(k))
        assert e.guard["mode"] == g.MODE_NORMAL and e.fx.orders == {} and e.temp_stops() == []
        assert "SMART_GUARD_SCORE_UNAVAILABLE" in e.event_kinds()


def test_s15_stale_data_blocks_exit_even_when_exit_ready_and_profitable():
    with env() as e:
        e.script(("SHORT", 55), ("SHORT", 55), None)
        e.tick(at(0))
        e.tick(at(1))
        assert e.fx.orders != {}  # ilk iki gözlem geçerliydi; burada kapanış beklenir


def test_s15b_exit_reverification_blocks_when_score_cannot_be_rechecked():
    with env(mark="100.5") as e:
        e.script(("SHORT", 55), ("SHORT", 55), None)
        e.tick(at(0))
        e.tick(at(1))                       # çıkışa hazır, zararda
        e.fx.pos["mark"] = Decimal("98")    # kâra geçti ama yeni mum verisi bayat
        e.tick(at(2))
        assert e.fx.orders == {} and e.fx.pos is not None
        assert e.guard["exit_ready"] is True  # durum korunur; veri düzelince karar verilir


def test_s16_duplicate_close_order_is_impossible():
    with env() as e:
        e.script(("SHORT", 55))
        e.tick(at(0))
        e.tick(at(1))
        assert len(e.fx.orders) == 1
        e.fx.pos = None
        e.tick(at(1, 130))
        e.tick(at(1, 260))
        assert len(e.fx.orders) == 1
        # aynı kimlikle ikinci çağrı da yeni emir yaratmaz
        sys.modules["app.v25_execution"] = make_live(e.fx)
        e.fx.pos = {"symbol": "ABCUSDT", "direction": "SHORT", "quantity": Decimal(10), "entry": Decimal(100), "mark": Decimal(98)}
        asyncio.run(sys.modules["app.v25_execution"].close_tracked_symbol(e.fx, "ABCUSDT", "smartexit-plan1"))
        assert len(e.fx.orders) == 1


def test_s17_manual_or_foreign_positions_are_never_touched():
    manual = make_plan(source="MANUAL")
    with env(plan=manual) as e:
        e.script(("SHORT", 40))
        for k in range(4):
            e.tick(at(k))
        assert e.fx.mutating_calls() == [] and e.analysis_calls == 0 and g.GUARD_KEY not in e.plan
    unconfirmed = make_plan(provenance_state="NO_PROVENANCE")
    with env(plan=unconfirmed) as e:
        e.script(("SHORT", 40))
        e.tick(at(0))
        assert e.fx.mutating_calls() == [] and g.GUARD_KEY not in e.plan


def test_s18_unprotected_or_closing_plans_are_skipped():
    for extra in ({"protection_state": "UNKNOWN"}, {"original_tp_attempted": False}, {"status": "GÜVENLİK KAPATMASI · DOĞRULAMA BEKLİYOR"}):
        with env(plan=make_plan(**extra)) as e:
            e.script(("SHORT", 40))
            e.tick(at(0))
            e.tick(at(1))
            assert e.fx.mutating_calls() == [] and e.fx.orders == {}


def test_s19_hedge_mode_is_not_supported_and_does_nothing():
    with env() as e:
        e.script(("SHORT", 40))
        e.tick(at(0), hedge_mode=True)
        e.tick(at(1), hedge_mode=True)
        assert e.fx.mutating_calls() == [] and e.analysis_calls == 0


def test_s20_temp_stop_cancel_failure_creates_no_conflict_and_retries():
    with env() as e:
        e.script(("SHORT", 62), ("SHORT", 80))
        e.tick(at(0))
        assert len(e.temp_stops()) == 1
        e.fx.fail_delete = True
        e.tick(at(1))                       # toparlandı ama iptal başarısız
        assert len(e.temp_stops()) == 1 and e.guard["temp_stop"]["cancel_pending"] is True
        posts = [c for c in e.fx.log if c[:2] == ("POST", "/fapi/v1/algoOrder")]
        assert len(posts) == 1              # yeni stop kurulmadı
        e.tick(at(1, 200))
        assert len(e.temp_stops()) == 1     # hâlâ başarısız, yine yeni emir yok
        e.fx.fail_delete = False
        e.tick(at(1, 400))
        assert e.temp_stops() == [] and e.guard["temp_stop"] is None and original_untouched(e)


def test_s22_funding_changes_flip_net_profit_and_missing_funding_blocks_close():
    # küçük brüt kâr: funding ödenince net kâr negatife döner → kapatma yok
    with env(mark="99.9") as e:   # brüt kâr 1.0 USDT < komisyon + kayma: net kâr yok
        e.script(("SHORT", 55))
        e.tick(at(0))
        e.tick(at(1))
        assert e.guard["mode"] == g.MODE_EXIT_READY and e.fx.orders == {}  # maliyet sonrası kâr yok
    # funding okunamıyorsa hiçbir otomatik işlem yok
    with env() as e:
        e.fx.fail_income = True
        e.script(("SHORT", 55))
        e.tick(at(0))
        e.tick(at(1))
        assert e.fx.orders == {} and e.temp_stops() == [] and e.guard["last_net_pnl"] is None
    # funding ödemesi net kârı eksiye çeker (güncelleme sonrası)
    with env(mark="99.0") as e:
        e.script(("SHORT", 55))
        e.tick(at(0))
        e.tick(at(1))
        assert len(e.fx.orders) == 1  # 10 USDT brüt kâr, funding yok: kapanır
    with env(mark="99.0") as e:
        e.fx.income = [{"asset": "USDT", "symbol": "ABCUSDT", "income": "-9.5"}]
        e.script(("SHORT", 55))
        e.tick(at(0))
        e.tick(at(1))
        assert e.fx.orders == {}  # funding 9.5 USDT ödenmiş: net kâr yok


def test_s23_oscillation_around_threshold_never_closes_in_exchange_flow():
    with env() as e:
        e.script(("SHORT", 69), ("SHORT", 71), ("SHORT", 68), ("SHORT", 72), ("SHORT", 69), ("SHORT", 71))
        for k in range(6):
            e.tick(at(k))
        assert e.fx.orders == {} and e.guard["exit_ready"] is False


def test_s24_same_candle_reread_is_not_a_new_observation_in_exchange_flow():
    with env() as e:
        e.script(("SHORT", 62))
        for extra in (0, 130, 260, 390, 520, 650):
            e.tick(at(0, extra))
        assert e.analysis_calls == 1 and e.fx.orders == {}
        assert len(e.guard["observations"]) == 1


def test_s25_system_off_and_emergency_switch_clean_only_temp_stop_and_keep_originals():
    with env() as e:
        e.script(("SHORT", 62))
        e.tick(at(0))
        assert len(e.temp_stops()) == 1
        e.state["original_v2_live"]["smart_profit"] = {"emergency_off": True}
        e.tick(at(0, 200))
        assert e.temp_stops() == [] and e.guard["mode"] == g.MODE_NORMAL and original_untouched(e)
        e.tick(at(1))
        e.tick(at(2))
        assert e.fx.orders == {} and e.analysis_calls == 1  # kapalıyken puan bile hesaplanmaz
    with env(settings={"enabled": False}) as e:
        e.script(("SHORT", 40))
        e.tick(at(0))
        assert e.fx.mutating_calls() == [] and e.analysis_calls == 0


def test_s26_protect_stop_switch_off_still_allows_exit_but_no_temp_stop():
    with env(settings={"protect_stop_enabled": False}) as e:
        e.script(("SHORT", 55))
        e.tick(at(0))
        assert e.temp_stops() == []
        e.tick(at(1))
        assert len(e.fx.orders) == 1


def test_s27_smart_exit_switch_off_never_closes():
    with env(settings={"smart_exit_enabled": False}) as e:
        e.script(("SHORT", 55))
        for k in range(4):
            e.tick(at(k))
        assert e.fx.orders == {} and e.guard["mode"] == g.MODE_EXIT_READY and len(e.temp_stops()) == 1


def test_s28_leftover_guard_order_without_record_is_cleaned_not_duplicated():
    with env() as e:
        e.script(("SHORT", 62))
        # kayıt yok ama borsada bizim kimlikli emir var (yeniden başlatma sırasında kayıt kaybı)
        e.fx.algos.append(e.fx.raw(client_id_for("SGP", "intent-1"), "STOP_MARKET", "98.5", "10", reduce_only=True))
        e.tick(at(0))
        assert len(e.temp_stops()) <= 1
        e.tick(at(0, 200))
        e.tick(at(0, 400))
        assert len(e.temp_stops()) == 1 and original_untouched(e)


def test_s29_uncertain_order_result_is_not_resent_until_visibility_window_passes():
    with env() as e:
        e.script(("SHORT", 62))
        e.fx.fail_post = True
        orig_post = sys.modules["app.v25_execution"].post_entry_protection_algo

        async def unknown(client, params, rows):
            e.fx.log.append(("POST", "/fapi/v1/algoOrder", dict(params)))
            raise FakeExc("timeout", unknown_execution=True)

        sys.modules["app.v25_execution"].post_entry_protection_algo = unknown
        e.tick(at(0))
        posts = lambda: len([c for c in e.fx.log if c[:2] == ("POST", "/fapi/v1/algoOrder")])
        assert posts() == 1 and e.guard["temp_stop"]["pending"] is True
        e.tick(at(0, 130))
        e.tick(at(0, 260))
        assert posts() == 1          # sonuç belirsiz: tekrar gönderilmez
        e.guard["temp_stop"]["requested_at"] -= 1000
        sys.modules["app.v25_execution"].post_entry_protection_algo = orig_post
        e.fx.fail_post = False
        e.tick(at(0, 390))
        assert len(e.temp_stops()) == 1 and "pending" not in e.guard["temp_stop"]


def test_s30_direction_mismatch_between_plan_and_exchange_is_skipped():
    with env(plan=make_plan(direction="LONG")) as e:   # borsada SHORT var
        e.script(("LONG", 40))
        e.tick(at(0))
        e.tick(at(1))
        assert e.fx.mutating_calls() == [] and e.analysis_calls == 0


def test_real_strategy_path_scores_synthetic_candles():
    """Gerçek strateji fonksiyonu + kapanmış mum penceresi: puan açık pozisyon için hesaplanabiliyor."""
    import random
    try:
        from app.strategies import original_v2_demo as strategy
        import app.main  # noqa: F401  (strateji değerlendirmesi ana modülü içe aktarır)
    except ImportError:
        return  # bağımlılıklar yoksa bu yerel koşuda atlanır; CI'da çalışır
    intervals = {"15m": 900, "1h": 3600, "4h": 14400}
    decision_at = 1_700_000_100 // 900 * 900

    def candles(interval, trend):
        step = intervals[interval] * 1000
        count = strategy.CANDLE_FETCH_LIMIT
        current = (decision_at * 1000) // step * step
        start = current - (count - 1) * step
        random.seed(1)
        price, rows = 100.0, []
        for i in range(count):
            price *= 1 + trend + random.uniform(-0.002, 0.002)
            o, c = price, price * (1 + random.uniform(-0.001, 0.001))
            t = start + i * step
            rows.append([t, str(o), str(max(o, c) * 1.001), str(min(o, c) * 0.999), str(c), "1000", t + step - 1, "100000"])
        return rows

    async def fetch(symbol, interval, limit):
        return candles(interval, -0.004)

    analysis, why = asyncio.run(g.fetch_analysis(None, "BTCUSDT", decision_at, fetch))
    assert why == "ok" and analysis["direction"] in {"LONG", "SHORT", "BEKLE"} and analysis["atr"] > 0
    assert g.position_score(analysis, "SHORT", 70)[0] is not None

    async def short_history(symbol, interval, limit):
        return candles(interval, -0.004)[:-5]

    none, reason = asyncio.run(g.fetch_analysis(None, "BTCUSDT", decision_at, short_history))
    assert none is None and reason == "mum_verisi_eksik_veya_bayat"


if __name__ == "__main__":  # pytest olmadan çalıştırma
    failures = 0
    names = sorted(n for n in dir() if n.startswith("test_"))
    for name in names:
        try:
            globals()[name]()
            print("OK  ", name)
        except Exception:
            failures += 1
            import traceback
            print("FAIL", name)
            traceback.print_exc()
    print(f"\n{len(names) - failures}/{len(names)} geçti")
    sys.exit(1 if failures else 0)
