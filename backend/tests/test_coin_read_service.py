"""Yorum veri servisi ve pozisyon yorum alarmı: önbellek, kısmi veri, bildirim planı, alarm turu."""
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import coin_read, coin_read_service as svc, read_alarms as ra  # noqa: E402


def candles(step, n=80, base=100.0):
    return [{"time": 1000 + i * 60, "open": base + i * step, "high": base + i * step + 0.2, "low": base + i * step - 0.2, "close": base + i * step, "volume": 100.0} for i in range(n)]


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class ServiceTests(unittest.TestCase):
    def setUp(self):
        svc.reset()

    def test_read_caches_and_collapses_calls(self):
        calls = []
        async def fetch(symbol, tf, limit):
            calls.append((symbol, tf, limit)); return candles(0.3)
        clock = Clock()
        async def run():
            a, b = await asyncio.gather(svc.read(None, "BTCUSDT", with_futures=False, fetch=fetch, now=clock), svc.read(None, "BTCUSDT", with_futures=False, fetch=fetch, now=clock))
            return a, b
        a, b = asyncio.run(run())
        self.assertIs(a, b)
        self.assertEqual(len(calls), 6)                      # 6 zaman dilimi, tek hesap
        asyncio.run(svc.read(None, "BTCUSDT", with_futures=False, fetch=fetch, now=clock))
        self.assertEqual(len(calls), 6)                      # 20 sn içinde önbellekten
        clock.t += 25
        asyncio.run(svc.read(None, "BTCUSDT", with_futures=False, fetch=fetch, now=clock))
        self.assertEqual(len(calls), 12)

    def test_partial_failure_still_reads_and_total_failure_raises(self):
        async def fetch(symbol, tf, limit):
            if tf in ("1m", "5m"):
                raise RuntimeError("x")
            return candles(0.3)
        data = asyncio.run(svc.read(None, "ETHUSDT", with_futures=False, fetch=fetch))
        self.assertEqual([r["tf"] for r in data["timeframes"]], ["15m", "1h", "4h", "1d"])
        svc.reset()
        async def broken(symbol, tf, limit):
            raise RuntimeError("down")
        with self.assertRaises(RuntimeError):
            asyncio.run(svc.read(None, "ETHUSDT", with_futures=False, fetch=broken))

    def test_futures_parts_degrade_independently(self):
        async def fetch(symbol, tf, limit): return candles(0.3)
        async def get(path, params):
            if path.endswith("premiumIndex"): return {"lastFundingRate": "0.0008"}
            if path.endswith("openInterestHist"): raise RuntimeError("no")
            return [{"buyVol": "120", "sellVol": "100"}, {"buyVol": "130", "sellVol": "100"}]
        data = asyncio.run(svc.read(None, "SOLUSDT", fetch=fetch, get=get))
        futures = data["futures"]
        self.assertAlmostEqual(futures["funding_pct"], 0.08)
        self.assertIsNone(futures["oi_change_pct"])
        self.assertAlmostEqual(futures["taker_ratio"], 1.25)
        async def none_get(path, params): raise RuntimeError("all down")
        svc.reset()
        self.assertIsNone(asyncio.run(svc.read(None, "SOLUSDT", fetch=fetch, get=none_get))["futures"])

    def test_extras_attach_and_degrade(self):
        async def fetch(symbol, tf, limit): return candles(0.3, n=max(80, min(limit, 200)))
        async def get(path, params):
            if path.endswith("depth"):
                return {"bids": [[str(159.7 - 0.05 * i), "20"] for i in range(1, 30)], "asks": [[str(159.7 + 0.05 * i), "10"] for i in range(1, 30)]}
            raise RuntimeError("no futures")
        data = asyncio.run(svc.read(None, "SOLUSDT", fetch=fetch, get=get))
        self.assertEqual(set(data) >= {"context", "plan", "book", "liquidations", "events", "atr"}, True)
        self.assertIsNotNone(data["book"])
        self.assertIsNotNone(data["context"])          # BTC bağlamı alınabildi
        self.assertIsNone(data["liquidations"])        # akış çalışmıyor → bölüm yok (yanlış "sakin" demez)
        self.assertIn("items", data["events"])
        svc.reset()
        async def no_depth(path, params): raise RuntimeError("down")
        data2 = asyncio.run(svc.read(None, "SOLUSDT", fetch=fetch, get=no_depth))
        self.assertIsNone(data2["book"])
        self.assertIsNotNone(data2["summary"])
        svc.reset()
        alarm = asyncio.run(svc.read(None, "SOLUSDT", with_futures=False, fetch=fetch))
        self.assertNotIn("book", alarm)                # alarm turu ek veri çekmez

    def test_history_attached_and_failure_tolerated(self):
        async def fetch(symbol, tf, limit): return candles(0.3, n=limit if limit > 200 else 80)
        data = asyncio.run(svc.read(None, "BNBUSDT", with_futures=False, history_tf="15m", fetch=fetch))
        self.assertEqual(data["history"]["tf"], "15m")
        svc.reset()
        async def flaky(symbol, tf, limit):
            if limit == svc.HISTORY_LIMIT: raise RuntimeError("history down")
            return candles(0.3)
        self.assertIsNone(asyncio.run(svc.read(None, "BNBUSDT", with_futures=False, history_tf="15m", fetch=flaky))["history"])

    def test_valid_symbol(self):
        self.assertEqual(svc.valid_symbol(" btcusdt "), "BTCUSDT")
        for bad in ("", "B", "BTC-USDT", "../x", None):
            self.assertIsNone(svc.valid_symbol(bad))


