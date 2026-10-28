#!/usr/bin/env python3
"""Kais 2 ana tarihsel veri indirme (iki aşamalı). Yalnız geliştirme dönemi (2024-05..2026-05).

Aşama 1  Günlük mumlar (~20 MB) → aylık ilk-100 evren + funding eksiğinin etkisi raporu. Burada DURULUR.
Aşama 2  Yalnız sen "devam" dedikten sonra, `--confirm` ile: evrene giren coinlerin 15m mumları ve funding'i
         (1h/4h 15m'den hesaplanır; mark fiyatı indirilmez). Eski açıklama: 15m/1h/4h mumları,
         mark price 15m ve funding verisi. Her dosya SHA-256 ile doğrulanır, indirme sonrası içerik kontrolü yapılır.

Güvenlik kuralları (ikisi için de):
  * Kilitli dönem (2026-06 ve sonrası) ne listelenir ne indirilir; kodda ayrıca reddedilir.
  * Eksik veri tahminle doldurulmaz; interpolasyon yoktur. Eksikler raporlanır, ilgili coin-ay test dışı sayılır.
  * API anahtarı, imza, emir yok; yalnız izinli arşiv adreslerine imzasız GET. Standart kütüphane.
  * Yeni/boş klasöre yazar; repo içine, /opt/kaistrade, /var/lib/kaistrade, /etc altına yazmaz; dosya ezmez.
  * Kais 1 koduna, canlı emir sistemine, canlı işlem kayıtlarına dokunmaz.
  * Yarıda kalırsa aynı komut + --resume (yalnız bu aracın klasöründe).

Kullanım:
  python history_download.py stage1 --report <discovery-report.json> --output <yeni boş klasör>
  python history_download.py stage2 --report <discovery-report.json> --output <aynı klasör>            # yalnız plan
  python history_download.py stage2 --report <discovery-report.json> --output <aynı klasör> --confirm   # indirir
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import history_discovery as hd
import history_universe_probe as probe

VERSION = "history-download-v1"
STAGE2_MARKER = "STAGE2.json"
# Küçültülmüş indirme: yalnız 15m mum + funding. 1h/4h mumlar 15m'den hesaplanır (indirilmez).
KINDS: tuple[tuple[str, str], ...] = (("klines", "15m"), ("fundingRate", ""))
STEP_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400}
MAX_CSV_BYTES = 40_000_000


def label_of(kind: str, interval: str) -> str:
    return hd.kind_label(kind, interval)


REQUIRED_LABELS = tuple(label_of(k, i) for k, i in KINDS)


# --------------------------------------------------------------------------- evren (aşama 1 çıktısı)

def latest_reports(root: Path) -> Path:
    folders = sorted(p for p in root.glob("reports-*") if (p / "universe-by-month.csv").is_file())
    if not folders:
        raise FileNotFoundError("Önce aşama 1 çalışmalı (universe-by-month.csv bulunamadı).")
    return folders[-1]


def load_universe(root: Path) -> list[dict[str, str]]:
    path = latest_reports(root) / "universe-by-month.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Evren boş; aşama 1 raporunu kontrol edin.")
    for row in rows:
        month = row["month"]
        if hd.locked(month) or not (hd.ANALYSIS_START <= month <= hd.DEV_END):
            raise ValueError(f"Evren dosyasında izinsiz ay: {month}")
    return rows


# --------------------------------------------------------------------------- aşama 2 planı

def plan_stage2(archive: hd.Archive, symbols: list[str], progress=lambda _: None) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols, 1):
        if index % 25 == 0:
            progress(f"liste {index}/{len(symbols)}")
        for kind, interval in KINDS:
            prefix = f"{hd.MONTHLY}{kind}/{symbol}/" + (f"{interval}/" if interval else "")
            keys, _ = archive.list(prefix)  # kilitli aylar listeden zaten düşer
            for item in keys:
                if not item["key"].endswith(".zip"):
                    continue
                found = hd.MONTH_RE.search(item["key"])
                if found and probe.allowed_month(found.group(1)):
                    plan.append({"symbol": symbol, "month": found.group(1), "key": item["key"], "size": item["size"],
                                 "label": label_of(kind, interval)})
    return plan


# --------------------------------------------------------------------------- içerik kontrolü (tahmin yok)

def inspect_zip(data: bytes, label: str) -> dict[str, Any]:
    """Satır sayısı, yinelenen ve ARADAKİ eksik mum sayısı. Hiçbir şey doldurulmaz."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != 1 or not names[0].endswith(".csv") or archive.getinfo(names[0]).file_size > MAX_CSV_BYTES:
            raise ValueError("Beklenmeyen zip içeriği")
        text = archive.read(names[0]).decode("utf-8", "replace")
    stamps: list[int] = []
    for row in csv.reader(text.splitlines()):
        if not row or not row[0].strip().isdigit():
            continue
        stamp = int(row[0])
        stamps.append(stamp // 1000 if stamp >= 10 ** 15 else stamp)
    if not stamps:
        raise ValueError("Dosyada veri satırı yok")
    unique = sorted(set(stamps))
    result = {"rows": len(stamps), "duplicates": len(stamps) - len(unique), "sorted": stamps == sorted(stamps),
              "internal_missing": 0}
    interval = label.split("_")[-1]
    if label != "fundingRate" and interval in STEP_SECONDS:
        step = STEP_SECONDS[interval] * 1000
        result["internal_missing"] = max(0, (unique[-1] - unique[0]) // step + 1 - len(unique))
    return result


def validate(root: Path, plan: list[dict[str, Any]], logs: list[dict[str, Any]],
             universe: list[dict[str, str]]) -> dict[str, Any]:
    ok_status = ("indirildi", "mevcut")
    failed = {(item["symbol"], item["month"], item["label"]) for item, log in zip(plan, logs)
              if not log["status"].startswith(ok_status)}
    found: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    problems: list[dict[str, str]] = []
    for item in plan:
        key = (item["symbol"], item["month"], item["label"])
        if key in failed:
            continue
        path = root / "raw" / item["label"] / item["symbol"] / Path(item["key"]).name
        try:
            info = inspect_zip(path.read_bytes(), item["label"])
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            problems.append({"symbol": item["symbol"], "month": item["month"], "kind": item["label"], "problem": str(exc)})
            continue
        found.setdefault((item["symbol"], item["month"]), {})[item["label"]] = info
    rows: list[dict[str, Any]] = []
    for entry in universe:
        symbol, month = entry["symbol"], entry["month"]
        have = found.get((symbol, month), {})
        missing = [name for name in REQUIRED_LABELS if name not in have]
        gaps = sum(info["internal_missing"] for info in have.values())
        dups = sum(info["duplicates"] for info in have.values())
        unsorted = [name for name, info in have.items() if not info["sorted"]]
        if missing:
            status = "eksik: " + " ".join(missing)
        elif gaps or dups or unsorted:
            status = "tam (mum boşluğu/yineleme var)"
        else:
            status = "tam"
        rows.append({"month": month, "symbol": symbol, "status": status, "missing_kinds": " ".join(missing),
                     "candle_gap_total": gaps, "duplicate_rows": dups,
                     "unsorted_kinds": " ".join(unsorted)})
    return {"coin_months": rows, "problems": problems, "failed_downloads": len(failed)}


def summary_markdown(result: dict[str, Any]) -> str:
    rows, logs = result["validation"]["coin_months"], result["logs"]
    total = len(rows)
    complete = [r for r in rows if r["status"].startswith("tam")]
    clean = [r for r in rows if r["status"] == "tam"]
    gappy = [r for r in rows if r["status"].startswith("tam (")]
    missing_funding = [r for r in rows if "fundingRate" in r["missing_kinds"].split()]
    bad = [log for log in logs if not log["status"].startswith(("indirildi", "mevcut"))]
    by_month: dict[str, dict[str, int]] = {}
    for r in rows:
        m = by_month.setdefault(r["month"], {"n": 0, "ok": 0, "gap": 0})
        m["n"] += 1
        m["ok"] += 1 if r["status"].startswith("tam") else 0
        m["gap"] += 1 if r["status"].startswith("tam (") else 0
    lines = ["# Ana veri indirme: doğrulama raporu", "", f"Oluşturma: {result['generated_at']} · Araç: {VERSION}", "",
             "**Kilitli dönem (2026-06 ve sonrası) indirilmedi.** Eksik veri tahminle doldurulmadı, interpolasyon yok.", "",
             "## İndirme", "",
             f"- Dosya: {len(logs)} · başarılı: {len(logs) - len(bad)} · başarısız veya SHA-256 uyuşmayan: {len(bad)}",
             f"- Boyut: {sum(log['bytes'] for log in logs) / 1e6:.1f} MB",
             f"- İçeriği çözümlenemeyen dosya: {len(result['validation']['problems'])}", "",
             "## Evren coin-ayları", "",
             f"- Toplam coin-ay (aşama 1 evreni): {total}",
             f"- Tam (5 tür de var): {len(complete)} (%{100 * len(complete) / max(total, 1):.1f}) · "
             f"temiz: {len(clean)} · mum boşluğu/yineleme olan: {len(gappy)}",
             f"- Eksik: {total - len(complete)} (%{100 * (total - len(complete)) / max(total, 1):.1f}) · "
             f"bunların funding eksiği olanı: {len(missing_funding)}", "",
             "| Ay | Evren | Tam | Boşluklu | Eksik |", "| --- | --- | --- | --- | --- |"]
    for month in sorted(by_month):
        m = by_month[month]
        lines.append(f"| {month} | {m['n']} | {m['ok']} | {m['gap']} | {m['n'] - m['ok']} |")
    lines += ["", "Eksik coin-aylar test dışıdır ve burada raporlanır. Ayrıntı: `validation-coin-month.csv`, `download-log.csv`.", ""]
    return "\n".join(lines)


def write_reports(root: Path, result: dict[str, Any]) -> list[Path]:
    folder = probe.new_report_folder(root, "reports2")
    probe.write_csv(folder / "validation-coin-month.csv", result["validation"]["coin_months"],
                    ["month", "symbol", "status", "missing_kinds", "candle_gap_total", "duplicate_rows", "unsorted_kinds"])
    probe.write_csv(folder / "download-log.csv", result["logs"], ["symbol", "month", "bytes", "sha256", "status"])
    probe.write_csv(folder / "content-problems.csv", result["validation"]["problems"],
                    ["symbol", "month", "kind", "problem"])
    with (folder / "validation-summary.md").open("x", encoding="utf-8") as handle:
        handle.write(summary_markdown(result))
    return sorted(folder.iterdir())


# --------------------------------------------------------------------------- aşamalar

def run_stage1(args: argparse.Namespace) -> int:
    argv = ["--report", str(args.report), "--output", str(args.output), "--rate", str(args.rate)]
    if args.resume:
        argv.append("--resume")
    if args.symbols:
        argv += ["--symbols", *args.symbols]
    return probe.main(argv)


def run_stage2(args: argparse.Namespace) -> int:
    if args.report is not None:
        probe.load_report(args.report)  # biçim denetimi
    root = probe.check_output(args.output, resume=True)
    if not (root / probe.MARKER).is_file():
        print(json.dumps({"error": "Bu klasör aşama 1'den gelmiyor (PROBE.json yok)."}, ensure_ascii=False), file=sys.stderr)
        return 2
    universe = load_universe(root)
    symbols = sorted({row["symbol"] for row in universe})
    http = probe.PoliteHttp(rate=min(max(args.rate, 0.5), 12.0))
    archive = hd.Archive(http)
    try:
        plan = plan_stage2(archive, symbols, progress=lambda m: print(m, file=sys.stderr, flush=True))
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    size = sum(item["size"] for item in plan)
    free = shutil.disk_usage(root).free
    print(json.dumps({"symbols": len(symbols), "planned_files": len(plan), "planned_mb": round(size / 1e6, 1),
                      "free_gb": round(free / 1e9, 1), "confirm": bool(args.confirm)}, ensure_ascii=False))
    if free < size + hd.MIN_FREE_GB * 10 ** 9:
        print(json.dumps({"error": f"Boş alan yetersiz (en az {hd.MIN_FREE_GB} GB artı veri boyutu gerekir)."}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    if not args.confirm:
        print("Yalnız plan gösterildi. İndirmek için aynı komuta --confirm ekleyin.")
        return 0
    marker = root / STAGE2_MARKER
    if not marker.exists():
        with marker.open("x", encoding="utf-8") as handle:
            json.dump({"tool": VERSION, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "kinds": [label_of(k, i) for k, i in KINDS], "range": [hd.DEV_START, hd.DEV_END]}, handle)
    logs = probe.download_all(http, archive.base or hd.LISTING_BASES[0], plan, root,
                              progress=lambda m: print(m, file=sys.stderr, flush=True))
    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "logs": logs,
              "validation": validate(root, plan, logs, universe)}
    files = write_reports(root, result)
    failed = [log for log in logs if not log["status"].startswith(("indirildi", "mevcut"))]
    print(json.dumps({"output": str(root), "reports": [p.name for p in files], "files": len(logs), "failed": len(failed),
                      "requests": http.count}, ensure_ascii=False))
    return 0 if not failed else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="stage", required=True)
    for name in ("stage1", "stage2"):
        p = sub.add_parser(name)
        p.add_argument("--report", required=name == "stage1", type=Path,
                       help="keşif aracının discovery-report.json dosyası (yalnız okunur; aşama 2'de isteğe bağlı)")
        p.add_argument("--output", required=True, type=Path)
        p.add_argument("--rate", type=float, default=probe.RATE_PER_SECOND)
        p.add_argument("--resume", action="store_true")
        if name == "stage1":
            p.add_argument("--symbols", nargs="*", help="deneme için yalnız bu semboller")
        else:
            p.add_argument("--confirm", action="store_true", help="indirmeyi başlatır (yoksa yalnız plan gösterilir)")
    args = parser.parse_args(argv)
    return run_stage1(args) if args.stage == "stage1" else run_stage2(args)


if __name__ == "__main__":
    raise SystemExit(main())
