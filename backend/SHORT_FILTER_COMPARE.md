# SHORT filtresi karşılaştırma aracı (Kais Original V2 hattı)

Amaç: mevcut SHORT filtresini (alignment >= 80 ise SHORT reddedilir) ve aday kuralları AYNI geçmiş mumlarda,
V2'nin KENDİ çevrimdışı test hattıyla karşılaştırmak. Canlı stratejiye ve canlı parametrelere dokunmaz; yalnız
çevrimdışı araştırmadır. Hiçbir aday "kazanan" ilan edilmez.

## Ne kullanır
- V2'nin ön tarama düzeni: ölçüm görünümü `kaistrade-data\measurement-view-2020-10_2022-08` (TRAIN 2020-10..2021-12,
  VALIDATION 2022-01..2022-08; mühürlü TEST'e yükleyici ve yol bekçisi izin vermez), V2 profili, aynı motor ve maliyetler.
- Aday kurallar `app/short_filter_study.py` içinde sonuçlar görülmeden önce sabitlenmiştir
  (`CUR_80` kontrol, `OFF`, `THR_75`, `THR_85`, `THR_90`, `EXT_ONLY_80`, `SYM_80`). Adaylar yalnız giriş ELER.
- Her (aday, dönem) için motor yeniden çalışır; "filtre kapalı" karar bir kez hesaplanıp önbelleğe alınır.

## Güvenilirlik kontrolleri (başarısızsa sonuç üretilmez / rapor en üstte uyarır)
1. `CUR_80`, kayıtlı **run3 sayımlarıyla** (karar sayıları, ret nedenleri, boyutlandırma...) birebir aynı olmalı.
   Aynı değilse çalışma durur (`RUN3_COUNT_PARITY_MISMATCH`).
2. `CUR_80` işlem sayısı ve ortalama R'si, eski **ön tarama sonucuyla** (`original-v2-prescreen\prescreen.json`)
   aynı olmalı. Dosya yoksa `ATLANDI` yazılır ve başarılı sayılmaz.
3. Eski ön taramanın "kaynak dosyası değişmedi" kilidi uygulanmaz (o tarihten beri uygulama dosyaları değişti); değişen
   dosyalar raporda listelenir. Asıl koruma 1 ve 2'dir.

## Çalıştırma (Windows PowerShell, repo klasöründe; laptop prizde, uyku kapalı)
```
# 1) Önce yalnız kontrol adayı (güvenilirlik kontrolleri + kontrol sonuçları)
.\.venv\Scripts\python.exe backend\short_filter_v2_compare_cli.py --metadata C:\Users\ahmtt\kaistrade-data\donchian-holdout-evidence\current-metadata.json --variants CUR_80
```
Çıktı klasörü `C:\Users\ahmtt\kaistrade-data\short-filter-v2-compare` ve zaten varsa betik durur (üstüne yazmaz).
Tam karşılaştırma için bu klasörü başka bir adla taşıyıp aynı komutu `--variants` vermeden çalıştırın.
Çıktılar: `REPORT.md`, `results.json`, `islemler-*.csv` (her adayın işlemleri), `degisim-*.csv` (kontrolden
eklenen/elenen işlemler). Raporda LONG/SHORT ayrı, TRAIN/VALIDATION ayrı, BTC aylık getirisine göre YUKARI/ASAGI/YATAY.

## Sınırlamalar
Saf hesap ve karar sarmalayıcı birim testlidir (`tests/test_short_filter_study.py`). Betiğin veri yükleme ve motor
kısmı bu geliştirme ortamında gerçek veriyle ÇALIŞTIRILAMADI (veri ve bağımlılıklar yok). İlk gerçek çalıştırmada
hata olursa çıktıyı olduğu gibi bildirin. Veri penceresi 2020-10..2022-08'dir ve V2 bu pencerede geliştirildi.
