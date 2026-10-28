#!/usr/bin/env python3
"""Kais 2 küçük ölçüm: yalnız GÜNLÜK mumları indirir, aylık ilk-100 evreni kurar, funding eksiğinin etkisini ölçer.

Bu ana veri indirme aracı DEĞİL. Amaç, tam indirmeden önce iki şeyi ölçmek:
  1. Her ayın evreni (önceki ayın ort. günlük USDT hacmine göre ilk 100) içinde kaç coin
     funding (veya başka veri) eksiği yüzünden test dışı kalıyor.
  2. Funding'i sondan itibaren kesilen coinlerde günlük mumlar o aylarda gerçekten işleniyor mu.

Güvenlik kuralları:
  * Yalnız klines/1d aylık zip + .CHECKSUM dosyaları; geliştirme dönemi (2024-05..2026-05).
    Kilitli dönem (2026-06 ve sonrası) ne listelenir ne indirilir.
  * Her dosya Binance'in .CHECKSUM değeriyle SHA-256 doğrulanır; uyuşmayan dosya kaydedilmez.
  * API anahtarı, imza, emir yok; yalnız izinli arşiv adreslerine imzasız GET.
  * Çıktı yeni/boş klasöre yazılır; repo içine, /opt/kaistrade, /var/lib/kaistrade, /etc altına yazılmaz.
  * Mevcut dosya üzerine yazılmaz ("xb"/"x"); yarıda kalırsa --resume yalnız bu aracın klasöründe çalışır.
  * Kais 1 koduna, canlı emir sistemine, canlı işlem kayıtlarına dokunmaz; yalnız standart kütüphane.
  * Eksik veri tahminle doldurulmaz.

Kullanım:
  python history_universe_probe.py --report <discovery-report.json> --plan-only
  python history_universe_probe.py --report <discovery-report.json> --output <yeni boş klasör>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import threading
import time
import urllib.error
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import history_discovery as hd

VERSION = "history-universe-probe-v1"
KIND, INTERVAL = "klines", "1d"
MARKER = "PROBE.json"
RATE_PER_SECOND = 8.0
WORKERS = 4
MIN_DAYS = 15                      # önceki ayda en az bu kadar gün verisi olmayan coin sıralamaya girmez
MAX_CSV_BYTES = 5_000_000          # tek aylık günlük mum dosyası bundan büyük olamaz
STABLE_BASES = ("USDC", "FDUSD", "TUSD", "USDP", "BUSD", "USDS", "USDE", "DAI")


# --------------------------------------------------------------------------- ağ

class PoliteHttp(hd.Http):
    """Keşif aracının Http'si + çok iş parçacığı için ortak hız sınırı (toplam saniyede RATE istek)."""

    def __init__(self, rate: float = RATE_PER_SECOND):
        super().__init__(rate_per_second=1000.0)
        self.rate = rate
        self.count = 0
        self._lock = threading.Lock()
        self._next = 0.0

    def get(self, url: str) -> bytes:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + 1.0 / self.rate
            self.count += 1
        if slot > now:
            time.sleep(slot - now)
        return super().get(url)


# --------------------------------------------------------------------------- girdi

def load_report(path: Path) -> dict[str, Any]:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not str(report.get("version", "")).startswith("history-discovery"):
        raise ValueError("Bu bir keşif raporu (discovery-report.json) değil.")
    if not report.get("candidates") or not isinstance(report.get("coins"), dict):
        raise ValueError("Keşif raporunda aday coin listesi yok.")
    return report


def prev_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year - 1:04d}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"


def allowed_month(month: str) -> bool:
    return hd.DEV_START <= month <= hd.DEV_END and not hd.locked(month)


def check_output(path: Path, *, resume: bool) -> Path:
    try:
        return hd.check_output(path)
    except FileExistsError:
        root = Path(path).expanduser().resolve()
        if resume and (root / MARKER).is_file():
            return root  # yasaklı klasör ve git denetimleri check_output'ta zaten geçti
        raise


# --------------------------------------------------------------------------- plan ve indirme

