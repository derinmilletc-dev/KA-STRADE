"""C18 veri indirici: funding, metrics (OI + long/short), likidasyon. Yalnız standart kütüphane.

Ön kayıt: backend/studies/c18-turev-veri-onkayit.md (SHA-256 .sha256 dosyasında).
Kurallar: yalnız TRAIN/VALID pencereleri (ısınma dahil); 2026-06-01 ve sonrası REDDEDİLİR (OOS tüketildi);
çıktı klasörü yeni ve boş olmalı; üzerine yazma yok; eksik veri doldurulmaz; her ZIP .CHECKSUM ile doğrulanır.

Kullanım:
  python c18_download.py --out C:\\Users\\ahmtt\\kaistrade-data\\c18 --dry-run
  python c18_download.py --out C:\\Users\\ahmtt\\kaistrade-data\\c18 --probe      (likidasyon var mı, 3 örnek gün)
  python c18_download.py --out C:\\Users\\ahmtt\\kaistrade-data\\c18               (indir + doğrula)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = "https://data.binance.vision/data/futures/um/"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
# (başlangıç, bitiş dahil): ısınma (30 gün z-skoru için) + TRAIN / VALID gerçek veri aralıkları
WINDOWS = ((date(2025, 3, 1), date(2025, 9, 30)), (date(2026, 3, 1), date(2026, 5, 31)))
FORBIDDEN_FROM = date(2026, 6, 1)
DAILY = ("metrics", "liquidationSnapshot")
SCHEMA = "c18-download-v1"


def days():
    for a, b in WINDOWS:
        d = a
        while d <= b:
            yield d
            d += timedelta(days=1)


def months():
    seen = []
    for d in days():
        m = (d.year, d.month)
        if m not in seen:
            seen.append(m)
    return seen


def plan(streams):
    items = []
    for s in SYMBOLS:
        if "fundingRate" in streams:
            for y, m in months():
                items.append(("fundingRate", s, f"monthly/fundingRate/{s}/{s}-fundingRate-{y}-{m:02d}.zip"))
        for kind in DAILY:
            if kind in streams:
                for d in days():
                    items.append((kind, s, f"daily/{kind}/{s}/{s}-{kind}-{d.isoformat()}.zip"))
    assert all(d < FORBIDDEN_FROM for d in days()), "OOS tarihi plana girdi"
    return items


def guard(items):
    """Dosya adındaki hiçbir tarih 2026-06-01 ve sonrası olamaz."""
    for _, _, rel in items:
        m = re.search(r"-(\d{4})-(\d{2})(?:-\d{2})?\.zip$", rel)
        if not m or (int(m[1]), int(m[2])) >= (FORBIDDEN_FROM.year, FORBIDDEN_FROM.month):
            raise SystemExit(f"REDDEDİLDİ: yasak dönem veya tarihsiz dosya {rel}")


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "kaistrade-c18/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_one(args):
    kind, sym, rel, out = args
    dest = out / "raw" / kind / sym / Path(rel).name
    rec = {"kind": kind, "symbol": sym, "file": Path(rel).name, "status": None}
    try:
        data = fetch(ROOT + rel)
        want = fetch(ROOT + rel + ".CHECKSUM").decode().split()[0].lower()
    except urllib.error.HTTPError as e:
        rec["status"] = "MISSING" if e.code in (403, 404) else f"HTTP_{e.code}"
        return rec
    except (urllib.error.URLError, TimeoutError, OSError) as e:  # ağ hatası: doldurma yok, kayda geçer
        rec["status"] = f"ERROR_{type(e).__name__}"
        return rec
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        rec["status"] = "CHECKSUM_MISMATCH"
        return rec
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.testzip() is not None or not z.namelist():
                rec["status"] = "BAD_ZIP"
                return rec
    except zipfile.BadZipFile:
        rec["status"] = "BAD_ZIP"
        return rec
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        rec["status"] = "EXISTS_REFUSED"
        return rec
    dest.write_bytes(data)
    rec.update(status="OK", sha256=got, bytes=len(data))
    return rec


def probe():
    """Likidasyon arşivi var mı? 3 örnek gün (her pencereden), yalnız HEAD benzeri küçük GET."""
    sample = [date(2025, 4, 15), date(2025, 9, 15), date(2026, 4, 20)]
    res = []
    for d in sample:
        rel = f"daily/liquidationSnapshot/BTCUSDT/BTCUSDT-liquidationSnapshot-{d.isoformat()}.zip"
        try:
            n = len(fetch(ROOT + rel + ".CHECKSUM"))
            res.append((d.isoformat(), "VAR", n))
        except urllib.error.HTTPError as e:
            res.append((d.isoformat(), f"YOK (HTTP {e.code})", 0))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            res.append((d.isoformat(), f"HATA {type(e).__name__}", 0))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--streams", default="fundingRate,metrics,liquidationSnapshot")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args(argv)
    streams = set(a.streams.split(","))
    items = plan(streams)
    guard(items)
    if a.dry_run:
        print(f"{len(items)} dosya planlandı; pencereler: {[(x.isoformat(), y.isoformat()) for x, y in WINDOWS]}")
        for _, _, rel in items[:5] + items[-3:]:
            print(" ", ROOT + rel)
        return 0
    if a.probe:
        for d, st, n in probe():
            print(d, st)
        return 0
    out = Path(a.out)
    if out.exists() and any(out.iterdir()):
        raise SystemExit("Çıktı klasörü yeni ve boş olmalı.")
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(k, s, rel, out) for k, s, rel in items]
    with ThreadPoolExecutor(a.workers) as ex:
        recs = list(ex.map(get_one, jobs))
    summary = {}
    for r in recs:
        summary.setdefault(r["kind"], {}).setdefault(r["status"], 0)
        summary[r["kind"]][r["status"]] += 1
    (out / "c18_download_manifest.json").write_text(
        json.dumps({"schema": SCHEMA, "windows": [[x.isoformat(), y.isoformat()] for x, y in WINDOWS],
                    "summary": summary, "files": recs}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    bad = sum(v for k in summary.values() for s, v in k.items() if s not in ("OK", "MISSING"))
    print("Yalnız 'MISSING' (arşivde yok) kabul edilir; başka durum varsa bana manifesti gönder.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