class AlarmPureTests(unittest.TestCase):
    NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    def test_normalize(self):
        ok = ra.normalize("binance", "BTCUSDT", "btcusdt", "long", 100.5, "all", "5m")
        self.assertEqual((ok["exchange"], ok["symbol"], ok["side"], ok["mode"], ok["min_tf"], ok["entry"]), ("binance", "BTCUSDT", "LONG", "all", "5m", 100.5))
        fallback = ra.normalize("coinbase", "BIP-20DEC30-CDE", "BTCUSDT", "SHORT", -1, "x", "1d")
        self.assertEqual((fallback["mode"], fallback["min_tf"], fallback["entry"]), ("against", "15m", None))
        for bad in (("kraken", "A1", "BTCUSDT", "LONG"), ("binance", "A", "BTCUSDT", "LONG"), ("binance", "AB", "BT/C", "LONG"), ("binance", "AB", "BTCUSDT", "UP")):
            self.assertIsNone(ra.normalize(*bad, None, "against", "15m"))

    def test_plan_notifies_new_only_and_respects_cooldown(self):
        events = {"15m:breakout_down": {"tone": "danger", "text": "a"}, "mtf:against": {"tone": "danger", "text": "b"}}
        send, active = ra.plan([], events, None, self.NOW)
        self.assertEqual(sorted(send), ["15m:breakout_down", "mtf:against"])
        self.assertEqual(active, sorted(send))
        again, active2 = ra.plan(active, events, self.NOW, self.NOW + timedelta(seconds=60))
        self.assertEqual(again, [])                                  # aynı olay tekrar bildirilmez
        self.assertEqual(active2, active)
        grown = {**events, "1h:div": {"tone": "warn", "text": "c"}}
        blocked, kept = ra.plan(active, grown, self.NOW, self.NOW + timedelta(seconds=120))
        self.assertEqual(blocked, [])                                # soğuma süresi dolmadı
        self.assertEqual(kept, active)                               # yeni olay "aktif" yazılmadı
        later, _ = ra.plan(active, grown, self.NOW, self.NOW + timedelta(seconds=ra.COOLDOWN_SECONDS + 1))
        self.assertEqual(later, ["1h:div"])
        gone, cleared = ra.plan(active, {}, self.NOW, self.NOW + timedelta(seconds=999))
        self.assertEqual((gone, cleared), ([], []))                  # olay geçti: tekrar çıkarsa yine bildirilir
        back, _ = ra.plan(cleared, events, self.NOW, self.NOW + timedelta(seconds=2000))
        self.assertEqual(len(back), 2)

    def test_message_orders_by_severity(self):
        events = {"1h:div": {"tone": "warn", "text": "uyum"}, "15m:breakout_down": {"tone": "danger", "text": "kırılım"}, "4h:x": {"tone": "good", "text": "iyi"}}
        title, body = ra.message("BTC", "LONG", events, list(events))
        self.assertIn("⚠ BTC LONG", title)
        self.assertTrue(body.startswith("kırılım uyum"))
        self.assertIn("+1 uyarı daha", body)
        good_title, _ = ra.message("BTC", "SHORT", {"a": {"tone": "good", "text": "x"}}, ["a"])
        self.assertTrue(good_title.startswith("✅"))