def plan_files(archive: hd.Archive, symbols: list[str], progress: Callable[[str], None] = lambda _: None) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols, 1):
        if index % 100 == 0:
            progress(f"liste {index}/{len(symbols)}")
        prefix = f"{hd.MONTHLY}{KIND}/{symbol}/{INTERVAL}/"
        keys, _ = archive.list(prefix)  # kilitli aylar listeden zaten düşer
        for item in keys:
            if not item["key"].endswith(".zip"):
                continue
            found = hd.MONTH_RE.search(item["key"])
            if found and allowed_month(found.group(1)):
                plan.append({"symbol": symbol, "month": found.group(1), "key": item["key"], "size": item["size"],
                             "label": f"{KIND}_{INTERVAL}"})
    return plan


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_one(http: Any, base: str, item: dict[str, Any], root: Path) -> dict[str, Any]:
    month = item["month"]
    if not allowed_month(month):
        raise ValueError(f"İzinsiz ay: {month}")  # kilitli dönem indirilemez
    target = root / "raw" / item.get("label", f"{KIND}_{INTERVAL}") / item["symbol"] / Path(item["key"]).name
    log = {"symbol": item["symbol"], "month": month, "bytes": 0, "sha256": "", "status": ""}
    sidecar = target.with_name(target.name + ".sha256")
    if target.is_file() and sidecar.is_file():
        data = target.read_bytes()
        if sha256_of(data) == sidecar.read_text(encoding="utf-8").strip():
            log.update(bytes=len(data), sha256=sha256_of(data), status="mevcut (doğrulandı)")
            return log
        log["status"] = "yerel dosya bozuk; elle silip --resume ile tekrar çalıştırın"
        return log
    url = f"{base}/{item['key']}"
    try:
        data = http.get(url)
        expected = http.get(url + ".CHECKSUM").decode("utf-8", "replace").split()[0].lower()
    except (urllib.error.URLError, TimeoutError, ValueError, IndexError) as exc:
        log["status"] = f"indirilemedi: {exc}"
        return log
    digest = sha256_of(data)
    if digest != expected:
        log.update(bytes=len(data), sha256=digest, status="SHA-256 UYUŞMADI; dosya kaydedilmedi")
        return log
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as handle:
        handle.write(data)
    with sidecar.open("x", encoding="utf-8") as handle:
        handle.write(digest + "\n")
    log.update(bytes=len(data), sha256=digest, status="indirildi (doğrulandı)")
    return log


def download_all(http: Any, base: str, plan: list[dict[str, Any]], root: Path,
                 progress: Callable[[str], None] = lambda _: None) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for index, log in enumerate(pool.map(lambda item: fetch_one(http, base, item, root), plan), 1):
            logs.append(log)
            if index % 250 == 0 or index == len(plan):
                progress(f"indirme {index}/{len(plan)}")
    return logs


# --------------------------------------------------------------------------- günlük mum çözümleme

