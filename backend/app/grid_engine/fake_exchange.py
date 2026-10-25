"""SAHTE borsa: gerçek anahtar/para/ağ yok. Hata enjekte edilebilir. Tek yönlü (one-way) pozisyon modeli."""
from __future__ import annotations

from typing import Callable

from .ports import (BUY, CANCELED, DUPLICATE_CLIENT_ID, EPS, FILLED, LIMIT, MARKET, NEW, PARTIAL,
                    REDUCE_ONLY_REJECTED, SELL, STOP_MARKET, UNKNOWN_ORDER, WOULD_TRIGGER_IMMEDIATELY, ExchangeRejected,
                    ExchangeTimeout, ExchangeUnreachable, OrderInfo)


class FakeExchange:
    def __init__(self, *, tick: float = 0.1, step: float = 0.001, min_notional: float = 5.0) -> None:
        self.tick, self.step, self.min_notional = tick, step, min_notional
        self.prices: dict[str, float] = {}
        self.orders: dict[str, OrderInfo] = {}
        self.positions: dict[str, float] = {}
        self.connected = True
        self.lose_response_for: set[str] = set()      # emir işlenir, yanıt kaybolur (Timeout)
        self.lose_response_always = False
        self.dup_never_rejected = False                # kimlik tekrarını HİÇ reddetmeyen (kötü senaryo) borsa
        self.reuse_final_ids = False                   # biten (dolu/iptal) emrin kimliği yeniden kullanılabilir
        self.reject_next: list[tuple[int, str]] = []
        self.hooks: list[Callable[[str, dict], None]] = []  # her çağrının başında çalışır
        self.cancel_race: dict[str, float] = {}       # client_id -> iptalden hemen önce dolacak miktar
        self.calls: list[tuple[str, str]] = []
        self.placed_count: dict[str, int] = {}        # client_id -> kaç kez GERÇEKTEN kabul edildi

    # ---- test yardımcıları ----
    def set_position(self, symbol: str, qty: float) -> None:
        self.positions[symbol] = qty

    def _enter(self, method: str, **info) -> None:
        self.calls.append((method, str(info.get("client_id", ""))))
        for hook in list(self.hooks):
            hook(method, info)
        if not self.connected:
            raise ExchangeUnreachable("bağlantı yok")

    def _check_filters(self, qty: float, price: float, type: str) -> None:
        if qty <= EPS or abs(qty / self.step - round(qty / self.step)) > 1e-6:
            raise ExchangeRejected(-1013, f"miktar adımı ({self.step}) uygun değil")
        if type == LIMIT:
            if price <= 0 or abs(price / self.tick - round(price / self.tick)) > 1e-6:
                raise ExchangeRejected(-1111, f"fiyat adımı ({self.tick}) uygun değil")

    # ---- borsa arayüzü ----
    def place_order(self, *, client_id, symbol, side, type, qty, price=0.0, stop_price=0.0, reduce_only=False):
        self._enter("place_order", client_id=client_id)
        if self.reject_next:
            code, msg = self.reject_next.pop(0)
            raise ExchangeRejected(code, msg)
        existing = self.orders.get(client_id)
        if existing is not None and not self.dup_never_rejected:
            if existing.status in (NEW, PARTIAL) or not self.reuse_final_ids:
                raise ExchangeRejected(DUPLICATE_CLIENT_ID, "ClientOrderId is duplicated")
        self._check_filters(qty, price, type)
        mark = self.prices.get(symbol, 0.0)
        if type != STOP_MARKET and not reduce_only and qty * (price or mark) < self.min_notional - 1e-9:
            raise ExchangeRejected(-4164, "emir değeri en düşük sınırın altında")
        if type == STOP_MARKET and mark and ((side == SELL and mark <= stop_price) or (side == BUY and mark >= stop_price)):
            raise ExchangeRejected(WOULD_TRIGGER_IMMEDIATELY, "Order would immediately trigger")
        pos = self.positions.get(symbol, 0.0)
        if reduce_only:
            closes = (side == SELL and pos > EPS) or (side == BUY and pos < -EPS)
            if not closes or qty > abs(pos) + EPS:
                raise ExchangeRejected(REDUCE_ONLY_REJECTED, "ReduceOnly Order is rejected")
        order = OrderInfo(client_id, symbol, side, type, price, stop_price, qty, 0.0, NEW, reduce_only)
        self.orders[client_id] = order
        self.placed_count[client_id] = self.placed_count.get(client_id, 0) + 1
        if type == MARKET:
            self._fill(order, qty, mark)
        elif type == LIMIT:
            if (side == BUY and mark and mark <= price) or (side == SELL and mark and mark >= price):
                self._fill(order, qty, price)
        if self.lose_response_always or client_id in self.lose_response_for:
            self.lose_response_for.discard(client_id)
            raise ExchangeTimeout("yanıt alınamadı")
        return self._copy(order)

    def get_order(self, symbol, client_id):
        self._enter("get_order", client_id=client_id)
        order = self.orders.get(client_id)
        return self._copy(order) if order else None

    def open_orders(self, symbol):
        self._enter("open_orders")
        return [self._copy(o) for o in self.orders.values() if o.symbol == symbol and o.status in (NEW, PARTIAL)]

    def cancel_order(self, symbol, client_id):
        self._enter("cancel_order", client_id=client_id)
        order = self.orders.get(client_id)
        if order is None:
            raise ExchangeRejected(UNKNOWN_ORDER, "Unknown order")
        race = self.cancel_race.pop(client_id, 0.0)
        if race and order.status in (NEW, PARTIAL):
            self._fill(order, min(race, order.qty - order.filled), order.price or order.stop_price)
        if order.status not in (NEW, PARTIAL):
            raise ExchangeRejected(UNKNOWN_ORDER, "Order does not exist (dolmuş ya da iptal)")
        order.status = CANCELED
        return self._copy(order)

    def position(self, symbol):
        self._enter("position")
        return self.positions.get(symbol, 0.0)

    # ---- fiyat ve dolum simülasyonu ----
    def set_price(self, symbol: str, price: float) -> None:
        self.prices[symbol] = price
        for order in list(self.orders.values()):
            if order.symbol != symbol or order.status not in (NEW, PARTIAL):
                continue
            remaining = order.qty - order.filled
            if order.type == LIMIT:
                if (order.side == BUY and price <= order.price) or (order.side == SELL and price >= order.price):
                    self._fill(order, remaining, order.price)
            elif order.type == STOP_MARKET:
                if (order.side == SELL and price <= order.stop_price) or (order.side == BUY and price >= order.stop_price):
                    pos = abs(self.positions.get(symbol, 0.0))
                    qty = min(remaining, pos) if order.reduce_only else remaining
                    if qty <= EPS:
                        order.status = CANCELED
                    else:
                        self._fill(order, qty, order.stop_price)

    def partial_fill(self, client_id: str, qty: float) -> None:
        order = self.orders[client_id]
        self.prices[order.symbol] = order.price or order.stop_price   # dolum fiyatında dur (diğer emirleri tetikleme)
        self._fill(order, qty, order.price or order.stop_price)

    def _fill(self, order: OrderInfo, qty: float, price: float) -> None:
        qty = round(min(qty, order.qty - order.filled), 8)
        if qty <= EPS:
            return
        order.filled = round(order.filled + qty, 8)
        order.status = FILLED if order.qty - order.filled <= EPS else PARTIAL
        signed = qty if order.side == BUY else -qty
        self.positions[order.symbol] = round(self.positions.get(order.symbol, 0.0) + signed, 8)

    @staticmethod
    def _copy(order: OrderInfo) -> OrderInfo:
        return OrderInfo(**order.__dict__)