class FakePool:
    def __init__(self, rows):
        self.rows, self.executed = rows, []
    async def execute(self, sql, *args):
        self.executed.append((sql, args)); return "DELETE 1"
    async def fetch(self, sql, *args): return self.rows
    async def fetchrow(self, *a, **k): return None
    async def fetchval(self, *a, **k): return 0


def row(**over):
    base = {"id": 1, "user_id": "u1", "exchange": "binance", "product_id": "BTCUSDT", "symbol": "BTCUSDT", "side": "LONG", "entry": 100.0,
            "mode": "against", "min_tf": "15m", "stop": None, "active_keys": "", "last_push_at": None}
    return {**base, **over}


def down_read():
    rows = [{"time": 1000 + i * 60, "open": 130 - i * 0.3, "high": 130 - i * 0.3 + 0.2, "low": 130 - i * 0.3 - 0.2, "close": 130 - i * 0.3, "volume": 100.0} for i in range(80)]
    return coin_read.build({tf: rows for tf in svc.ALARM_TFS})


class AlarmRunTests(unittest.TestCase):
    NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)

    def app(self, rows):
        pool = FakePool(rows)
        class App:
            class state:
                db_pool = pool
        ra.reset_schema_cache()
        return App, pool

    def test_pushes_once_for_long_when_market_turns_down(self):
        app, pool = self.app([row()])
        push = AsyncMock()
        with patch.object(svc, "read", AsyncMock(return_value=down_read())), patch.object(ra, "_push", push):
            self.assertEqual(asyncio.run(ra.run_once(app, self.NOW)), 1)
        push.assert_awaited_once()
        title, body, url = push.await_args.args[2:5]
        self.assertIn("BTCUSDT LONG", title)
        self.assertIn("pozisyonuna ters", body)
        self.assertIn("ex=binance", url)
        update = [e for e in pool.executed if e[0].startswith("UPDATE")][-1]
        self.assertIn("mtf:against", update[1][1])

    def test_no_push_when_event_already_active_or_market_favours_position(self):
        app, _ = self.app([row(active_keys="mtf:against," + ",".join(k for k in sorted(coin_read.position_events({r["tf"]: r for r in down_read()["timeframes"]}, down_read()["summary"], "LONG"))) )])
        push = AsyncMock()
        with patch.object(svc, "read", AsyncMock(return_value=down_read())), patch.object(ra, "_push", push):
            self.assertEqual(asyncio.run(ra.run_once(app, self.NOW)), 0)
        push.assert_not_awaited()
        app2, _ = self.app([row(side="SHORT")])
        with patch.object(svc, "read", AsyncMock(return_value=down_read())), patch.object(ra, "_push", push):
            self.assertEqual(asyncio.run(ra.run_once(app2, self.NOW)), 0)
        push.assert_not_awaited()

    def test_data_failure_skips_without_crash_and_failed_push_is_retried(self):
        app, pool = self.app([row()])
        with patch.object(svc, "read", AsyncMock(side_effect=RuntimeError("down"))), patch.object(ra, "_push", AsyncMock()) as push:
            self.assertEqual(asyncio.run(ra.run_once(app, self.NOW)), 0)
        push.assert_not_awaited()
        app, pool = self.app([row()])
        with patch.object(svc, "read", AsyncMock(return_value=down_read())), patch.object(ra, "_push", AsyncMock(side_effect=RuntimeError("push"))):
            self.assertEqual(asyncio.run(ra.run_once(app, self.NOW)), 0)
        self.assertFalse([e for e in pool.executed if e[0].startswith("UPDATE")])   # aktif yazılmadı: yeniden denenir

    def test_coinbase_alarm_removed_when_position_closed_but_kept_when_unreadable(self):
        cb = row(exchange="coinbase", product_id="BIP-20DEC30-CDE")
        app, pool = self.app([cb])
        with patch.object(ra, "_open_coinbase_products", AsyncMock(return_value=set())), patch.object(svc, "read", AsyncMock(return_value=down_read())), patch.object(ra, "_push", AsyncMock()) as push:
            asyncio.run(ra.run_once(app, self.NOW))
        push.assert_not_awaited()
        self.assertTrue([e for e in pool.executed if "DELETE FROM kaistrade_read_alarm WHERE user_id" in e[0]])
        app, pool = self.app([cb])
        with patch.object(ra, "_open_coinbase_products", AsyncMock(return_value=None)), patch.object(svc, "read", AsyncMock(return_value=down_read())), patch.object(ra, "_push", AsyncMock()) as push:
            asyncio.run(ra.run_once(app, self.NOW))
        push.assert_awaited_once()
        self.assertFalse([e for e in pool.executed if "DELETE FROM kaistrade_read_alarm WHERE user_id" in e[0]])

    def test_no_pool_is_noop(self):
        class App:
            class state: pass
        self.assertEqual(asyncio.run(ra.run_once(App, self.NOW)), 0)