def parse_daily_zip(data: bytes) -> list[tuple[str, float]]:
    """[(gün, USDT hacmi)] — zip içindeki tek csv; başlık satırı varsa atlanır."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv") or archive.getinfo(names[0]).file_size > MAX_CSV_BYTES:
            raise ValueError("Beklenmeyen zip içeriği")
        text = archive.read(names[0]).decode("utf-8", "replace")
    rows: list[tuple[str, float]] = []
    for row in csv.reader(text.splitlines()):
        if len(row) < 8 or not row[0].strip().isdigit():
            continue
        stamp = int(row[0])
        if stamp >= 10 ** 15:  # mikrosaniye
            stamp //= 1000
        day = datetime.fromtimestamp(stamp / 1000, timezone.utc).strftime("%Y-%m-%d")
        rows.append((day, float(row[7])))
    return rows


def month_stats(rows: list[tuple[str, float]], month: str) -> dict[str, Any] | None:
    mine = [(day, volume) for day, volume in rows if day.startswith(month)]
    if not mine:
        return None
    return {"days": len(mine), "avg_daily_usdt": sum(v for _, v in mine) / len(mine),
            "zero_days": sum(1 for _, v in mine if v <= 0), "last_day": max(day for day, _ in mine)}


def load_stats(root: Path, plan: list[dict[str, Any]]) -> tuple[dict[str, dict[str, dict[str, Any]]], list[dict[str, str]]]:
    stats: dict[str, dict[str, dict[str, Any]]] = {}
    problems: list[dict[str, str]] = []
    for item in plan:
        path = root / "raw" / item.get("label", f"{KIND}_{INTERVAL}") / item["symbol"] / Path(item["key"]).name
        if not path.is_file():
            problems.append({"symbol": item["symbol"], "month": item["month"], "problem": "dosya yok (indirme başarısız)"})
            continue
        try:
            result = month_stats(parse_daily_zip(path.read_bytes()), item["month"])
        except (ValueError, zipfile.BadZipFile) as exc:
            problems.append({"symbol": item["symbol"], "month": item["month"], "problem": f"çözümlenemedi: {exc}"})
            continue
        if result is None:
            problems.append({"symbol": item["symbol"], "month": item["month"], "problem": "dosyada o aya ait satır yok"})
            continue
        stats.setdefault(item["symbol"], {})[item["month"]] = result
    return stats, problems


# --------------------------------------------------------------------------- evren ve etki

def build_universe(stats: dict[str, dict[str, dict[str, Any]]], size: int = hd.UNIVERSE_SIZE) -> dict[str, list[tuple[str, float]]]:
    """Ay M evreni = M-1 ayının ort. günlük USDT hacmine göre ilk `size`; coin M ayında da işlem görmeli (listede olmalı)."""
    universe: dict[str, list[tuple[str, float]]] = {}
    for month in hd.months(hd.ANALYSIS_START, hd.DEV_END):
        before = prev_month(month)
        ranked = [(symbol, by[before]["avg_daily_usdt"]) for symbol, by in stats.items()
                  if before in by and by[before]["days"] >= MIN_DAYS and month in by]
        ranked.sort(key=lambda row: (-row[1], row[0]))
        universe[month] = ranked[:size]
    return universe


def impact(universe: dict[str, list[tuple[str, float]]], coins: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    months_rows: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    for month, ranked in universe.items():
        total = sum(volume for _, volume in ranked) or 1.0
        counts = {"ok": 0, "funding": 0, "other": 0}
        lost = 0.0
        delisted = 0
        for rank, (symbol, volume) in enumerate(ranked, 1):
            coin = coins.get(symbol) or {}
            testable = month in (coin.get("testable_months") or [])
            if testable:
                reason = ""
                counts["ok"] += 1
            elif month in (coin.get("missing_funding_months") or []):
                reason = "funding yok"
                counts["funding"] += 1
                lost += volume
            else:
                reason = "başka veri eksik"
                counts["other"] += 1
                lost += volume
            delisted += 1 if coin.get("delisted") else 0
            detail.append({"month": month, "rank": rank, "symbol": symbol, "avg_daily_usdt_prev_month": round(volume),
                           "testable": "evet" if testable else "hayır", "reason": reason,
                           "delisted": "evet" if coin.get("delisted") else "hayır"})
        months_rows.append({"month": month, "universe": len(ranked), "testable": counts["ok"],
                            "funding_excluded": counts["funding"], "other_excluded": counts["other"],
                            "excluded_volume_share_pct": round(100 * lost / total, 2), "delisted_in_universe": delisted})
    return months_rows, detail


def funding_gap_check(coins: dict[str, Any], stats: dict[str, dict[str, dict[str, Any]]],
                      detail: list[dict[str, Any]]) -> list[dict[str, Any]]:
    in_universe: dict[str, list[str]] = {}
    for row in detail:
        in_universe.setdefault(row["symbol"], []).append(row["month"])
    rows: list[dict[str, Any]] = []
    for symbol in sorted(coins):
        coin = coins[symbol]
        missing = coin.get("missing_funding_months") or []
        if not missing:
            continue
        by = stats.get(symbol, {})
        seen = [by[m] for m in missing if m in by]
        days = sum(s["days"] for s in seen)
        zero = sum(s["zero_days"] for s in seen)
        active = hd.months(coin.get("first_month") or hd.DEV_START, coin.get("last_month") or hd.DEV_END)
        tail = bool(active) and missing[-1] == active[-1] and missing == [m for m in active if m >= missing[0]]
        avg = sum(s["avg_daily_usdt"] * s["days"] for s in seen) / days if days else 0.0
        if not seen:
            verdict = "günlük mum yok"
        elif zero / days > 0.5 or avg < 1000:
            verdict = "mumlar sönük (hacim yok/çok az): fiilen işlem görmüyor olabilir"
        else:
            verdict = "mumlar canlı (hacim var) ama funding dosyası yok"
        months_in_top = [m for m in in_universe.get(symbol, [])]
        rows.append({"symbol": symbol, "delisted_flag": "evet" if coin.get("delisted") else "hayır",
                     "first_missing_month": missing[0], "missing_month_count": len(missing),
                     "continuous_until_end": "evet" if tail else "hayır",
                     "daily_data_days_in_missing_months": days, "zero_volume_days": zero,
                     "avg_daily_usdt_in_missing_months": round(avg), "verdict": verdict,
                     "months_in_top100": len(months_in_top),
                     "top100_months_with_funding_missing": len([m for m in months_in_top if m in missing])})
    return rows


def stable_symbols(universe: dict[str, list[tuple[str, float]]]) -> list[str]:
    names = {symbol for ranked in universe.values() for symbol, _ in ranked}
    return sorted(s for s in names if s.removesuffix("USDT") in STABLE_BASES)


# --------------------------------------------------------------------------- rapor

def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0]) if rows else ["empty"])
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summary_markdown(result: dict[str, Any]) -> str:
    months_rows, gaps, logs = result["impact"], result["funding_gap"], result["logs"]
    total_slots = sum(r["universe"] for r in months_rows)
    ok = sum(r["testable"] for r in months_rows)
    fund = sum(r["funding_excluded"] for r in months_rows)
    other = sum(r["other_excluded"] for r in months_rows)
    worst = min(months_rows, key=lambda r: r["testable"] / max(r["universe"], 1)) if months_rows else None
    bad = [log for log in logs if not log["status"].startswith(("indirildi", "mevcut"))]
    lines = [
        "# Günlük mum ölçümü: evren ve funding etkisi", "",
        f"Oluşturma: {result['generated_at']} · Araç: {VERSION}", "",
        "**Bu rapor yalnız günlük mumlara dayanır (klines/1d). Kilitli dönem (2026-06 ve sonrası) indirilmedi ve raporda yok.**", "",
        "## İndirme", "",
        f"- Planlanan dosya: {len(logs)} · başarılı: {len(logs) - len(bad)} · başarısız veya uyuşmayan: {len(bad)}",
        f"- Toplam boyut: {sum(log['bytes'] for log in logs) / 1e6:.1f} MB · SHA-256 her dosyada .CHECKSUM ile doğrulandı",
        f"- Çözümlenemeyen/eksik dosya: {len(result['problems'])}", "",
        "## Evren (ayın önceki ayına göre ilk 100) ve test edilebilirlik", "",
        f"- Analiz ayı: {len(months_rows)} · toplam evren yuvası (coin-ay): {total_slots}",
        f"- Tam test edilebilir: {ok} (%{100 * ok / max(total_slots, 1):.1f})",
        f"- Funding yüzünden dışarıda: {fund} (%{100 * fund / max(total_slots, 1):.1f})",
        f"- Başka veri eksiği yüzünden dışarıda: {other} (%{100 * other / max(total_slots, 1):.1f})",
    ]
    if worst:
        lines.append(f"- En kötü ay: {worst['month']} → {worst['testable']}/{worst['universe']} test edilebilir, "
                     f"funding dışı kalan hacim payı %{worst['excluded_volume_share_pct']}")
    lines += ["", "| Ay | Evren | Test edilebilir | Funding yok | Başka eksik | Dışarıda kalan hacim % | Delist coin |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in months_rows:
        lines.append(f"| {r['month']} | {r['universe']} | {r['testable']} | {r['funding_excluded']} | {r['other_excluded']} | "
                     f"{r['excluded_volume_share_pct']} | {r['delisted_in_universe']} |")
    live = [g for g in gaps if g["verdict"].startswith("mumlar canlı")]
    dull = [g for g in gaps if g["verdict"].startswith("mumlar sönük")]
    lines += ["", "## Funding'i kesilen coinler", "",
              f"- Funding eksiği olan coin: {len(gaps)} · sondan itibaren kesintisiz eksik: {sum(1 for g in gaps if g['continuous_until_end'] == 'evet')}",
              f"- Eksik aylarda günlük mumları canlı olan: {len(live)} · sönük olan: {len(dull)}",
              f"- Bu coinlerden en az bir ay ilk 100'e girenler: {sum(1 for g in gaps if g['months_in_top100'])} · "
              f"ilk 100'deyken funding'i eksik olan coin-ay: {sum(g['top100_months_with_funding_missing'] for g in gaps)}", "",
              "En çok ilk 100'de bulunan funding-eksikli coinler:", "",
              "| Coin | Delist? | İlk eksik ay | Eksik ay | Eksik aylarda ort. günlük hacim (USDT) | İlk 100'de ay | Karar |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for g in sorted(gaps, key=lambda g: -g["top100_months_with_funding_missing"])[:15]:
        lines.append(f"| {g['symbol']} | {g['delisted_flag']} | {g['first_missing_month']} | {g['missing_month_count']} | "
                     f"{g['avg_daily_usdt_in_missing_months']} | {g['months_in_top100']} | {g['verdict']} |")
    stable = result["stable"]
    lines += ["", "## Dikkat", "",
              f"- Stabil/pariteli semboller sıralamaya girmiş olabilir: {', '.join(stable) if stable else 'yok'} (çıkarılıp çıkarılmayacağı sizin kararınız).",
              "- Sıralamada coin, önceki ayda en az 15 gün veriye sahip olmalı ve ilgili ayda da işlem görmeli.",
              "- Eksik funding tahminle doldurulmadı; ilgili coin-ay test dışı sayıldı.", "",
              "Ayrıntı dosyaları: `universe-by-month.csv`, `universe-impact.csv`, `funding-gap-check.csv`, `download-log.csv`.", ""]
    return "\n".join(lines)


def new_report_folder(root: Path, prefix: str) -> Path:
    """Yeni (hiç var olmayan) rapor klasörü; aynı saniyede tekrar çalışsa da mevcut klasör ezilmez."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for number in range(100):
        folder = root / (f"{prefix}-{stamp}" + (f"-{number}" if number else ""))
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            continue
    raise FileExistsError("Rapor klasörü oluşturulamadı")


