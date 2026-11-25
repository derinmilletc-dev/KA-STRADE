"""İleriye dönük test aracı (emir vermez): 
  python3 forward_test_cli.py status            -> kaç sinyal toplandı/çözüldü
  python3 forward_test_cli.py report [--json]   -> başarı raporu ve geliştirme önerileri
  python3 forward_test_cli.py tick              -> elle bir tarama+çözüm turu (genel Binance verisini okur)
Veritabanı: KAIS_FORWARD_TEST_PATH ya da varsayılan veri klasöründeki forward-test.sqlite3; ilk argüman olarak yol da verilebilir (--path=...)."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app import forward_test as ft
from app.score_report import build, render


def _path(argv: list[str]) -> Path:
    for arg in argv:
        if arg.startswith("--path="):
            return Path(arg.split("=", 1)[1])
    return ft.default_path()


async def _tick(store: ft.Store) -> dict:
    import httpx
    async with httpx.AsyncClient(base_url="https://fapi.binance.com", timeout=15) as client:
        async def get(path: str, params: dict):
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        return await ft.scan_once(get, store)


def main(argv: list[str]) -> int:
    command = next((a for a in argv if not a.startswith("--")), "report")
    path = _path(argv)
    if command in {"report", "status"} and not path.exists():
        print(f"Veritabanı yok: {path}. Uygulama en az bir tur çalıştıktan (≈2-15 dk) sonra oluşur; KAIS_FORWARD_TEST=0 ile kapatılmış olabilir.", file=sys.stderr)
        return 2
    store = ft.Store(path)
    if command == "tick":
        print(json.dumps(asyncio.run(_tick(store)), ensure_ascii=False), store.counts())
        return 0
    if command == "status":
        counts = store.counts()
        first = counts["first_signal_time"]
        since = datetime.fromtimestamp(first, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if first else "-"
        print(f"Sinyal: {counts['signals']}  Sonuçlanan: {counts['resolved']}  Bekleyen: {counts['pending']}  İlk sinyal: {since}")
        return 0
    if command == "report":
        report = build(store.records())
        print(json.dumps(report, ensure_ascii=False, indent=1) if "--json" in argv else render(report))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
