"""KAIS FARMER piyasa verisi ve aday önbelleği (C4). Karar VERMEZ: emir, marjin, slot, ARM, günlük zarar, ölü-kapanış kararı burada yoktur.

Tek okuyucu: tüm Farmer motorları için TEK `FarmerFeed`. Mumlar paylaşımlı `BinanceKlineStream` belleğinden okunur (REST yok);
tek istisna `warm_up` — akışın ortak eksik-mum indirmesi (`get_candles`) üzerinden, yalnız yetersiz geçmiş için.
Aday üretimi YALNIZ kapanmış sinyal mumu (varsayılan 3m, ortamdan 15m) üzerindedir; strateji çıktısı Original V2'nin `legacy.analysis` / `legacy.mtf` alanlarıdır
(yeni indikatör yok). Akışa abone OLMAYAN sembol için aday üretilmez ve giriş izni verilmez; varmış gibi kabul edilmez."""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence

from .confirm import FarmerCandidate
from .demand import NO_ROOM, DemandResult, merge_demand, valid_symbol

SIGNAL_INTERVAL_ENV = "KAISTRADE_FARMER_SIGNAL_INTERVAL"
SIGNAL_INTERVALS = ("3m", "15m")
INTERVAL_MS = {"3m": 180_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def _signal_interval() -> str:
    """Aday taraması hangi kapanmış mumdan yapılır. Varsayılan 3m (kurucu kararı, 2026-11-10); geri dönüş: ortamda 15m.
    Geçersiz değer sessizce varsayılana dönmez: kapalı güvenli 15m'e düşer (eski, test edilmiş davranış)."""
    import os
    value = os.getenv(SIGNAL_INTERVAL_ENV, "3m").strip().lower()
    return value if value in SIGNAL_INTERVALS else "15m"


SIGNAL_INTERVAL = _signal_interval()
INTERVALS = (SIGNAL_INTERVAL, "1h", "4h")        # 3m'de 15m akışı gerekmez (strateji penceresi 3m'den beslenir)
STRATEGY_PRIMARY = "15m"                          # Original V2 birincil pencere anahtarı; 3m'de sinyal mumu bu anahtara konur
HISTORY_CANDLES = 260                      # original_v2_demo.CANDLE_FETCH_LIMIT ile aynı; kapalı pencere 259

# veri yok nedenleri (açık raporlanır)
UNSUBSCRIBED, STREAM_DOWN, WARMUP, DATA_GAP, EVAL_ERROR, NO_CANDIDATE = (
    "UNSUBSCRIBED", "STREAM_DISCONNECTED", "WARMUP", "DATA_GAP", "EVAL_ERROR", "NO_CANDIDATE")
_TRANSIENT = {WARMUP, DATA_GAP, STREAM_DOWN, UNSUBSCRIBED}       # aynı mumda yeniden denenebilir (strateji değerlendirmesi tekrarlanmaz)


class StreamLike(Protocol):
    def is_subscribed(self, symbol: str, interval: str) -> bool: ...
    def candles(self, symbol: str, interval: str) -> list[list[Any]]: ...


Evaluator = Callable[[str, int, dict[str, Any], list[str]], Any]       # (symbol, at_seconds, frames, allowed) -> StrategyResult
FramesBuilder = Callable[[str, Mapping[str, list[list[Any]]], int], "dict[str, Any] | str"]


def default_frames_builder(symbol: str, rows: Mapping[str, list[list[Any]]], at: int) -> "dict[str, Any] | str":
    """Original V2 canlı taramasıyla AYNI normalleştirme ve bütünlük kuralı (backtest_data.Series): kapalı pencere 259, boşluksuz,
    son kapalı mum beklenen mum. Dönüş: {interval: kapalı mumlar} ya da veri-yok nedeni (WARMUP | DATA_GAP)."""
    from ..backtest_data import normalize_candles
    frames: dict[str, Any] = {}
    for interval in INTERVALS:
        raw = rows.get(interval) or []
        if len(raw) < HISTORY_CANDLES - 1:
            return WARMUP
        shaped = [{"open_time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5],
                   "close_time": r[6], "quote_volume": r[7] if len(r) > 7 else None} for r in raw]
        try:
            series, duplicates = normalize_candles(shaped, interval)
        except (ValueError, TypeError, KeyError):
            return DATA_GAP
        closed = series.closed(at)
        if duplicates or len(closed) != HISTORY_CANDLES - 1 or not series.history_complete(at):
            return DATA_GAP
        frames[interval] = closed
    if SIGNAL_INTERVAL != STRATEGY_PRIMARY:                                     # Original V2 birincil pencere anahtarı "15m"
        frames[STRATEGY_PRIMARY] = frames.pop(SIGNAL_INTERVAL)
    return frames


def default_evaluator(symbol: str, at: int, frames: dict[str, Any], allowed: list[str]) -> Any:
    """Original V2 canlı evreni değerlendirmesi (SHORT için short_filter_v2 dahil). Kais-2 kapısı KAPALI: Farmer kendi onayını uygular.
    `enabled=True` doğrudan verilir: Farmer Original V2 demo özellik bayrağına bağlı değildir."""
    from ..strategies import original_v2_demo as strategy
    from ..strategies.contracts import StrategyInput
    return strategy.evaluate_live_universe(StrategyInput(symbol, at, frames), allowed_symbols=allowed, enabled=True, kais2_gate=False)


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CachedCandidate:
    symbol: str
    interval: str
    candle_open_ms: int
    direction: str
    confidence: float | None
    momentum: str | None
    trap_score: float | None
    breakout_quality: float | None
    atr: float | None
    entry_price: float | None
    mtf: dict[str, Any]
    short_filter: dict[str, Any] | None
    snapshot: dict[str, Any]           # legacy.analysis (series hariç) — onay katmanı bunu okur
    generated_at: float

    @property
    def tradable_direction(self) -> bool:
        return self.direction in ("LONG", "SHORT")

    def to_farmer_candidate(self) -> FarmerCandidate:
        """C5 onay katmanının girdisi. Derin kopya: onay önbelleği bozamaz."""
        return FarmerCandidate(self.symbol, self.interval, self.candle_open_ms, copy.deepcopy(self.snapshot), copy.deepcopy(self.mtf))


def candidate_from_legacy(symbol: str, interval: str, candle_open_ms: int, legacy: Mapping[str, Any], generated_at: float) -> CachedCandidate:
    analysis = legacy.get("analysis") if isinstance(legacy.get("analysis"), Mapping) else {}
    mtf = legacy.get("mtf") if isinstance(legacy.get("mtf"), Mapping) else {}
    radar = analysis.get("radar") if isinstance(analysis.get("radar"), Mapping) else {}
    short_filter = mtf.get("short_filter_v2")
    return CachedCandidate(
        symbol=symbol, interval=interval, candle_open_ms=int(candle_open_ms), direction=str(analysis.get("direction") or ""),
        confidence=_num(analysis.get("confidence")), momentum=analysis.get("momentum"), trap_score=_num(radar.get("trap_score")),
        breakout_quality=_num(radar.get("breakout_quality")), atr=_num(analysis.get("atr")), entry_price=_num(analysis.get("entry")),
        mtf=copy.deepcopy(dict(mtf)), short_filter=copy.deepcopy(dict(short_filter)) if isinstance(short_filter, Mapping) else None,
        snapshot={k: copy.deepcopy(v) for k, v in analysis.items() if k != "series"}, generated_at=generated_at)


class CandidateCache:
    """Son TARANAN kapanmış mumun adayları. Yeni kapanmış mum gelince tamamen yenilenir; aynı mumda yeniden hesaplanmaz."""

    def __init__(self) -> None:
        self.candle_open_ms = 0
        self._items: dict[str, CachedCandidate] = {}
        self._unavailable: dict[str, str] = {}

    def begin(self, candle_open_ms: int) -> None:
        self.candle_open_ms = int(candle_open_ms)
        self._items.clear()
        self._unavailable.clear()

    def put(self, item: CachedCandidate) -> None:
        self._items[item.symbol] = item
        self._unavailable.pop(item.symbol, None)

    def mark_unavailable(self, symbol: str, reason: str) -> None:
        self._items.pop(symbol, None)
        self._unavailable[symbol] = reason

    def get(self, symbol: str, candle_open_ms: int | None = None) -> CachedCandidate | None:
        item = self._items.get(symbol)
        if item is None or (candle_open_ms is not None and item.candle_open_ms != int(candle_open_ms)):
            return None
        return item

    def unavailable(self) -> dict[str, str]:
        return dict(self._unavailable)

    def processed(self, symbol: str) -> bool:
        """Bu mumda sonucu kesinleşmiş mi (aday ya da kalıcı veri-yok)? Geçici nedenler yeniden denenir."""
        return symbol in self._items or (symbol in self._unavailable and self._unavailable[symbol] not in _TRANSIENT)

    def entry_candidates(self) -> list[CachedCandidate]:
        """Yalnız LONG/SHORT adaylar; güven puanı azalan, eşitlikte sembol sırası (deterministik). BEKLE giriş adayı değildir."""
        items = [c for c in self._items.values() if c.tradable_direction]
        return sorted(items, key=lambda c: (-(c.confidence if c.confidence is not None else -1.0), c.symbol))

    def all(self) -> list[CachedCandidate]:
        return sorted(self._items.values(), key=lambda c: c.symbol)


@dataclass(frozen=True)
class DeadCheckInput:
    """C3 `FarmerEngine.dead_check` girdisi. KARAR YOK: yalnız kapanmış mumun analizi ve fiyat."""
    symbol: str
    candle_open_ms: int
    analysis: dict[str, Any]           # direction / momentum / confidence (+ atr, entry ...)
    price: float | None
    price_source: str                  # "KLINE_LAST": akışın oluşmakta olan mumunun kapanışı (son işlem fiyatı), işaret fiyatı DEĞİL


@dataclass(frozen=True)
class ScanResult:
    candle_open_ms: int
    scanned: int                       # bu çağrıda strateji değerlendirilen sembol
    reused: int                        # önbellekten kullanılan (yeniden hesaplanmayan)
    new_candle: bool
    unavailable: dict[str, str]


class FarmerFeed:
    def __init__(self, stream: StreamLike | None, *, apply_demand: Callable[[list[str]], Awaitable[list[str]]] | None = None,
                 evaluator: Evaluator = default_evaluator, frames_builder: FramesBuilder = default_frames_builder,
                 clock: Callable[[], float] = time.time) -> None:
        self.stream, self._apply, self._evaluate, self._frames, self._clock = stream, apply_demand, evaluator, frames_builder, clock
        self.cache = CandidateCache()
        self._demand: list[str] = []
        self._dropped: dict[str, str] = {}
        self.evaluations = 0               # strateji değerlendirme sayacı (performans / tekrar testleri)
        self.stream_reads = 0

    # ---------- talep ----------
    @property
    def demand(self) -> list[str]:
        return list(self._demand)

    async def set_demand(self, symbols: Iterable[str]) -> dict[str, Any]:
        """Farmer'ın istediği sembolleri kaydeder ve paylaşımlı akışa EKLETİR. Gerçekten abone olunanlar `status()["subscribed"]`dır."""
        wanted: list[str] = []
        for symbol in symbols:
            if symbol not in wanted:
                wanted.append(symbol)
        preview: DemandResult = merge_demand([], wanted)        # geçersiz/sığmayan Farmer sembolleri açıkça raporlanır
        self._demand = wanted
        self._dropped = dict(preview.dropped)
        if self._apply is not None:
            applied = await self._apply(wanted)
            if isinstance(applied, (list, tuple)):
                for symbol in wanted:                              # akışa verilen listede olmayan geçerli sembol = sığmadı (100 sınırı)
                    if valid_symbol(symbol) and symbol not in applied:
                        self._dropped[symbol] = NO_ROOM
        return self.status()

    def subscribed(self, symbol: str) -> bool:
        return self.stream is not None and valid_symbol(symbol) and all(self.stream.is_subscribed(symbol, iv) for iv in INTERVALS)

    def status(self) -> dict[str, Any]:
        subscribed = [s for s in self._demand if self.subscribed(s)]
        unsubscribed = {s: self._dropped.get(s, UNSUBSCRIBED) for s in self._demand if s not in subscribed}
        return {"demand": list(self._demand), "subscribed": subscribed, "unsubscribed": unsubscribed,
                "stream_connected": bool(getattr(self.stream, "connected", True)) if self.stream is not None else False,
                "scan_candle_open_ms": self.cache.candle_open_ms, "candidates": len(self.cache.all()),
                "unavailable": self.cache.unavailable()}

    # ---------- ısınma (tek ortak indirme yolu) ----------
    async def warm_up(self, rest_fetch: Callable[[dict[str, Any]], Awaitable[Any]], *, concurrency: int = 4) -> int:
        """Yetersiz geçmişi olan ABONE sembol/aralıklar için akışın ortak `get_candles` yolunu bir kez çağırır. Dönüş: indirilen çift sayısı."""
        if self.stream is None or not hasattr(self.stream, "get_candles"):
            return 0
        todo = [(s, iv) for s in self._demand if self.subscribed(s) for iv in INTERVALS
                if len(self.stream.candles(s, iv)) < HISTORY_CANDLES]
        gate = asyncio.Semaphore(max(1, concurrency))

        async def one(symbol: str, interval: str) -> None:
            async with gate:
                await self.stream.get_candles(symbol, interval, HISTORY_CANDLES, rest_fetch)    # type: ignore[attr-defined]

        results = await asyncio.gather(*(one(s, iv) for s, iv in todo), return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, BaseException))

    # ---------- zaman ----------
    @staticmethod
    def last_closed_open_ms(now_ms: int, interval: str = SIGNAL_INTERVAL) -> int:
        step = INTERVAL_MS[interval]
        return int(now_ms) // step * step - step

    # ---------- tarama ----------
    def scan(self, now_ms: int, symbols: Sequence[str] | None = None) -> ScanResult:
        """Kapanmış sinyal mumu (3m) için aday üretir. Aynı kapanmış mumda tekrar çağrı: sonucu kesinleşmiş semboller YENİDEN hesaplanmaz;
        yalnız önbellekte olmayan (yeni talep / geçici veri-yok) semboller işlenir. Yeni kapanmış mum → önbellek tamamen yenilenir."""
        closed_ms = self.last_closed_open_ms(now_ms)
        if closed_ms < self.cache.candle_open_ms:                            # geri giden saat: önbellek geriye sarılmaz
            return ScanResult(self.cache.candle_open_ms, 0, len(self.cache.all()), False, self.cache.unavailable())
        new_candle = closed_ms != self.cache.candle_open_ms
        if new_candle:
            self.cache.begin(closed_ms)
        targets = list(self._demand if symbols is None else symbols)
        at = (closed_ms + INTERVAL_MS[SIGNAL_INTERVAL]) // 1000          # karar anı = kapanan mumun bitişi (saniye)
        scanned = reused = 0
        for symbol in targets:
            if self.cache.processed(symbol):
                reused += 1
                continue
            if self.stream is None or not self.subscribed(symbol):
                self.cache.mark_unavailable(symbol, self._dropped.get(symbol, UNSUBSCRIBED))       # abone olunmayan: aday YOK
                continue
            if not bool(getattr(self.stream, "connected", True)):
                self.cache.mark_unavailable(symbol, STREAM_DOWN)
                continue
            rows = {iv: self.stream.candles(symbol, iv) for iv in INTERVALS}
            self.stream_reads += len(INTERVALS)
            frames = self._frames(symbol, rows, at)
            if isinstance(frames, str):
                self.cache.mark_unavailable(symbol, frames)
                continue
            try:
                result = self._evaluate(symbol, at, frames, [s for s in targets if valid_symbol(s)] or [symbol])
                legacy = getattr(result, "legacy", result)
                self.evaluations += 1
                scanned += 1
                self.cache.put(candidate_from_legacy(symbol, SIGNAL_INTERVAL, closed_ms, legacy, self._clock()))
            except Exception as exc:                                         # noqa: BLE001 — bir sembol taramayı düşürmez
                self.cache.mark_unavailable(symbol, f"{EVAL_ERROR}:{exc.__class__.__name__}")
        return ScanResult(closed_ms, scanned, reused, new_candle, self.cache.unavailable())

    # ---------- giriş verisi kapısı ----------
    def entry_data_ok(self, symbol: str, now_ms: int) -> tuple[bool, str]:
        """Bu sembol için GÜNCEL kapanmış mum adayı var mı ve hâlâ abone mi? (Giriş izni DEĞİL: yalnız veri kapısı.)"""
        if not self.subscribed(symbol):
            return False, self._dropped.get(symbol, UNSUBSCRIBED)
        if not bool(getattr(self.stream, "connected", True)):
            return False, STREAM_DOWN
        closed_ms = self.last_closed_open_ms(now_ms)
        item = self.cache.get(symbol, closed_ms)
        if item is None:
            return False, self.cache.unavailable().get(symbol, NO_CANDIDATE)
        return True, ""

    # ---------- ölü-pozisyon girdisi (karar C3/C7'de) ----------
    def dead_check_input(self, symbol: str, now_ms: int) -> DeadCheckInput | None:
        """Elde tutulan sembolün SON KAPANMIŞ mum analizi (BEKLE dahil) + güncel fiyat. Yoksa None (ör. abone değil / veri yok)."""
        closed_ms = self.last_closed_open_ms(now_ms)
        item = self.cache.get(symbol, closed_ms)
        if item is None:
            return None
        price = None
        if self.stream is not None and self.subscribed(symbol):
            rows = self.stream.candles(symbol, SIGNAL_INTERVAL)
            price = _num(rows[-1][4]) if rows else None
        analysis = {"direction": item.direction, "momentum": item.momentum, "confidence": item.confidence, "atr": item.atr,
                    "entry": item.entry_price}
        return DeadCheckInput(symbol, item.candle_open_ms, analysis, price, "KLINE_LAST")
