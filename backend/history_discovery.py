"""Kais 2 tarihsel veri KEŞİF aracı: arşivde ne var, ne yok? Hiçbir veri dosyası indirmez.

Ne yapar:
  * Binance'in herkese açık USD-M arşivinin DOSYA LİSTESİNİ okur (dosya adları ve boyutları; içerik okunmaz).
  * İsteğe bağlı olarak imzasız genel sembol bilgisini (exchangeInfo) okur: listeleme tarihi, durum, sözleşme tipi.
  * Coin ve ay bazında mum (15m/1h/4h/1d), mark fiyat (15m), funding ve emir defteri (bookTicker/bookDepth)
    bulunabilirliğini, eksikleri, funding kapsamını, depolama tahminini, maliyet senaryolarını, riskleri ve
    indirme öncesi son kontrol listesini raporlar.

Ne yapmaz:
  * Mum, funding veya emir defteri dosyası İNDİRMEZ; mevcut hiçbir dosyayı değiştirmez.
  * API anahtarı, imzalı istek veya emir yetkisi KULLANMAZ; canlı bot kayıtlarına veya ayarlarına erişmez.
  * Kilitli örnek dışı dönemi (2026-06 ve sonrası) RAPORLAMAZ: liste yanıtındaki bu aylara ait adlar kaydedilmeden atılır.
  * Kais 1 kodunu içe aktarmaz; yalnız Python standart kütüphanesini kullanır.

Çıktı: yeni (veya boş) bir klasöre JSON / CSV / Markdown raporları. Canlı bot klasörlerine ve bir git deposunun içine yazmaz.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

VERSION = "history-discovery-v1"
DEV_START = "2024-05"          # 2 ay ısınma dahil
DEV_END = "2026-05"            # geliştirme dönemi sonu
HOLDOUT_START = "2026-06"      # kilitli örnek dışı: bu ve sonrası hiçbir yerde raporlanmaz
ANALYSIS_START = "2024-07"
LISTING_BASES = (
    "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
    "https://data.binance.vision",
)
EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
ALLOWED_HOSTS = {"s3-ap-northeast-1.amazonaws.com", "data.binance.vision", "fapi.binance.com"}
MONTHLY = "data/futures/um/monthly/"
DAILY = "data/futures/um/daily/"
MONTHLY_KINDS: tuple[tuple[str, str], ...] = (
    ("klines", "15m"), ("klines", "1h"), ("klines", "4h"), ("klines", "1d"),
    ("markPriceKlines", "15m"), ("fundingRate", ""),
)
REQUIRED_FOR_TEST = (("klines", "15m"), ("klines", "1h"), ("klines", "4h"), ("markPriceKlines", "15m"), ("fundingRate", ""))
ORDERBOOK_KINDS = ("bookTicker", "bookDepth")
FORBIDDEN_OUTPUT_ROOTS = ("/opt/kaistrade", "/var/lib/kaistrade", "/etc")
MIN_FREE_GB = 10
STAGE5_MB_PER_COIN_MONTH = 51.4 / (8 * 27)   # bilgisayardaki stage5 setinden ölçülen oran
UNIVERSE_SIZE = 100

# Maliyet senaryoları (simülasyon; gerçek bid-ask verisi değildir). Tek yön, baz puan (bp).
LIQUIDITY_TIERS = ((1, 10, 1.0), (11, 30, 2.0), (31, 60, 4.0), (61, 100, 7.0))
VOLATILITY_STEPS = ((1.0, 1.0), (1.5, 1.5), (3.0, 2.5))   # (ATR oranı eşiği, çarpan)
SIZE_IMPACT_BP_AT_1PCT = 5.0                               # emir / 15m hacim = %1 iken ek kayma; kare kök ölçek
SCENARIOS = {
    "iyimser": {"model": 0.5, "taker_bp": 4.5, "delist": 2.0},
    "temel": {"model": 1.0, "taker_bp": 5.0, "delist": 3.0},
    "kotumser": {"model": 2.0, "taker_bp": 5.0, "delist": 5.0},
}

MONTH_RE = re.compile(r"-(\d{4}-\d{2})\.zip$")
DAY_RE = re.compile(r"-(\d{4}-\d{2})-(\d{2})\.zip$")


# --------------------------------------------------------------------------- yardımcılar

def months(start: str, end: str) -> list[str]:
    year, month = map(int, start.split("-"))
    last = tuple(map(int, end.split("-")))
    result = []
    while (year, month) <= last:
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def locked(month: str) -> bool:
    return month >= HOLDOUT_START


def is_usdt_perpetual_name(symbol: str) -> bool:
    return symbol.endswith("USDT") and "_" not in symbol and symbol.isalnum() and symbol.upper() == symbol


def check_output(path: Path) -> Path:
    root = path.expanduser().resolve()
    text = root.as_posix()
    for forbidden in FORBIDDEN_OUTPUT_ROOTS:
        if text == forbidden or text.startswith(forbidden + "/"):
            raise ValueError(f"Çıktı canlı sistem klasörüne yazılamaz: {forbidden}")
    for parent in (root, *root.parents):
        if (parent / ".git").exists():
            raise ValueError("Çıktı bir git deposunun içinde olamaz (canlı bot klasörü olabilir).")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("Çıktı klasörü yeni veya boş olmalı; mevcut dosyalar değiştirilmez.")
    return root


class Guard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 - urllib API
        raise urllib.error.HTTPError(newurl, code, "Yönlendirme reddedildi", headers, fp)


@dataclass
class Http:
    """Yalnız izinli adreslere, kimlik bilgisi olmadan, hız sınırıyla GET."""
    rate_per_second: float = 4.0
    timeout: float = 45.0
    requests: int = 0
    _last: float = 0.0
    _opener: Any = field(default=None, repr=False)

    def get(self, url: str) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"İzinsiz adres: {url}")
        if self._opener is None:
            self._opener = urllib.request.build_opener(Guard)
        wait = 1.0 / max(self.rate_per_second, 0.1) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(url, headers={"User-Agent": VERSION})  # Authorization/API anahtarı yok
        for attempt in range(4):
            self._last = time.monotonic()
            self.requests += 1
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 3:
                    raise
            time.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


def parse_listing(payload: bytes) -> tuple[list[dict[str, Any]], list[str], bool, str | None]:
    """S3 ListObjects (v1) XML: (anahtarlar, alt önekler, devamı var mı, sonraki işaret)."""
    root = ET.fromstring(payload)
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
    keys = [{"key": item.findtext(f"{ns}Key") or "", "size": int(item.findtext(f"{ns}Size") or 0)}
            for item in root.findall(f"{ns}Contents")]
    prefixes = [item.findtext(f"{ns}Prefix") or "" for item in root.findall(f"{ns}CommonPrefixes")]
    truncated = (root.findtext(f"{ns}IsTruncated") or "false").lower() == "true"
    marker = root.findtext(f"{ns}NextMarker") or (keys[-1]["key"] if keys else (prefixes[-1] if prefixes else None))
    return keys, prefixes, truncated, marker


@dataclass
class Archive:
    http: Http
    base: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    def _list_page(self, prefix: str, marker: str | None, delimiter: str | None) -> bytes:
        params = {"prefix": prefix}
        if delimiter:
            params["delimiter"] = delimiter
        if marker:
            params["marker"] = marker
        query = urllib.parse.urlencode(params)
        bases = [self.base] if self.base else list(LISTING_BASES)
        last_error: Exception | None = None
        for base in bases:
            try:
                payload = self.http.get(f"{base}?{query}")
                ET.fromstring(payload)  # XML değilse bu adres liste vermiyor
                self.base = base
                return payload
            except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
                last_error = exc
        raise RuntimeError(f"Arşiv listesi alınamadı: {last_error}")

    def list(self, prefix: str, *, delimiter: str | None = None, start_after: str | None = None,
             stop_month: str | None = HOLDOUT_START) -> tuple[list[dict[str, Any]], list[str]]:
        """Önek altındaki anahtarlar. Kilitli aylara ait adlar KAYDEDİLMEZ ve o noktada listeleme durur."""
        keys: list[dict[str, Any]] = []
        prefixes: list[str] = []
        marker = start_after
        while True:
            page_keys, page_prefixes, truncated, marker = parse_listing(self._list_page(prefix, marker, delimiter))
            reached_lock = False
            for item in page_keys:
                found = MONTH_RE.search(item["key"].removesuffix(".CHECKSUM")) or DAY_RE.search(item["key"].removesuffix(".CHECKSUM"))
                month = found.group(1) if found else None
                if month and stop_month and month >= stop_month:
                    reached_lock = True
                    continue  # kilitli dönem: kaydetme
                keys.append(item)
            prefixes.extend(page_prefixes)
            if reached_lock or not truncated or not marker:
                return keys, prefixes


# --------------------------------------------------------------------------- keşif

def month_files(keys: list[dict[str, Any]]) -> dict[str, int]:
    """Aylık zip dosyaları: {ay: bayt}. CHECKSUM dosyaları sayılmaz."""
    result: dict[str, int] = {}
    for item in keys:
        if item["key"].endswith(".zip"):
            found = MONTH_RE.search(item["key"])
            if found and not locked(found.group(1)):
                result[found.group(1)] = item["size"]
    return result


def day_counts(keys: list[dict[str, Any]]) -> dict[str, int]:
    """Günlük zip dosyaları: {ay: gün sayısı}."""
    result: dict[str, int] = {}
    for item in keys:
        if item["key"].endswith(".zip"):
            found = DAY_RE.search(item["key"])
            if found and not locked(found.group(1)):
                result[found.group(1)] = result.get(found.group(1), 0) + 1
    return result


def kind_label(kind: str, interval: str) -> str:
    return f"{kind}_{interval}" if interval else kind


def discover(archive: Archive, *, exchange_info: dict[str, Any] | None, symbols: list[str] | None = None,
             orderbook: bool = True, progress: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    dev_months = months(DEV_START, DEV_END)
    _, symbol_prefixes = archive.list(MONTHLY + "klines/", delimiter="/", stop_month=None)
    archived = sorted({p.rstrip("/").split("/")[-1] for p in symbol_prefixes})
    info = {row.get("symbol"): row for row in (exchange_info or {}).get("symbols", []) if isinstance(row, dict)}
    candidates: list[str] = []
    excluded: list[dict[str, str]] = []
    for symbol in sorted(set(archived) | {s for s, row in info.items() if row.get("contractType") == "PERPETUAL"}):
        row = info.get(symbol)
        if not is_usdt_perpetual_name(symbol):
            excluded.append({"symbol": symbol, "reason": "USDT perpetual adı değil"})
        elif row and (row.get("contractType") not in {None, "PERPETUAL"} or row.get("quoteAsset") not in {None, "USDT"}):
            excluded.append({"symbol": symbol, "reason": f"sözleşme {row.get('contractType')} / {row.get('quoteAsset')}"})
        else:
            candidates.append(symbol)
    if symbols:
        candidates = [s for s in candidates if s in set(symbols)]
    coins: dict[str, Any] = {}
    for index, symbol in enumerate(candidates, 1):
        progress(f"[{index}/{len(candidates)}] {symbol}")
        entry: dict[str, Any] = {"symbol": symbol, "kinds": {}, "orderbook": {}}
        for kind, interval in MONTHLY_KINDS:
            prefix = f"{MONTHLY}{kind}/{symbol}/" + (f"{interval}/" if interval else "")
            try:
                keys, _ = archive.list(prefix)
                entry["kinds"][kind_label(kind, interval)] = month_files(keys)
            except RuntimeError as exc:
                archive.errors.append({"symbol": symbol, "kind": kind_label(kind, interval), "error": str(exc)})
                entry["kinds"][kind_label(kind, interval)] = None
        if orderbook:
            for kind in ORDERBOOK_KINDS:
                try:
                    keys, _ = archive.list(f"{DAILY}{kind}/{symbol}/", start_after=f"{DAILY}{kind}/{symbol}/{symbol}-{kind}-{DEV_START}")
                    entry["orderbook"][kind] = day_counts(keys)
                except RuntimeError as exc:
                    archive.errors.append({"symbol": symbol, "kind": kind, "error": str(exc)})
                    entry["orderbook"][kind] = None
        coins[symbol] = summarise_coin(entry, info.get(symbol), dev_months)
    return {"version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "listing_base": archive.base, "dev_range": [DEV_START, DEV_END], "analysis_start": ANALYSIS_START,
            "holdout": f"{HOLDOUT_START} ve sonrası kilitli; bu raporda yer almaz",
            "exchange_info_available": exchange_info is not None,
            "archived_symbol_count": len(archived), "candidates": candidates, "excluded": excluded,
            "coins": coins, "errors": archive.errors, "requests": archive.http.requests}


def summarise_coin(entry: dict[str, Any], info: dict[str, Any] | None, dev_months: list[str]) -> dict[str, Any]:
    base = entry["kinds"].get("klines_15m") or {}
    present = sorted(base)
    first, last = (present[0], present[-1]) if present else (None, None)
    status = (info or {}).get("status")
    onboard = (info or {}).get("onboardDate")
    onboard_month = datetime.fromtimestamp(int(onboard) / 1000, timezone.utc).strftime("%Y-%m") if onboard else None
    delisted = bool(present) and (status not in {None, "TRADING"} or last < DEV_END)
    active = [m for m in dev_months if first and first <= m <= last] if present else []
    gaps: list[dict[str, str]] = []
    testable: list[str] = []
    for month in active:
        missing = []
        for kind, interval in REQUIRED_FOR_TEST:
            months_present = entry["kinds"].get(kind_label(kind, interval))
            if months_present is None:
                missing.append(f"{kind_label(kind, interval)} (liste hatası)")
            elif month not in months_present:
                missing.append(kind_label(kind, interval))
        partial = []
        if month == first:
            partial.append("listeleme ayı (kısmi olabilir)")
        if delisted and month == last:
            partial.append("delist ayı (kısmi olabilir)")
        for name in missing:
            gaps.append({"month": month, "symbol": entry["symbol"], "kind": name,
                         "reason": "arşivde yok" if "hata" not in name else "liste alınamadı",
                         "action": "test dışı (funding tahminle doldurulmaz)" if name.startswith("fundingRate") else "test dışı"})
        if not missing:
            testable.append(month)
        if partial:
            gaps.append({"month": month, "symbol": entry["symbol"], "kind": "-", "reason": ", ".join(partial),
                         "action": "bilgi; eksik sayılmaz"})
    funding = entry["kinds"].get("fundingRate") or {}
    funding_months = [m for m in active if m in funding]
    ranking_daily = entry["kinds"].get("klines_1d") or {}
    orderbook = {kind: (None if counts is None else sum(1 for m in active if (counts or {}).get(m)))
                 for kind, counts in entry["orderbook"].items()}
    size_active = {label: sum(v for m, v in (months_present or {}).items() if m in active)
                   for label, months_present in entry["kinds"].items()}
    return {"symbol": entry["symbol"], "exchange_status": status or "borsa listesinde yok",
            "onboard_month": onboard_month, "first_month": first, "last_month": last, "delisted": delisted,
            "active_months": len(active), "testable_months": testable, "funding_months": len(funding_months),
            "funding_coverage": round(len(funding_months) / len(active), 4) if active else None,
            "missing_funding_months": [m for m in active if m not in funding],
            "daily_ranking_months": len([m for m in active if m in ranking_daily]),
            "orderbook_months": orderbook, "bytes_by_kind": size_active, "gaps": gaps}


# --------------------------------------------------------------------------- raporlar

def storage_estimate(report: dict[str, Any]) -> dict[str, Any]:
    coins = report["coins"].values()
    all_bytes = sum(sum(v for k, v in c["bytes_by_kind"].items() if k != "klines_1d") for c in coins)
    daily_bytes = sum(c["bytes_by_kind"].get("klines_1d", 0) for c in coins)
    dev = months(DEV_START, DEV_END)
    # Evren hacimle seçilecek; burada yalnız boyut tahmini için her ay en büyük 15m dosyalı 100 coin alınır (yaklaşık).
    selected_pairs: set[tuple[str, str]] = set()
    by_month: dict[str, list[tuple[int, str]]] = {m: [] for m in dev}
    for coin in coins:
        for month in coin["testable_months"]:
            by_month[month].append((coin["bytes_by_kind"].get("klines_15m", 0), coin["symbol"]))
    for month, rows in by_month.items():
        for _, symbol in sorted(rows, reverse=True)[:UNIVERSE_SIZE]:
            selected_pairs.add((symbol, month))
    coin_months = len(selected_pairs)
    proxy_mb = coin_months * STAGE5_MB_PER_COIN_MONTH
    return {"all_candidates_mb": round(all_bytes / 1e6, 1), "ranking_1d_mb": round(daily_bytes / 1e6, 1),
            "universe_coin_months_estimate": coin_months, "universe_mb_estimate": round(proxy_mb, 1),
            "budget_gb": 3, "note": "Evren hacme göre seçilecek; buradaki 100'lük seçim yalnız boyut tahmini içindir."}


def cost_tables() -> dict[str, Any]:
    tables = {}
    for name, scenario in SCENARIOS.items():
        rows = []
        for low, high, base in LIQUIDITY_TIERS:
            for threshold, multiplier in VOLATILITY_STEPS:
                one_side = base * multiplier * scenario["model"]
                rows.append({"hacim_sirasi": f"{low}-{high}", "oynaklik_esigi": threshold, "spread_kayma_bp": round(one_side, 2),
                             "komisyon_bp": scenario["taker_bp"],
                             "gidis_donus_bp_buyukluk_haric": round(2 * (one_side + scenario["taker_bp"]), 2),
                             "delist_kapanis_bp": round(one_side * scenario["delist"] + scenario["taker_bp"], 2)})
        tables[name] = {"parameters": scenario, "rows": rows,
                        "size_impact": f"+{SIZE_IMPACT_BP_AT_1PCT} bp × kök(emir / 15m hacim ÷ %1) × {scenario['model']}"}
    return tables


def risks(report: dict[str, Any], storage: dict[str, Any]) -> list[dict[str, str]]:
    coins = list(report["coins"].values())
    delisted = [c["symbol"] for c in coins if c["delisted"]]
    funding_gaps = [c["symbol"] for c in coins if c["missing_funding_months"]]
    no_book = [c["symbol"] for c in coins if not any((c["orderbook_months"] or {}).values())]
    renamed = [c["symbol"] for c in coins if re.match(r"^(1000|1M|1000000)", c["symbol"])]
    items = [
        {"risk": "Funding eksikleri", "detail": f"{len(funding_gaps)} coinde en az bir ay funding yok; o aylar test dışı."},
        {"risk": "Delist edilmiş coinler", "detail": f"{len(delisted)} coin dönem içinde listeden çıkmış; zorla kapanış maliyeti kötümser hesaplanır."},
        {"risk": "Emir defteri verisi", "detail": f"{len(no_book)} coinde dönem içinde emir defteri dosyası yok; spread/kayma bu coinlerde tamamen simülasyon."},
        {"risk": "Ad değişikliği şüphesi", "detail": f"{len(renamed)} sembol 1000/1M önekli; aynı coinin eski adıyla eşleştirilmesi gerekebilir."},
        {"risk": "Kaldıraç dilimleri", "detail": "Tarihsel değerler arşivde yok; indirme aşamasında bugünkü değerler kullanılacak (yaklaşık)."},
        {"risk": "Liste hataları", "detail": f"{len(report['errors'])} liste isteği başarısız; bu coin/türler eksik görünebilir."},
        {"risk": "Arşiv düzeltmeleri", "detail": "Binance geçmiş dosyaları nadiren yeniden yayınlar; indirmede SHA-256 her dosyada doğrulanacak."},
        {"risk": "Disk ve süre", "detail": f"Tahmini evren verisi ~{storage['universe_mb_estimate']} MB; 3 GB bütçe."},
    ]
    if not report["exchange_info_available"]:
        items.append({"risk": "Sembol bilgisi yok", "detail": "exchangeInfo alınamadı; delist yalnız arşivdeki son aya göre belirlendi."})
    return items


def preflight(report: dict[str, Any], storage: dict[str, Any], output: Path) -> list[dict[str, str]]:
    coins = list(report["coins"].values())
    free_gb = shutil.disk_usage(output if output.exists() else output.parent).free / 1e9
    per_month = {m: sum(1 for c in coins if m in c["testable_months"]) for m in months(ANALYSIS_START, DEV_END)}
    thin = [m for m, n in per_month.items() if n < UNIVERSE_SIZE]
    total_active = sum(c["active_months"] for c in coins) or 1
    funding_cov = sum(c["funding_months"] for c in coins) / total_active
    checks = [
        ("Kilitli dönem", "OK", f"{HOLDOUT_START} ve sonrası bu raporda yok."),
        ("Liste erişimi", "OK" if report["listing_base"] else "HATA", f"Kullanılan adres: {report['listing_base']}"),
        ("Liste hataları", "OK" if not report["errors"] else "UYARI", f"{len(report['errors'])} hata"),
        ("Sembol bilgisi", "OK" if report["exchange_info_available"] else "UYARI", "exchangeInfo"),
        ("Aday sayısı", "OK" if len(coins) >= UNIVERSE_SIZE else "UYARI", f"{len(coins)} aday coin"),
        ("Aylık test edilebilir coin", "OK" if not thin else "UYARI",
         "her ay ≥ 100" if not thin else f"100'ün altında kalan aylar: {', '.join(thin)}"),
        ("Funding kapsamı", "OK" if funding_cov >= 0.98 else "UYARI", f"%{funding_cov * 100:.1f} coin-ay"),
        ("Disk (indirmede sunucuda tekrar kontrol edilir)", "OK" if free_gb >= MIN_FREE_GB else "UYARI",
         f"Bu makinede boş {free_gb:.1f} GB; gereken ≥ {MIN_FREE_GB} GB"),
        ("API anahtarı / emir", "OK", "Kullanılmadı; yalnız imzasız genel istekler."),
        ("Veri indirildi mi", "OK", "Hayır; yalnız dosya listesi okundu."),
    ]
    return [{"kontrol": a, "durum": b, "ayrinti": c} for a, b, c in checks]


def write_reports(output: Path, report: dict[str, Any]) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    storage = storage_estimate(report)
    costs = cost_tables()
    risk_items = risks(report, storage)
    checks = preflight(report, storage, output)
    written: list[Path] = []

    def put(name: str, text: str) -> None:
        path = output / name
        with path.open("x", encoding="utf-8", newline="") as stream:  # "x": var olan dosyanın üzerine yazmaz
            stream.write(text)
        written.append(path)

    full = {**report, "storage": storage, "cost_scenarios": costs, "risks": risk_items, "preflight": checks}
    put("discovery-report.json", json.dumps(full, ensure_ascii=False, indent=2))

    def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
        import io
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    coins = list(report["coins"].values())
    put("coin-availability.csv", table([{
        **c, "testable_month_count": len(c["testable_months"]), "missing_funding_count": len(c["missing_funding_months"]),
        "bookTicker_months": (c["orderbook_months"] or {}).get("bookTicker"),
        "bookDepth_months": (c["orderbook_months"] or {}).get("bookDepth"),
    } for c in coins], ["symbol", "exchange_status", "onboard_month", "first_month", "last_month", "delisted", "active_months",
                        "testable_month_count", "funding_months", "funding_coverage", "missing_funding_count",
                        "daily_ranking_months", "bookTicker_months", "bookDepth_months"]))
    gaps = sorted((g for c in coins for g in c["gaps"]), key=lambda g: (g["month"], g["symbol"], g["kind"]))
    put("monthly-gaps.csv", table(gaps, ["month", "symbol", "kind", "reason", "action"]))
    put("funding-coverage.csv", table([{"symbol": c["symbol"], "active_months": c["active_months"],
                                        "funding_months": c["funding_months"], "coverage": c["funding_coverage"],
                                        "missing_months": " ".join(c["missing_funding_months"])} for c in coins],
                                      ["symbol", "active_months", "funding_months", "coverage", "missing_months"]))
    for name, data in costs.items():
        put(f"cost-scenario-{name}.csv", table(data["rows"], list(data["rows"][0])))
    put("summary.md", summary_markdown(full))
    return written


def summary_markdown(full: dict[str, Any]) -> str:
    coins = list(full["coins"].values())
    s = full["storage"]
    lines = [
        "# Arşiv keşif raporu", "",
        f"Oluşturma: {full['generated_at']} · Araç: {full['version']} · Liste adresi: {full['listing_base']}", "",
        "**Bu rapor yalnız arşivdeki dosya listesine dayanır; hiçbir veri dosyası indirilmedi.** "
        f"Kilitli dönem ({HOLDOUT_START} ve sonrası) raporda yer almaz.", "",
        "## Coin bazında bulunabilirlik", "",
        f"- Arşivde klasörü olan sembol: {full['archived_symbol_count']}",
        f"- USDT perpetual aday: {len(coins)} · dışlanan: {len(full['excluded'])}",
        f"- Dönem içinde delist edilen: {sum(c['delisted'] for c in coins)}",
        f"- Ayrıntı: `coin-availability.csv`", "",
        "## Aylık eksik veri", "",
        "| Ay | Test edilebilir coin | Eksikli coin |", "| --- | --- | --- |",
    ]
    for month in months(ANALYSIS_START, DEV_END):
        ok = sum(1 for c in coins if month in c["testable_months"])
        bad = len({g["symbol"] for c in coins for g in c["gaps"] if g["month"] == month and g["action"] != "bilgi; eksik sayılmaz"})
        lines.append(f"| {month} | {ok} | {bad} |")
    lines += ["", "Ayrıntı: `monthly-gaps.csv` (eksik veri tahminle doldurulmaz; ilgili coin-ay test dışı).", "",
              "## Funding kapsamı", ""]
    total_active = sum(c["active_months"] for c in coins) or 1
    lines += [f"- Coin-ay kapsamı: %{sum(c['funding_months'] for c in coins) / total_active * 100:.1f}",
              f"- En az bir ayı eksik coin: {sum(1 for c in coins if c['missing_funding_months'])}",
              "- Eksik funding tahminle doldurulmaz, sıfır veya 8 saat varsayılmaz; o coin-ay test dışı. Ayrıntı: `funding-coverage.csv`", "",
              "## Depolama tahmini", "",
              f"- Tüm adaylar, tüm türler (geliştirme dönemi): {s['all_candidates_mb']} MB",
              f"- Sıralama için günlük mumlar: {s['ranking_1d_mb']} MB",
              f"- Tahmini evren (ayda ilk {UNIVERSE_SIZE}): {s['universe_coin_months_estimate']} coin-ay ≈ {s['universe_mb_estimate']} MB",
              f"- Ayrılacak bütçe: {s['budget_gb']} GB. {s['note']}", "",
              "## Maliyet senaryoları (simülasyon)", "",
              "Gerçek tarihsel bid-ask verisi bulunmayan her yerde spread ve kayma simülasyondur. "
              "Her senaryo ayrı dosyada: `cost-scenario-iyimser.csv`, `cost-scenario-temel.csv`, `cost-scenario-kotumser.csv`.", ""]
    for name, data in full["cost_scenarios"].items():
        p = data["parameters"]
        lines.append(f"- **{name}**: model ×{p['model']}, taker {p['taker_bp']} bp, delist kapanışı model ×{p['delist']}; büyüklük etkisi {data['size_impact']}")
    lines += ["", "## Veri indirme riskleri", ""] + [f"- **{r['risk']}:** {r['detail']}" for r in full["risks"]]
    lines += ["", "## İndirme öncesi son kontrol", "", "| Kontrol | Durum | Ayrıntı |", "| --- | --- | --- |"]
    lines += [f"| {c['kontrol']} | {c['durum']} | {c['ayrinti']} |" for c in full["preflight"]]
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- giriş

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arşiv keşfi: yalnız dosya listesi okunur, hiçbir veri indirilmez.")
    parser.add_argument("--output", type=Path, required=True, help="Yeni veya boş rapor klasörü")
    parser.add_argument("--rate", type=float, default=4.0, help="Saniyede en fazla istek (varsayılan 4)")
    parser.add_argument("--symbols", nargs="*", help="Yalnız bu semboller (deneme için)")
    parser.add_argument("--skip-orderbook", action="store_true", help="Emir defteri listesini atla")
    parser.add_argument("--no-exchange-info", action="store_true", help="Sembol bilgisini (exchangeInfo) okuma")
    args = parser.parse_args(argv)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("UYARI: root olarak çalışıyor; ayrı bir kullanıcıyla çalıştırman önerilir.", file=sys.stderr)
    output = check_output(args.output)
    http = Http(rate_per_second=args.rate)
    info = None
    if not args.no_exchange_info:
        try:
            info = json.loads(http.get(EXCHANGE_INFO))
        except Exception as exc:  # noqa: BLE001 - sembol bilgisi isteğe bağlı
            print(f"UYARI: exchangeInfo alınamadı: {exc}", file=sys.stderr)
    try:
        report = discover(Archive(http), exchange_info=info, symbols=args.symbols, orderbook=not args.skip_orderbook,
                          progress=lambda text: print(text, file=sys.stderr))
    except RuntimeError as exc:
        print(f"HATA: {exc}. İnternet bağlantısını ve arşiv adresine erişimi kontrol et; hiçbir dosya yazılmadı.",
              file=sys.stderr)
        return 2
    written = write_reports(output, report)
    print(json.dumps({"output": str(output), "files": [p.name for p in written], "requests": http.requests,
                      "candidates": len(report["candidates"]), "errors": len(report["errors"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
