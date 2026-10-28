# Kais 2 – Küçük ölçüm aracı (günlük mum, evren, funding etkisi)

Ana veri indirme aracı **değildir**. Yalnız `klines/1d` aylık dosyalarını (≈20 MB) indirir ve şunu ölçer:
her ayın ilk-100 evreninde (önceki ayın ort. günlük USDT hacmi) kaç coin funding veya başka eksik yüzünden
test dışı kalıyor; funding'i kesilen coinlerin günlük mumları o aylarda canlı mı.

Kurallar: yalnız 2024-05..2026-05; kilitli dönem (2026-06+) listelenmez/indirilmez; her dosya `.CHECKSUM` ile
SHA-256 doğrulanır; API anahtarı/emir yok; yeni boş klasöre yazar; mevcut dosya ezilmez; Kais 1'e dokunmaz.

```
# 1) Önce yalnız plan (hiçbir şey indirmez/yazmaz)
python history_universe_probe.py --report <discovery-report.json> --plan-only
# 2) İndirme + rapor
python history_universe_probe.py --report <discovery-report.json> --output <yeni boş klasör>
# Yarıda kalırsa aynı komut + --resume
```

Çıktı: `reports-*/summary.md`, `universe-by-month.csv`, `universe-impact.csv`, `funding-gap-check.csv`, `download-log.csv`.