def write_reports(root: Path, result: dict[str, Any]) -> list[Path]:
    folder = new_report_folder(root, "reports")
    write_csv(folder / "universe-by-month.csv", result["detail"],
              ["month", "rank", "symbol", "avg_daily_usdt_prev_month", "testable", "reason", "delisted"])
    write_csv(folder / "universe-impact.csv", result["impact"])
    write_csv(folder / "funding-gap-check.csv", result["funding_gap"],
              ["symbol", "delisted_flag", "first_missing_month", "missing_month_count", "continuous_until_end",
               "daily_data_days_in_missing_months", "zero_volume_days", "avg_daily_usdt_in_missing_months", "verdict",
               "months_in_top100", "top100_months_with_funding_missing"])
    write_csv(folder / "download-log.csv", result["logs"], ["symbol", "month", "bytes", "sha256", "status"])
    with (folder / "summary.md").open("x", encoding="utf-8") as handle:
        handle.write(summary_markdown(result))
    return sorted(folder.iterdir())


def analyse(root: Path, plan: list[dict[str, Any]], logs: list[dict[str, Any]], coins: dict[str, Any]) -> dict[str, Any]:
    stats, problems = load_stats(root, plan)
    universe = build_universe(stats)
    months_rows, detail = impact(universe, coins)
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "logs": logs, "problems": problems,
            "impact": months_rows, "detail": detail, "funding_gap": funding_gap_check(coins, stats, detail),
            "stable": stable_symbols(universe)}


