"""Testler için DURUMLU sahte Coinbase (gerçek ağ yok). Pozisyon, emir, bakiye tutar; hataları ayarlanabilir."""
import email.utils
import json
import re
from decimal import Decimal
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric import ec

from app.coinbase import trading_client
from app.coinbase.errors import CoinbaseError

NOW = 1_800_000_000.0
KEY_NAME = "organizations/org-123/apiKeys/key-456"
PRODUCT = "ETP-20DEC30-CDE"


def _resp(payload, status=200):
    headers = {"date": email.utils.formatdate(NOW, usegmt=True)}
    return status, headers, json.dumps(payload).encode()


class FakePool:
    """journal için: kilit + olay listesi."""

    def __init__(self):
        self.events = []
        self.lock = None

    async def execute(self, sql, *args):
        if sql.lstrip().startswith("INSERT INTO kaistrade_coinbase_events"):
            user_id, action, product_id, order_id, role, outcome, detail = args
            self.events.append({"action": action, "product_id": product_id, "order_id": order_id, "role": role, "outcome": outcome,
                                "detail": json.loads(detail) if detail else None})
        elif sql.lstrip().startswith("DELETE FROM kaistrade_coinbase_flow_lock"):
            if self.lock == args[1]:
                self.lock = None

    async def fetchrow(self, sql, *args):
        if sql.lstrip().startswith("SELECT detail"):
            for event in reversed(self.events):
                if event["product_id"] == args[1] and event["outcome"] == "rest_sent":
                    return {"detail": event["detail"]}
            return None
        if self.lock is None:
            self.lock = args[1]
            return {"token": args[1]}
        return None

    async def fetch(self, sql, *args):
        return []

    def outcomes(self):
        return [e["outcome"] for e in self.events]