if __name__ == "__main__":
    unittest.main()


class StopEventTests(unittest.TestCase):
    def test_long_near_hit_and_far(self):
        self.assertEqual(list(ra.stop_events("LONG", 99.0, 99.2, None)), ["stop:near"])      # %0,20 kaldı (< %0,3)
        self.assertEqual(ra.stop_events("LONG", 99.0, 101.0, None), {})                      # %1,98 uzak
        self.assertEqual(list(ra.stop_events("LONG", 99.0, 98.9, None)), ["stop:hit"])       # geçti
        self.assertEqual(list(ra.stop_events("LONG", 99.0, 99.0, None)), ["stop:hit"])       # tam üstünde

    def test_short_mirrors(self):
        self.assertEqual(list(ra.stop_events("SHORT", 101.0, 100.8, None)), ["stop:near"])
        self.assertEqual(list(ra.stop_events("SHORT", 101.0, 101.2, None)), ["stop:hit"])
        self.assertEqual(ra.stop_events("SHORT", 101.0, 95.0, None), {})

    def test_threshold_scales_with_atr_but_is_capped(self):
        self.assertEqual(ra.stop_events("LONG", 99.0, 99.6, None), {})                       # %0,6 > %0,3
        self.assertEqual(list(ra.stop_events("LONG", 99.0, 99.6, 1.4)), ["stop:near"])       # yarım ATR = %0,7
        self.assertEqual(ra.stop_events("LONG", 98.0, 100.0, 10.0), {})                      # tavan %1; %2 uzak

    def test_missing_stop_or_price_is_silent(self):
        self.assertEqual(ra.stop_events("LONG", None, 100.0, 1.0), {})
        self.assertEqual(ra.stop_events("LONG", 99.0, 0, 1.0), {})

    def test_normalize_keeps_only_valid_stop(self):
        self.assertEqual(ra.normalize("binance", "BTCUSDT", "BTCUSDT", "LONG", 100, "all", "5m", 98.5)["stop"], 98.5)
        self.assertIsNone(ra.normalize("binance", "BTCUSDT", "BTCUSDT", "LONG", 100, "all", "5m", -1)["stop"])
        self.assertIsNone(ra.normalize("binance", "BTCUSDT", "BTCUSDT", "LONG", 100, "all", "5m", "x")["stop"])

    def test_run_pushes_when_price_nears_stop(self):
        flat = coin_read.build({tf: [{"time": 1000 + i * 60, "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 100.0} for i in range(80)] for tf in svc.ALARM_TFS})
        class App:
            class state:
                db_pool = FakePool([row(stop=99.9, mode="against")])
        ra.reset_schema_cache()
        push = AsyncMock()
        with patch.object(svc, "read", AsyncMock(return_value={**flat, "atr": None})), patch.object(ra, "_push", push):
            self.assertEqual(asyncio.run(ra.run_once(App, datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc))), 1)
        self.assertIn("stopuna yaklaştı", push.await_args.args[3])
