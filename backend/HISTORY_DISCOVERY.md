# Kais 2 – Arşiv keşif aracı

`backend/history_discovery.py` Binance'in herkese açık USD-M arşivinin yalnız **dosya listesini** okur ve
coin/ay bazında veri bulunabilirliğini raporlar. Hiçbir veri dosyası indirmez, mevcut dosyaları değiştirmez,
API anahtarı veya emir yetkisi kullanmaz, Kais 1 kodunu içe aktarmaz. Kilitli dönem (2026-06 ve sonrası)
raporda yer almaz.

## Çalıştırma

Python 3.10+ yeterli; ek paket gerekmez. Canlı botun klasörleri dışında, yeni veya boş bir klasöre yazar.

```
python history_discovery.py --output C:\Users\<kullanıcı>\kaistrade-data\discovery-2026-10-27
```

Deneme için: `--symbols BTCUSDT ETHUSDT`. Emir defteri listesini atlamak için: `--skip-orderbook`.
İstek hızı varsayılan saniyede 4'tür (`--rate`).

## Çıktılar

| Dosya | İçerik |
| --- | --- |
| `summary.md` | Coin bazında bulunabilirlik, aylık eksikler, funding kapsamı, depolama tahmini, maliyet senaryoları, riskler, son kontrol |
| `coin-availability.csv` | Her coin: durum, ilk/son ay, delist, test edilebilir ay, funding ve emir defteri kapsamı |
| `monthly-gaps.csv` | Eksik veya kısmi her coin-ay ve sebebi (eksik veri tahminle doldurulmaz) |
| `funding-coverage.csv` | Her coin için funding kapsamı ve eksik aylar |
| `cost-scenario-iyimser.csv`, `-temel.csv`, `-kotumser.csv` | Likidite ve oynaklığa göre spread/kayma simülasyonu (gerçek bid-ask değildir) |
| `discovery-report.json` | Tüm ham sonuç |
