# Kais 2 – Ana tarihsel veri indirme (iki aşamalı)

Yalnız geliştirme dönemi (2024-05..2026-05). **Kilitli dönem (2026-06 ve sonrası) listelenmez, indirilmez.**

## Aşama 1 (≈20 MB): günlük mumlar → aylık ilk-100 evren + funding etkisi raporu
```
python history_download.py stage1 --report <discovery-report.json> --output <yeni boş klasör>
```
Sonunda durur. `reports-*/summary.md` birlikte incelenir.

## Aşama 2: yalnız onaydan sonra
```
python history_download.py stage2 --report <discovery-report.json> --output <aynı klasör>            # yalnız plan
python history_download.py stage2 --report <discovery-report.json> --output <aynı klasör> --confirm   # indirir
```
Evrene giren coinlerin klines 15m/1h/4h, markPriceKlines 15m, fundingRate dosyaları. Boş alan denetimi (≥10 GB + veri).

## Ortak kurallar
SHA-256 (.CHECKSUM) her dosyada; tutmayan dosya kaydedilmez. Eksik veri doldurulmaz, interpolasyon yok. API anahtarı/emir yok.
Yeni boş klasör; mevcut dosya ezilmez; yarıda kalırsa aynı komut + `--resume`. Kais 1'e dokunmaz.

Çıktı: `reports2-*/validation-summary.md`, `validation-coin-month.csv` (tam / tam (mum boşluğu) / eksik: türler),
`download-log.csv`, `content-problems.csv`.