# --------------------------------------------------------------------------- giriş

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", required=True, type=Path, help="keşif aracının discovery-report.json dosyası (yalnız okunur)")
    parser.add_argument("--output", type=Path, help="yeni veya boş klasör (--plan-only ile gerekmez)")
    parser.add_argument("--plan-only", action="store_true", help="yalnız dosya listesini okur; hiçbir şey indirmez veya yazmaz")
    parser.add_argument("--resume", action="store_true", help="yarım kalan bu araç klasöründe devam eder")
    parser.add_argument("--rate", type=float, default=RATE_PER_SECOND, help="toplam saniyedeki istek sınırı")
    parser.add_argument("--symbols", nargs="*", help="deneme için yalnız bu semboller")
    args = parser.parse_args(argv)

    report = load_report(args.report)
    symbols = [s for s in report["candidates"] if not args.symbols or s in set(args.symbols)]
    root: Path | None = None
    if not args.plan_only:
        if not args.output:
            parser.error("--output gerekli (veya --plan-only kullanın)")
        root = check_output(args.output, resume=args.resume)

    http = PoliteHttp(rate=min(max(args.rate, 0.5), 12.0))
    archive = hd.Archive(http)
    try:
        plan = plan_files(archive, symbols, progress=lambda m: print(m, file=sys.stderr, flush=True))
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    size = sum(item["size"] for item in plan)
    print(json.dumps({"planned_files": len(plan), "planned_mb": round(size / 1e6, 1), "symbols": len(symbols),
                      "first_month": min((i["month"] for i in plan), default=None),
                      "last_month": max((i["month"] for i in plan), default=None)}, ensure_ascii=False))
    if args.plan_only:
        return 0

    assert root is not None
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        with marker.open("x", encoding="utf-8") as handle:
            json.dump({"tool": VERSION, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "kind": f"{KIND}/{INTERVAL}", "range": [hd.DEV_START, hd.DEV_END]}, handle)
    logs = download_all(http, archive.base or hd.LISTING_BASES[0], plan, root, progress=lambda m: print(m, file=sys.stderr, flush=True))
    result = analyse(root, plan, logs, report["coins"])
    files = write_reports(root, result)
    failed = [log for log in logs if not log["status"].startswith(("indirildi", "mevcut"))]
    print(json.dumps({"output": str(root), "reports": [p.name for p in files], "files": len(logs), "failed": len(failed),
                      "requests": http.count}, ensure_ascii=False))
    return 0 if not failed else 3


if __name__ == "__main__":
    raise SystemExit(main())