class FakeExchange:
    def __init__(self, *, bid="3000", ask="3000.5", daily_pnl="0", buying_power="500", contract_size="0.01"):
        self.bid, self.ask = bid, ask
        self.daily_pnl = daily_pnl
        self.buying_power = buying_power
        self.contract_size = contract_size
        self.positions = {}          # product -> {"side": "BUY"/"SELL", "contracts": int}
        self.orders = {}             # id -> dict
        self.calls = []              # (method, path, body)
        self.seq = 0
        # davranış ayarları
        self.entry_mode = "fill"     # fill | reject | partial | nofill | lost_response
        self.partial_contracts = 3
        self.stop_mode = "ok"        # ok | reject | wrong_size | inactive
        self.bracket_mode = "ok"     # ok | reject | wrong_size | inactive
        self.rest_mode = "ok"        # ok | reject
        self.attached_works = True   # False: borsa ekli bracket'ı sessizce yok sayar (doldurunca korumasız kalır)
        self.close_fails = False
        self.close_noop = False      # istek başarılı görünür ama pozisyon kapanmaz
        self.cancel_fails = set()    # bu id'ler iptal edilemez
        self.margin_level = "MARGIN_LEVEL_TYPE_BASE"
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.product_rows = [
            {"product_id": "ETP-20DEC30-CDE", "display_name": "ETH PERP", "price": "3000.5", "price_percentage_change_24h": "-1.25",
             "approximate_quote_24h_volume": "5000000", "status": "online", "trading_disabled": False,
             "future_product_details": {"contract_root_unit": "eth", "contract_size": "0.01", "contract_expiry": "2030-12-20T16:00:00Z"}},
            {"product_id": "BIP-20DEC30-CDE", "display_name": "BTC PERP", "price": "100000", "price_percentage_change_24h": "0.5",
             "approximate_quote_24h_volume": "90000000", "status": "online", "trading_disabled": False,
             "future_product_details": {"contract_root_unit": "BTC", "contract_size": "0.01"}},
            {"product_id": "XXX-20DEC30-CDE", "display_name": "XXX", "price": "oops", "status": "offline", "trading_disabled": True,
             "future_product_details": {}},
        ]

    def client(self):
        async def sleep(_):
            return None
        return trading_client.CoinbaseTradingClient(
            KEY_NAME, self.key, transport=self.transport, clock=lambda: NOW, monotonic=lambda: 1000.0, sleep=sleep, min_interval=0.0,
        )

    # --- yardımcılar
    def _new_id(self, prefix="o"):
        self.seq += 1
        return f"{prefix}-{self.seq:08d}"

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]

    def active_stops(self):
        return [o for o in self.orders.values() if o["status"] == "OPEN" and o["order_type"] == "STOP_LIMIT"]

    def fill_resting(self, order_id):
        """Bekleyen limit doldu: pozisyon açılır; ekli bracket çalışıyorsa borsa onu kendisi kurar."""
        order = self.orders[order_id]
        order["status"] = "FILLED"
        size = int(order["base_size"])
        self.positions[order["product_id"]] = {"side": order["side"], "contracts": size}
        attached = order.get("attached")
        if attached and self.attached_works:
            br = attached["trigger_bracket_gtc"]
            oid = self._new_id("br")
            self.orders[oid] = {
                "order_id": oid, "client_order_id": f"exch-{oid}", "product_id": order["product_id"],
                "side": "SELL" if order["side"] == "BUY" else "BUY", "status": "OPEN", "order_type": "BRACKET",
                "base_size": str(size), "stop_price": br["stop_trigger_price"], "tp": br["limit_price"],
            }

    def set_position(self, product, side, contracts):
        self.positions[product] = {"side": side, "contracts": contracts}

    def add_stop(self, product, side, contracts, stop="2940", prefix="kais-s-"):
        oid = self._new_id("stop")
        self.orders[oid] = {
            "order_id": oid, "client_order_id": f"{prefix}{oid}", "product_id": product, "side": "SELL" if side == "BUY" else "BUY",
            "status": "OPEN", "order_type": "STOP_LIMIT", "base_size": str(contracts), "stop_price": str(stop), "limit_price": str(stop),
        }
        return oid

    # --- aktarım
    async def transport(self, method, url, headers, params, timeout, content=None):
        path = urlparse(url).path
        body = json.loads(content) if content else None
        self.calls.append((method, path, body))
        if method == "GET":
            return self._get(path, params)
        return self._post(path, body)

    def _order_json(self, o):
        cfg = {}
        if o["order_type"] == "BRACKET":
            cfg = {"trigger_bracket_gtc": {"base_size": o["base_size"], "limit_price": o["tp"], "stop_trigger_price": o["stop_price"]}}
        if o["order_type"] == "STOP_LIMIT":
            cfg = {"stop_limit_stop_limit_gtc": {"base_size": o["base_size"], "limit_price": o["limit_price"], "stop_price": o["stop_price"],
                                                 "stop_direction": "STOP_DIRECTION_STOP_DOWN"}}
        return {"order_id": o["order_id"], "client_order_id": o["client_order_id"], "product_id": o["product_id"], "side": o["side"],
                "status": o["status"], "order_type": o["order_type"], "filled_size": o.get("filled_size", "0"),
                "average_filled_price": o.get("avg", ""), "order_configuration": cfg}

    def _get(self, path, params):
        if path.endswith("/cfm/balance_summary"):
            amt = lambda v: {"value": v, "currency": "USD"}  # noqa: E731
            window = {"margin_window_type": "FCM_MARGIN_WINDOW_TYPE_INTRADAY", "margin_level": self.margin_level,
                      "futures_buying_power": self.buying_power}
            return _resp({"balance_summary": {
                "futures_buying_power": amt(self.buying_power), "available_margin": amt(self.buying_power),
                "daily_realized_pnl": amt(self.daily_pnl), "total_usd_balance": amt("500"),
                "intraday_margin_window_measure": window,
                "overnight_margin_window_measure": {**window, "margin_window_type": "FCM_MARGIN_WINDOW_TYPE_OVERNIGHT"},
            }})
        if path.endswith("/cfm/intraday/current_margin_window"):
            return _resp({"margin_window": {"margin_window_type": "FCM_MARGIN_WINDOW_TYPE_INTRADAY"},
                          "is_intraday_margin_killswitch_enabled": False, "is_intraday_margin_enrollment_killswitch_enabled": False})
        if path.endswith("/cfm/positions"):
            rows = [{"product_id": p, "side": "FUTURES_POSITION_SIDE_LONG" if v["side"] == "BUY" else "FUTURES_POSITION_SIDE_SHORT",
                     "number_of_contracts": str(v["contracts"]), "avg_entry_price": self.ask}
                    for p, v in self.positions.items() if v["contracts"] > 0]
            return _resp({"positions": rows})
        if path.endswith("/best_bid_ask"):
            pid = params["product_ids"][0] if isinstance(params["product_ids"], list) else params["product_ids"]
            return _resp({"pricebooks": [{"product_id": pid, "bids": [{"price": self.bid, "size": "1"}], "asks": [{"price": self.ask, "size": "1"}]}]})
        if path.endswith("/orders/historical/batch"):
            rows = [self._order_json(o) for o in self.orders.values() if o["status"] in ("OPEN", "PENDING", "QUEUED")]
            return _resp({"orders": rows, "has_next": False, "cursor": ""})
        m = re.search(r"/orders/historical/([^/]+)$", path)
        if m:
            o = self.orders.get(m.group(1))
            return _resp({"order": self._order_json(o)}) if o else _resp({"error": "NOT_FOUND"}, 404)
        if path.endswith("/brokerage/products"):
            self.product_list_calls = getattr(self, "product_list_calls", 0) + 1
            return _resp({"products": self.product_rows})
        m = re.search(r"/products/([^/]+)$", path)
        if m:
            return _resp({"product_id": m.group(1), "status": "online", "base_increment": "1", "quote_increment": "0.5",
                          "base_min_size": "1", "future_product_details": {"contract_size": self.contract_size}})
        raise AssertionError(f"beklenmeyen GET {path}")

    def _ok(self, order_id):
        return _resp({"success": True, "success_response": {"order_id": order_id}})

    def _fail(self, reason):
        return _resp({"success": False, "error_response": {"new_order_failure_reason": reason, "message": "test"}})

    def _post(self, path, body):
        if path.endswith("/orders/close_position"):
            if self.close_fails:
                return self._fail("UNSUPPORTED_ORDER_CONFIGURATION")
            pos = self.positions.get(body["product_id"])
            if pos and not self.close_noop:
                pos["contracts"] -= int(body["size"])
                if pos["contracts"] <= 0:
                    del self.positions[body["product_id"]]
            return self._ok(self._new_id("close"))
        if path.endswith("/orders/batch_cancel"):
            results = []
            for oid in body["order_ids"]:
                if oid in self.cancel_fails or oid not in self.orders:
                    results.append({"success": False, "failure_reason": "UNKNOWN_CANCEL_FAILURE_REASON", "order_id": oid})
                else:
                    self.orders[oid]["status"] = "CANCELLED"
                    results.append({"success": True, "failure_reason": "UNKNOWN_CANCEL_FAILURE_REASON", "order_id": oid})
            return _resp({"results": results})
        if path.endswith("/brokerage/orders"):
            cfg = body["order_configuration"]
            if "market_market_ioc" in cfg:
                return self._entry(body, int(cfg["market_market_ioc"]["base_size"]))
            if "limit_limit_fok" in cfg:
                fok = cfg["limit_limit_fok"]
                cap = Decimal(fok["limit_price"])
                reachable = Decimal(str(self.ask)) <= cap if body["side"] == "BUY" else Decimal(str(self.bid)) >= cap
                saved = self.entry_mode
                if not reachable:
                    self.entry_mode = "nofill"
                try:
                    return self._entry(body, int(fok["base_size"]))
                finally:
                    self.entry_mode = saved
            if "trigger_bracket_gtc" in cfg:
                if self.bracket_mode == "reject":
                    return self._fail("UNSUPPORTED_ORDER_CONFIGURATION")
                br = cfg["trigger_bracket_gtc"]
                oid = self._new_id("br")
                size = br["base_size"] if self.bracket_mode != "wrong_size" else str(int(br["base_size"]) + 1)
                self.orders[oid] = {
                    "order_id": oid, "client_order_id": body["client_order_id"], "product_id": body["product_id"], "side": body["side"],
                    "status": "CANCELLED" if self.bracket_mode == "inactive" else "OPEN", "order_type": "BRACKET", "base_size": size,
                    "stop_price": br["stop_trigger_price"], "tp": br["limit_price"],
                }
                return self._ok(oid)
            if "limit_limit_gtc" in cfg:
                if self.rest_mode == "reject":
                    return self._fail("UNSUPPORTED_ORDER_CONFIGURATION")
                oid = self._new_id("rest")
                self.orders[oid] = {
                    "order_id": oid, "client_order_id": body["client_order_id"], "product_id": body["product_id"], "side": body["side"],
                    "status": "OPEN", "order_type": "LIMIT", "base_size": cfg["limit_limit_gtc"]["base_size"],
                    "attached": body.get("attached_order_configuration"),
                }
                return self._ok(oid)
            stop_cfg = cfg["stop_limit_stop_limit_gtc"]
            if self.stop_mode == "reject":
                return self._fail("INVALID_LIMIT_PRICE")
            oid = self._new_id("stop")
            size = stop_cfg["base_size"] if self.stop_mode != "wrong_size" else str(int(stop_cfg["base_size"]) + 1)
            self.orders[oid] = {
                "order_id": oid, "client_order_id": body["client_order_id"], "product_id": body["product_id"], "side": body["side"],
                "status": "CANCELLED" if self.stop_mode == "inactive" else "OPEN", "order_type": "STOP_LIMIT", "base_size": size,
                "stop_price": stop_cfg["stop_price"], "limit_price": stop_cfg["limit_price"],
            }
            return self._ok(oid)
        raise AssertionError(f"beklenmeyen POST {path}")

    def _entry(self, body, size):
        # Aynı client_order_id ikinci kez gelirse Coinbase gibi MEVCUT emri döndür (çift pozisyon açma)
        for existing in self.orders.values():
            if existing["client_order_id"] == body["client_order_id"]:
                if self.entry_mode == "lost_always":
                    raise CoinbaseError("NETWORK", "bağlantı koptu")
                return self._ok(existing["order_id"])
        if self.entry_mode == "reject":
            return self._fail("INSUFFICIENT_FUND")
        lost = self.entry_mode in ("lost_response", "lost_always")
        filled = {"fill": size, "lost_response": size, "lost_always": size, "partial": min(self.partial_contracts, size), "nofill": 0}[self.entry_mode]
        oid = self._new_id("entry")
        self.orders[oid] = {"order_id": oid, "client_order_id": body["client_order_id"], "product_id": body["product_id"], "side": body["side"],
                            "status": "FILLED" if filled == size else "CANCELLED", "order_type": "MARKET",
                            "filled_size": str(filled), "avg": self.ask}
        if filled:
            self.positions[body["product_id"]] = {"side": body["side"], "contracts": filled}
        if lost:
            raise CoinbaseError("NETWORK", "bağlantı koptu")
        return self._ok(oid)
