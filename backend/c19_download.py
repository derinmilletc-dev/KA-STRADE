"""C19 veri indirici (yalnız standart kütüphane). Ön kayıt: backend/studies/c19-hareket-devam-onkayit.md

İki aşama:
  rank  : arşiv dizininden USDT-M perpetual sembol listesini alır, 2025-03 15m mumlarını indirir, ortalama 24 saatlik quote hacmine göre
          ilk 100 sembolü seçer ve c19_universe.json yazar. (Yalnız 2025-03; TRAIN kararlarından önce.)
  fetch : seçilen semboller için klines/15m, markPriceKlines/15m, fundingRate (aylık) ve metrics (günlük) dosyalarını indirir.

Kurallar: yalnız 2025-03-01→2025-09-30 ve 2026-03-01→2026-05-31; 2026-06 ve sonrası REDDEDİLİR; her ZIP .CHECKSUM ile doğrulanır;
eksik veri doldurulmaz; üzerine yazma yok, ama yarım kalan indirme `fetch` yeniden çalıştırılarak devam ettirilir (doğrulanmış dosyalar atlanır).

Kullanım (PowerShell):
  python c19_download.py rank  --out C:\\Users\\ahmtt\\kaistrade-data\\c19
  python c19_download.py fetch --out C:\\Users\\ahmtt\\kaistrade-data\\c19 --dry-run
  python c19_download.py fetch --out C:\\Users\\ahmtt\\kaistrade-data\\c19
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

HOST = "https://data.binance.vision/"
LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
ROOT = "data/futures/um/"
WINDOWS = ((date(2025, 3, 1), date(2025, 9, 30)), (date(2026, 3, 1), date(2026, 5, 31)))
FORBIDDEN = (2026, 6)
TOP_N = 100
STABLE_BASES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "USDS", "EURI", "AEUR", "UST", "USDE", "BFUSD", "RLUSD"}
SCHEMA = "c19-download-v1"


def months():
    out = []
    for a, b in WINDOWS:
        d = a
        while d <= b:
            if (d.year, d.month) not in out:
                out.append((d.year, d.month))
            d += timedelta(days=1)
    return out


def days():
    for a, b in WINDOWS:
        d = a
        while d <= b:
            yield d
            d += timedelta(days=1)


def fetch(url, timeout=60, tries=4):
    last = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "kaistrade-c19/1"}), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
        time.sleep(1.5 * (k + 1))
    raise last


def guard(rel):
    m = re.search(r"-(\d{4})-(\d{2})(?:-\d{2})?\.zip$", rel)
    if not m or (int(m[1]), int(m[2])) >= FORBIDDEN:
        raise SystemExit(f"REDDEDİLDİ: yasak dönem veya tarihsiz dosya {rel}")


def list_symbols():
    """Arşiv dizini (delist edilmiş semboller dahil). Yalnız 'XXXUSDT' biçimli perpetual semboller; teslimli ('_' içeren) ve stabil çiftler dışlanır."""
    prefix, marker, syms = ROOT + "monthly/klines/", "", []
    while True:
        q = {"delimiter": "/", "prefix": prefix, "max-keys": "1000"}
        if marker:
            q["marker"] = marker
        root = ET.fromstring(fetch(LIST + "?" + urllib.parse.urlencode(q)))
        ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
        for cp in root.iter(ns + "CommonPrefixes"):
            syms.append(cp.find(ns + "Prefix").text[len(prefix):].strip("/"))
        trunc = root.find(ns + "IsTruncated")
        if trunc is not None and trunc.text == "true":
            nm = root.find(ns + "NextMarker")
            marker = nm.text if nm is not None else (root.findall(ns + "CommonPrefixes")[-1].find(ns + "Prefix").text)
        else:
            return filter_symbols(syms)


def filter_symbols(syms):
    out = []
    for s in sorted(set(syms)):
        if not s.endswith("USDT") or "_" in s or not re.fullmatch(r"[A-Z0-9]+USDT", s):
            continue
        if s[:-4] in STABLE_BASES:
            continue
        out.append(s)
    return out


def march_volume(sym, out):
    """2025-03 ortalama 24s quote hacmi = ay toplamı quote hacim / 31 gün."""
    rel = f"{ROOT}monthly/klines/{sym}/15m/{sym}-15m-2025-03.zip"
    guard(rel)
    try:
        data = fetch(HOST + rel)
        want = fetch(HOST + rel + ".CHECKSUM").decode().split()[0].lower()
    except urllib.error.HTTPError:
        return sym, None
    except (urllib.error.URLError, TimeoutError, OSError):
        return sym, "ERROR"
    if hashlib.sha256(data).hexdigest() != want:
        return sym, "CHECKSUM"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        raw = z.read(z.namelist()[0]).decode("utf-8-sig").splitlines()
    tot = 0.0
    for ln in raw:
        p = ln.split(",")
        if len(p) > 7 and p[0].isdigit():
            tot += float(p[7])
    return sym, tot / 31.0


def cmd_rank(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    syms = list_symbols()
    print(f"{len(syms)} aday sembol (USDT perpetual, stabil hariç)")
    with ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda s: march_volume(s, out), syms))
    bad = [(s, v) for s, v in res if v in ("ERROR", "CHECKSUM")]
    if bad:
        raise SystemExit(f"Ağ/doğrulama hatası {len(bad)} sembolde; yeniden çalıştır: {bad[:5]}")
    vols = sorted(((v, s) for s, v in res if isinstance(v, float)), reverse=True)
    top = [s for _, s in vols[:TOP_N]]
    (out / "c19_universe.json").write_text(json.dumps({
        "schema": SCHEMA, "rule": "2025-03 ortalama 24s quote hacmi, ilk 100", "candidates": len(syms), "with_march_2025_data": len(vols),
        "top": [{"symbol": s, "avg_24h_quote_volume": v} for v, s in vols[:TOP_N]]}, indent=1), encoding="utf-8")
    print(f"İlk {len(top)} sembol yazıldı: {out / 'c19_universe.json'}")
    print(", ".join(top[:12]), "...")


def plan(symbols):
    items = []
    for s in symbols:
        for y, m in months():
            for kind, extra in (("klines", "15m"), ("markPriceKlines", "15m")):
                items.append((kind, s, f"{ROOT}monthly/{kind}/{s}/{extra}/{s}-{extra}-{y}-{m:02d}.zip"))
            items.append(("fundingRate", s, f"{ROOT}monthly/fundingRate/{s}/{s}-fundingRate-{y}-{m:02d}.zip"))
        for d in days():
            items.append(("metrics", s, f"{ROOT}daily/metrics/{s}/{s}-metrics-{d.isoformat()}.zip"))
    for _, _, rel in items:
        guard(rel)
    return items


def verified(dest, want):
    return dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == want


def get_one(args):
    kind, sym, rel, out = args
    dest = out / "raw" / kind / sym / Path(rel).name
    rec = {"kind": kind, "symbol": sym, "file": Path(rel).name, "status": None}
    try:
        want = fetch(HOST + rel + ".CHECKSUM").decode().split()[0].lower()
        if verified(dest, want):
            rec["status"] = "OK"
            return rec
        data = fetch(HOST + rel)
    except urllib.error.HTTPError as e:
        rec["status"] = "MISSING" if e.code in (403, 404) else f"HTTP_{e.code}"
        return rec
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        rec["status"] = f"ERROR_{type(e).__name__}"
        return rec
    if hashlib.sha256(data).hexdigest() != want:
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
    dest.write_bytes(data)
    rec["status"] = "OK"
    return rec


def cmd_fetch(a):
    out = Path(a.out)
    uni = json.loads((out / "c19_universe.json").read_text(encoding="utf-8"))
    symbols = [x["symbol"] for x in uni["top"]]
    items = plan(symbols)
    if a.dry_run:
        print(f"{len(items)} dosya, {len(symbols)} sembol; pencereler: {[(x.isoformat(), y.isoformat()) for x, y in WINDOWS]}")
        for _, _, rel in items[:3] + items[-2:]:
            print(" ", HOST + rel)
        return 0
    jobs = [(k, s, rel, out) for k, s, rel in items]
    recs = []
    with ThreadPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(get_one, jobs), 1):
            recs.append(r)
            if i % 2000 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    summary = {}
    for r in recs:
        summary.setdefault(r["kind"], {}).setdefault(r["status"], 0)
        summary[r["kind"]][r["status"]] += 1
    (out / "c19_download_manifest.json").write_text(json.dumps({"schema": SCHEMA, "symbols": symbols, "summary": summary, "files": recs}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    bad = sum(v for k in summary.values() for s, v in k.items() if s not in ("OK", "MISSING"))
    print("Yalnız 'OK' ve 'MISSING' (arşivde yok) kabul edilir. Başka durum varsa aynı komutu tekrar çalıştır (devam eder).")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["rank", "fetch"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    return cmd_rank(a) if a.cmd == "rank" else cmd_fetch(a)


if __name__ == "__main__":
    sys.exit(main())
