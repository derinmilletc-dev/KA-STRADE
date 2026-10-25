#!/usr/bin/env python3
"""Grid — Gün 1 Testnet kontrolü. SADECE Binance Futures TESTNET.

Kullanım (sunucuda, anahtarı SADECE sen o ekranda tanımlarsın; bana yazma, dosyaya yazma):
    export BINANCE_TESTNET_API_KEY=...      # Testnet anahtarı (para çekme izni YOK)
    export BINANCE_TESTNET_API_SECRET=...
    cd backend && python -B scripts/grid_testnet_day1.py --report-dir ./grid_reports

Kurallar: adres kodda sabit (Testnet); anahtar loglara/rapora yazılmaz; belirsiz sonuçta emir TEKRAR GÖNDERİLMEZ,
kimlikle sorgulanır; başarısız test gizlenmez; ilk API kontrolü (kurallar/bağlantı) başarısızsa emir testlerine GEÇİLMEZ.
Çıkış kodu: 0 = hepsi geçti, 1 = en az bir başarısız, 2 = hazırlık hatası.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.grid_engine.ports import ExchangeError, ExchangeRejected, ExchangeTimeout  # noqa: E402
from app.grid_engine.testnet_adapter import BinanceTestnetAdapter, ENV_KEY, ENV_SECRET  # noqa: E402

PASS, FAIL, SKIP, INFO = "GEÇTİ", "BAŞARISIZ", "ATLANDI", "BİLGİ"
SYMBOLS = ("BTCUSDT", "ETHUSDT")


@dataclass
class Result:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    started: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))
    results: list[Result] = field(default_factory=list)
    secrets: tuple[str, ...] = ()

    def add(self, name: str, status: str, detail: str) -> Result:
        clean = detail
        for secret in self.secrets:
            if secret:
                clean = clean.replace(secret, "***")
        r = Result(name, status, clean)
        self.results.append(r)
        print(f"[{status}] {name}: {clean}", flush=True)
        return r

    @property
    def failed(self) -> bool:
        return any(r.status == FAIL for r in self.results)

    def write(self, directory: Path) -> tuple[Path, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        js, md = directory / f"grid_day1_{stamp}.json", directory / f"grid_day1_{stamp}.md"
        js.write_text(json.dumps({"started": self.started, "results": [asdict(r) for r in self.results]}, ensure_ascii=False, indent=2))
        lines = [f"# Grid Gün 1 Testnet raporu ({self.started})", "", "Adres: Testnet (kodda sabit). Gerçek para/canlı bağlantı yok.", "",
                 "| Test | Sonuç | Ayrıntı |", "|---|---|---|"]
        lines += [f"| {r.name} | {r.status} | {r.detail.replace('|', '/')} |" for r in self.results]
        def st(prefix: str) -> str:
            found = [r.status for r in self.results if r.name.startswith(prefix)]
            return (FAIL if FAIL in found else PASS if found and all(x == PASS for x in found) else SKIP if not found or SKIP in found else INFO)
        accept = [("Tek yönlü mod", st("Tek yönlü")), ("BTC/ETH filtreleri", st("Testnet kuralları")), ("Fiyat ve pozisyon okuma", FAIL if any(r.status == FAIL and r.name.startswith(("Fiyat", "Pozisyon okuma")) for r in self.results) else st("Pozisyon okuma")),
                  ("Emir oluşturma", st("Emir oluşturma")), ("Aynı kimlikle tekrar (açıkken)", st("Kimlik tekrarı (emir açıkken)")),
                  ("İptal", st("Emir iptali")), ("Yanıt kaybı", st("Yanıt kaybı")), ("Son pozisyon/emir kontrolü", FAIL if FAIL in (st("Pozisyon değişmedi"), st("Temizlik")) else st("Pozisyon değişmedi"))]
        lines += ["", "## Kabul kriterleri", "", "| Kontrol | Sonuç |", "|---|---|"] + [f"| {n} | {v} |" for n, v in accept]
        skipped = [r.name for r in self.results if r.status == SKIP]
        lines += ["", f"**Genel: {'BAŞARISIZ' if self.failed else 'GEÇTİ (atlananlar hariç)'}**"]
        if skipped:
            lines += ["", f"Atlanan {len(skipped)} madde ELLE doğrulanmalı (geçmiş sayılmaz): " + "; ".join(skipped)]
        md.write_text("\n".join(lines) + "\n")
        return js, md


def _order_kinds(adapter: BinanceTestnetAdapter, symbol: str, prefix: str) -> list[str]:
    return [o.client_id for o in adapter.open_orders(symbol) if o.client_id.startswith(prefix)]


def check_rules_and_connection(adapter: BinanceTestnetAdapter, report: Report) -> bool:
    ok = True
    try:
        adapter.assert_one_way_mode()
        report.add("Tek yönlü (one-way) mod", PASS, "dualSidePosition=false")
    except Exception as exc:  # noqa: BLE001 — her türlü hata rapora yazılır
        report.add("Tek yönlü (one-way) mod", FAIL, f"{type(exc).__name__}: {exc}")
        ok = False
    for symbol in SYMBOLS:
        try:
            f = adapter.symbol_filters(symbol)
            sane = f["tickSize"] > 0 and f["stepSize"] > 0 and f["minNotional"] >= 0
            report.add(f"Testnet kuralları {symbol}", PASS if sane else FAIL,
                       f"fiyat adımı {f['tickSize']}, miktar adımı {f['stepSize']}, en düşük emir {f['minNotional']} USDT"
                       " (Ekranda gördüğün değerlerle karşılaştır; Testnet ile canlı kuralları farklı olabilir.)")
            ok = ok and sane
        except Exception as exc:  # noqa: BLE001
            report.add(f"Testnet kuralları {symbol}", FAIL, f"{type(exc).__name__}: {exc}")
            ok = False
        try:
            report.add(f"Fiyat okuma {symbol}", PASS, f"{adapter.mark_price(symbol)}")
            report.add(f"Pozisyon okuma {symbol}", PASS, f"miktar {adapter.position(symbol)}")
        except Exception as exc:  # noqa: BLE001
            report.add(f"Fiyat/pozisyon okuma {symbol}", FAIL, f"{type(exc).__name__}: {exc}")
            ok = False
    return ok


def order_and_id_tests(adapter: BinanceTestnetAdapter, report: Report, symbol: str = "BTCUSDT") -> None:
    run_id = time.strftime("%H%M%S", time.gmtime())
    cid = f"kg-d1{run_id}-L01E0"
    prefix = "kg-d1"
    f = adapter.symbol_filters(symbol)
    mark = adapter.mark_price(symbol)
    # Fiyat filtresinin (PERCENT_PRICE) izin verdiği EN UZAK noktaya yakın: dolma ihtimali en düşük, ama borsa 'fiyat çok uzak' diye reddetmez.
    factor = min(0.95, f["percentDown"] + 0.01) if f.get("percentDown", 0) > 0 else 0.9
    price = round(mark * factor / f["tickSize"]) * f["tickSize"]
    steps = max(1, int(-(-(f["minNotional"] * 1.2 / price) // f["stepSize"])))
    qty = round(steps * f["stepSize"], 8)
    price = round(price, 8)
    before = adapter.position(symbol)
    created = False
    try:
        # 1) oluştur
        try:
            info = adapter.place_order(client_id=cid, symbol=symbol, side="BUY", type="LIMIT", qty=qty, price=price)
            created = True
            report.add("Emir oluşturma", PASS, f"{cid} durum {info.status}, fiyat {price}, miktar {qty}")
        except ExchangeTimeout as exc:
            # BELİRSİZ: tekrar GÖNDERME; kimlikle sorgula
            found = adapter.get_order(symbol, cid)
            created = found is not None
            report.add("Emir oluşturma (yanıt kaybı)", INFO, f"{exc}; kimlikle sorgu: {'emir borsada' if found else 'emir yok'}; yeniden gönderilmedi")
        # 2) oku
        got = adapter.get_order(symbol, cid)
        if got is not None and got.filled > 0:
            report.add("Emir DOLDU (beklenmiyordu)", FAIL, f"{cid} miktar {got.filled} doldu. Testnet'te pozisyon açıldı; elle kapat. Son pozisyon kontrolü de bunu gösterecek.")
        listed = cid in _order_kinds(adapter, symbol, prefix)
        report.add("Emir durumunu okuma", PASS if got and got.status == "NEW" and listed else FAIL,
                   f"get_order={'yok' if got is None else got.status}; açık emirler listesinde={listed}")
        # 3) kimlik tekrarı — emir AÇIKKEN
        try:
            adapter.place_order(client_id=cid, symbol=symbol, side="BUY", type="LIMIT", qty=qty, price=price)
            n = len(_order_kinds(adapter, symbol, cid))
            report.add("Kimlik tekrarı (emir açıkken)", FAIL, f"Borsa aynı kimliği KABUL ETTİ; aynı kimlikli açık emir sayısı: {n}. Motor borsaya güvenmemeli (zaten güvenmiyor).")
        except ExchangeRejected as exc:
            report.add("Kimlik tekrarı (emir açıkken)", PASS if exc.code == -4116 else INFO, f"Reddedildi, kod {exc.code} (beklenen -4116)")
        except ExchangeTimeout as exc:
            report.add("Kimlik tekrarı (emir açıkken)", INFO, f"yanıt kaybı: {exc}; tekrar denenmedi")
        # 4) iptal
        try:
            cancelled = adapter.cancel_order(symbol, cid)
            after = adapter.get_order(symbol, cid)
            report.add("Emir iptali", PASS if after and after.status == "CANCELED" else FAIL, f"iptal yanıtı {cancelled.status}; sorgu {after.status if after else 'yok'}")
        except ExchangeError as exc:
            report.add("Emir iptali", FAIL, f"{type(exc).__name__}: {exc}")
        # 5) kimlik tekrarı — emir BİTMİŞKEN (iptal edilmiş)
        try:
            adapter.place_order(client_id=cid, symbol=symbol, side="BUY", type="LIMIT", qty=qty, price=price)
            report.add("Kimlik tekrarı (emir iptal edilmişken)", INFO, "Borsa biten emrin kimliğinin YENİDEN kullanılmasına izin verdi. Motor bu yüzden biten kimliği hiç yeniden göndermiyor; ek bir sorun değil. Bu emir temizlenecek.")
        except ExchangeRejected as exc:
            report.add("Kimlik tekrarı (emir iptal edilmişken)", INFO, f"Reddedildi, kod {exc.code}")
        except ExchangeTimeout as exc:
            report.add("Kimlik tekrarı (emir iptal edilmişken)", INFO, f"yanıt kaybı: {exc}; tekrar denenmedi")
    finally:
        # temizlik: test önekli açık emir KALMASIN
        try:
            for leftover in _order_kinds(adapter, symbol, prefix):
                try:
                    adapter.cancel_order(symbol, leftover)
                except ExchangeError:
                    pass
            remaining = _order_kinds(adapter, symbol, prefix)
            report.add("Temizlik (borsada test emri kalmadı)", PASS if not remaining else FAIL, "açık test emri yok" if not remaining else f"KALAN: {remaining}")
            after_position = adapter.position(symbol)
            report.add("Pozisyon değişmedi (borsadan)", PASS if after_position == before else FAIL, f"önce {before}, sonra {after_position}")
        except ExchangeError as exc:
            report.add("Temizlik", FAIL, f"doğrulanamadı: {type(exc).__name__}: {exc} — Testnet'te '{prefix}' önekli emirleri elle kontrol et")


def run(adapter: BinanceTestnetAdapter, report: Report) -> Report:
    report.add("Stop verisi (açık pozisyon stopu sunucudan okunuyor mu)", SKIP,
               "Bu betik sunucu oturumuna erişmez. Elle doğrula: Master Trade'de açık pozisyon varken Grid önizlemede 'Açık pozisyonların stopa kadar zararı' sayı mı yoksa 'bilinmiyor' mu gösteriyor?")
    report.add("/api/v25/grid/filters (sunucu yolu)", SKIP,
               "Sunucu yolu oturum ister; betik çağırmaz. Elle doğrula: Grid önizlemede BTC ve ETH seçince 'Borsa kuralları: ...' satırı çıkıyor mu? Not: bu yol canlı piyasanın herkese açık kurallarını okur (Testnet değil).")
    report.add("Yanıt kaybı (çift emir yok)", SKIP,
               "Gerçek Testnet'te yapay yanıt kaybı üretilemez; bu davranış sahte sunucuda kanıtlandı. Bu betikte bir zaman aşımı gerçekten olursa emir TEKRAR GÖNDERİLMEZ, kimlikle sorgulanır ve rapora yazılır.")
    if not check_rules_and_connection(adapter, report):
        report.add("Emir testleri", SKIP, "İlk API kontrolü başarısız; kurallara göre emir testlerine GEÇİLMEDİ. Önce bağlantı/veri sorununu çöz.")
        return report
    try:
        order_and_id_tests(adapter, report)
    except Exception as exc:  # noqa: BLE001
        report.add("Emir testleri", FAIL, f"beklenmeyen hata: {type(exc).__name__}: {exc}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-dir", default="./grid_reports")
    args = parser.parse_args(argv)
    key, secret = os.environ.get(ENV_KEY, ""), os.environ.get(ENV_SECRET, "")
    if not key or not secret:
        print(f"{ENV_KEY} ve {ENV_SECRET} tanımlı değil (yalnızca Testnet anahtarı).", file=sys.stderr)
        return 2
    report = Report(secrets=(key, secret))
    adapter = BinanceTestnetAdapter(key, secret)
    run(adapter, report)
    js, md = report.write(Path(args.report_dir))
    print(f"\nRapor: {md}\nGenel: {'BAŞARISIZ' if report.failed else 'GEÇTİ'}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
