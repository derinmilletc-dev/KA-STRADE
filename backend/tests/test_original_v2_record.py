"""Original V2 kayıt altyapısı: yalnız Original, ağ çağrısı yok, hata işlemi etkilemez, mükerrer kayıt yok."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

from app import original_v2_record as rec  # noqa: E402
from app import signal_journal as journal_module  # noqa: E402
from app.signal_journal import Journal, Observer  # noqa: E402

APP = Path(__file__).parents[1] / "app"
# Hiçbir test gerçek günlük dosyası oluşturmasın: yayımlama varsayılan olarak yakalanır.
_DEFAULT_CAPTURE = None


def setUpModule():
    global _DEFAULT_CAPTURE
    _DEFAULT_CAPTURE = patch.object(journal_module, "emit", lambda *args, **kwargs: None)
    _DEFAULT_CAPTURE.start()


def tearDownModule():
    _DEFAULT_CAPTURE.stop()


def client_id_for(kind, intent):  # canlı fonksiyonun yerine yalnız test kimliği üretir
    return f"KT_{kind}_{intent}"


def strategy_result():
    legacy = {
        "signal_timestamp": 1000, "decision_time": 1900,
        "analysis": {
            "direction": "LONG", "confidence": 91, "entry": 100.0, "stop_loss": 98.0, "tp1": 102.0, "tp2": 104.0,
            "tp3": 106.0, "atr": 1.0, "adx": 30.0, "rsi": 61.0, "macd": 0.4, "momentum": "Pozitif", "trend": "Güçlü yükseliş",
            "volume_ratio": 1.3, "support": 97.0, "resistance": 105.0,
            "ema": {"ema20": 99.5, "ema50": 98.5, "ema200": 95.0},
            "bollinger": {"upper": 102.0, "middle": 100.0, "lower": 98.0},
            "radar": {"trap_score": 20, "breakout_quality": 70},
        },
        "mtf": {"alignment": 80, "verdict": "GÜÇLÜ ONAY", "higher_timeframe_confirmation": True,
                "timeframes": {"15m": {"direction": "LONG", "confidence": 91, "weight": 0.25},
                               "1h": {"direction": "LONG", "confidence": 75, "weight": 0.35},
                               "4h": {"direction": "LONG", "confidence": 70, "weight": 0.4}}},
    }
    check = SimpleNamespace(key="confidence", passed=True, value=91, threshold=85)
    return SimpleNamespace(legacy=legacy, signal=SimpleNamespace(quality_checks=(check,)))


def plan():
    return {"id": "p1", "intent_id": "original-live-1", "symbol": "BNBUSDT", "direction": "LONG", "created_at": "2026-10-26T10:00:00+00:00",
            "initial_stop_loss": "98", "initial_risk_usdt": 1.14, "tp1_fraction": "0.70", "initial_quantity": "0.1",
            "stop_client_id": "STOPID", "targets": [{"price": "101.6"}, {"price": "104"}, {"price": "106"}]}


def spec():
    return {"symbol": "BNBUSDT", "entry_price": "100", "stop_loss": "98", "targets": ["101.6", "104", "106"], "quantity": "0.1",
            "margin_usdt": 3.3, "notional_usdt": 10.0, "leverage": 3}


class Capture:
    def __init__(self):
        self.items = []

    def emit(self, kind, **data):
        self.items.append((kind, data))


class EntrySnapshotTests(unittest.TestCase):
    def test_entry_has_signal_mtf_planned_and_explicit_gaps(self):
        p = plan()
        cap = Capture()
        with patch.object(journal_module, "emit", cap.emit):
            rec.attach_entry(p, strategy_result(), spec(), spread_bp=1.5, snapshot={"wallet_balance": 100, "available_balance": 80,
                             "positions": [{"direction": "SHORT"}]}, policy={"min_confidence": 85},
                             config={"universe": {"ranked_symbols": ["BTCUSDT", "BNBUSDT"], "eligible_count": 50}}, recorded_at="t")
        entry = p["original_record"]["entry"]
        self.assertEqual(entry["signal"]["confidence"], 91)
        self.assertEqual(entry["mtf"]["directions"], {"15m": "LONG", "1h": "LONG", "4h": "LONG"})
        self.assertEqual(entry["mtf"]["confidence"]["4h"], 70)
        self.assertEqual(entry["signal"]["ema200"], 95.0)
        self.assertEqual(entry["planned"]["entry_price"], "100")        # planlanan; gerçekleşen burada yok
        self.assertNotIn("actual", entry)
        self.assertEqual(entry["market"]["universe_rank"], 2)
        self.assertEqual(entry["account"]["open_short"], 1)
        self.assertIn("funding_rate", entry["not_recorded"])
        self.assertIsNone(entry["market"].get("quote_volume_24h"))
        self.assertEqual(cap.items[0][0], "original_v2")
        self.assertEqual(cap.items[0][1]["section"], "entry")
        json.dumps(entry, allow_nan=False)

    def test_garbage_input_never_raises_and_plan_is_untouched_on_failure(self):
        p = plan()
        rec.attach_entry(p, None, {}, spread_bp=float("nan"), snapshot=None, policy=None, config=None, recorded_at="t")
        json.dumps(p, allow_nan=False)
        q = plan()
        with patch.object(rec, "build_entry", side_effect=RuntimeError("boom")):
            rec.attach_entry(q, strategy_result(), spec(), spread_bp=1, snapshot={}, policy={}, config={}, recorded_at="t")
        self.assertNotIn("original_record", q)

    def test_publish_swallows_queue_and_import_failures(self):
        with patch.object(journal_module, "emit", side_effect=RuntimeError("queue")):
            rec.publish("x", "entry", {"a": 1})
        rec.publish("", "entry", {"a": 1})

    def test_tp1_keeps_planned_and_actual_separate(self):
        p = plan()
        cap = Capture()
        with patch.object(journal_module, "emit", cap.emit):
            rec.attach_tp1(p, {"executedQty": "0.07", "avgPrice": "101.6", "status": "FILLED", "orderId": 77, "updateTime": 1_700_000_000_000},
                           {"algoId": 5, "clientAlgoId": "c", "actualOrderId": 77}, "0.07", "t")
        tp1 = p["original_record"]["tp1"]
        self.assertEqual(tp1["planned"]["fraction"], "0.70")
        self.assertEqual(tp1["actual"]["executed_qty"], "0.07")
        self.assertEqual(tp1["actual"]["order_id"], 77)
        self.assertIsNotNone(tp1["actual"]["update_time"])
        self.assertEqual(cap.items[0][1]["section"], "tp1")


class LegTests(unittest.TestCase):
    def rows(self, fee_asset="USDT"):
        entry = [{"id": 1, "orderId": 10, "time": 1_700_000_000_000, "price": "100", "qty": "0.1", "realizedPnl": "0", "commission": "0.005",
                  "commissionAsset": fee_asset, "side": "BUY"}]
        closes = [{"id": 2, "orderId": 77, "time": 1_700_000_100_000, "price": "101.6", "qty": "0.07", "realizedPnl": "0.112", "commission": "0.0035",
                   "commissionAsset": fee_asset, "side": "SELL"},
                  {"id": 3, "orderId": 88, "time": 1_700_000_200_000, "price": "103", "qty": "0.03", "realizedPnl": "0.09", "commission": "0.0015",
                   "commissionAsset": fee_asset, "side": "SELL"},
                  {"id": 4, "orderId": 99, "time": 1_700_000_300_000, "price": "102", "qty": "0.00", "realizedPnl": "0", "commission": "0",
                   "commissionAsset": fee_asset, "side": "SELL"}]
        return entry, closes

    def test_roles_come_only_from_exchange_client_ids_and_unknown_stays_unknown(self):
        p = plan()
        entry, closes = self.rows()
        algo = [{"clientAlgoId": client_id_for("TP12", p["intent_id"]), "actualOrderId": 77},
                {"clientAlgoId": "STOPID", "actualOrderId": 88}]
        result = rec.build_legs(p, entry, closes, entry + closes, algo, client_id_for)
        roles = {leg["fill_id"]: leg["role"] for leg in result["legs"].values()}
        self.assertEqual(roles, {"1": "ENTRY", "2": "TP1", "3": "STOP", "4": "UNKNOWN"})
        self.assertEqual(result["summary"]["funding"], {"status": "NOT_COMPUTED", "usdt": None})
        self.assertTrue(result["summary"]["commission_complete"])
        self.assertAlmostEqual(result["summary"]["net_excl_funding_usdt"], 0.112 + 0.09 + 0 - (0.005 + 0.0035 + 0.0015 + 0))

    def test_bnb_commission_marks_cost_incomplete_and_no_net(self):
        p = plan()
        entry, closes = self.rows(fee_asset="BNB")
        result = rec.build_legs(p, entry, closes, entry + closes, [], client_id_for)
        self.assertFalse(result["summary"]["commission_complete"])
        self.assertEqual(result["summary"]["non_usdt_commission_assets"], ["BNB"])
        self.assertIsNone(result["summary"]["net_excl_funding_usdt"])

    def test_missing_realized_pnl_gives_unknown_gross_not_zero(self):
        p = plan()
        entry, closes = self.rows()
        del closes[0]["realizedPnl"]
        result = rec.build_legs(p, entry, closes, entry + closes, [], client_id_for)
        self.assertIsNone(result["summary"]["gross_realized_pnl"])
        self.assertIsNone(result["summary"]["net_excl_funding_usdt"])

    def test_close_reason_is_copied_not_guessed(self):
        p = plan()
        entry, closes = self.rows()
        self.assertEqual(rec.build_legs(p, entry, closes, entry + closes, [], client_id_for)["summary"]["plan_close_reason"], "UNKNOWN")
        p["close_reason"] = "TP3"
        self.assertEqual(rec.build_legs(p, entry, closes, entry + closes, [], client_id_for)["summary"]["plan_close_reason"], "TP3")

    def test_tp1_role_falls_back_to_recorded_tp1_order_id(self):
        p = plan()
        p["original_record"] = {"tp1": {"actual": {"order_id": 77}}}
        entry, closes = self.rows()
        result = rec.build_legs(p, entry, closes, entry + closes, [], client_id_for)
        self.assertEqual({leg["fill_id"]: leg["role"] for leg in result["legs"].values()}["2"], "TP1")

    def test_publish_close_reemits_entry_tp1_and_legs_and_never_raises(self):
        p = plan()
        p["original_record"] = {"entry": {"a": 1}, "tp1": {"b": 2}}
        entry, closes = self.rows()
        cap = Capture()
        with patch.object(journal_module, "emit", cap.emit):
            rec.publish_close(p, entry, closes, entry + closes, [], client_id_for)
        self.assertEqual([item[1]["section"] for item in cap.items], ["entry", "tp1", "legs"])
        with patch.object(rec, "build_legs", side_effect=RuntimeError("x")):
            rec.publish_close(p, entry, closes, entry + closes, [], client_id_for)


class JournalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "journal.sqlite3"
        self.journal = Journal(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def legs(self, *ids):
        return {"legs": {f"BNBUSDT:{i}": {"fill_id": str(i), "kind": "EXIT"} for i in ids}, "summary": {"leg_count": len(ids)}}

    def test_same_events_twice_make_no_duplicates(self):
        for _ in range(2):
            self.journal.original_v2("i1", "entry", {"x": 1})
            self.journal.original_v2("i1", "tp1", {"y": 2})
            self.journal.original_v2("i1", "legs", self.legs(1, 2))
        record = self.journal.original_v2_trades()[0]["original_v2"]
        self.assertEqual(record["entry"], {"x": 1})
        self.assertEqual(record["tp1"], {"y": 2})
        self.assertEqual(sorted(record["legs"]), ["BNBUSDT:1", "BNBUSDT:2"])
        self.assertEqual(len(self.journal.original_v2_trades()), 1)

    def test_first_entry_wins_and_partial_run_completes_later(self):
        self.journal.original_v2("i1", "entry", {"x": 1})
        self.journal.original_v2("i1", "entry", {"x": 999})
        self.journal.original_v2("i1", "legs", self.legs(1))
        self.journal.original_v2("i1", "legs", self.legs(1, 2, 3))
        record = self.journal.original_v2_trades()[0]["original_v2"]
        self.assertEqual(record["entry"], {"x": 1})
        self.assertEqual(sorted(record["legs"]), ["BNBUSDT:1", "BNBUSDT:2", "BNBUSDT:3"])
        self.assertEqual(record["close_summary"]["leg_count"], 3)

    def test_survives_restart_and_other_trade_updates(self):
        self.journal.original_v2("i1", "entry", {"x": 1})
        reopened = Journal(self.path)
        reopened.trade("i1", {"gross_pnl": 1.0, "commission_usdt": 0.1, "commission_complete": True})
        record = Journal(self.path).original_v2_trades()[0]
        self.assertEqual(record["original_v2"]["entry"], {"x": 1})
        self.assertEqual(record["gross_pnl"], 1.0)
        self.assertIsNone(record["net_pnl"])          # funding hesaplanmadığı için net yok, sıfır varsayılmaz

    def test_no_new_table_and_invalid_section_rejected(self):
        self.journal.original_v2("i1", "entry", {"x": 1})
        import sqlite3
        tables = {row[0] for row in sqlite3.connect(self.path).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"journal_metadata", "signals", "rounds", "candles", "trades"})
        with self.assertRaises(ValueError):
            self.journal.original_v2("i1", "other", {"x": 1})

    def test_observer_routes_original_events_and_survives_bad_ones(self):
        observer = Observer(self.journal)
        observer.handle("original_v2", None, {"intent_id": "i9", "section": "entry", "data": {"x": 1}})
        self.assertEqual(self.journal.original_v2_trades()[0]["intent_id"], "i9")
        with self.assertRaises(ValueError):
            observer.handle("original_v2", None, {"intent_id": "i9", "section": "bad", "data": {}})


class ScopeTests(unittest.TestCase):
    """Emir akışına dokunulmadığını ve yalnız Original'a bağlandığını kaynak düzeyinde doğrular."""

    def test_hooks_are_guarded_and_original_only(self):
        source = (APP / "v25_execution.py").read_text(encoding="utf-8")
        entry = source.index("original_v2_record.attach_entry(")
        self.assertIn("if original_entry:", source[entry - 1500:entry])
        close = source.index("original_v2_record.publish_close(")
        self.assertIn("plan_is_original(plan)", source[close - 400:close])
        for position in (entry, close):
            window = source[position - 700:position + 700]
            self.assertIn("except Exception", window)
        live_source = (APP / "original_v2_live.py").read_text(encoding="utf-8")
        tp1 = live_source.index("original_v2_record.attach_tp1(")
        self.assertIn("except Exception", live_source[tp1:tp1 + 400])

    def test_record_module_makes_no_network_or_order_calls(self):
        text = (APP / "original_v2_record.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", text, flags=re.S)
        for forbidden in ("await ", "async def", "client.", "httpx", "requests", "signed(", "public_get", "place_order", "submit_entry"):
            self.assertNotIn(forbidden, code)


class OnlyOriginalPlansTests(unittest.TestCase):
    """Tam bağımlılıklar varsa (CI): kayıt yalnız Original planlarında tetiklenir, V25/Manuel planlarda değil."""

    def test_plan_is_original_is_false_for_other_layers(self):
        try:
            from app import original_v2_live as original
        except ImportError:
            self.skipTest("tam bağımlılıklar yok")
        self.assertTrue(original.plan_is_original({"source": original.SOURCE}))
        for source in ("V25_AUTO", "MANUAL", "DEMO", None):
            self.assertFalse(original.plan_is_original({"source": source}))


if __name__ == "__main__":
    unittest.main()
