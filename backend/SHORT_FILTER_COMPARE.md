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

---

# Yeni adaylar: "tükenme" kuralı (EXHAUST_80, EXHAUST_ANY)

Canlı strateji, demo, TP/SL, risk ayarları ve diğer dosyalar DEĞİŞMEDİ. `app/short_filter_study.py` canlı kod tarafından
içe aktarılmaz; yalnız çevrimdışı araştırmadır.

## 1) Mevcut filtre tam olarak ne yapıyor
`shared_mtf_decision` içinde `alignment = 0.25*güven15m + 0.35*güven1h + 0.40*güven4h`; SHORT için `alignment >= 80` ise
giriş reddedilir. 15m güveni zaten >= 80 (varsayılan eşik) olmak zorunda; yani filtre, "üç zaman diliminin de güçlü SHORT
dediği" durumları reddeder. Güven puanı yalnız trend/momentum onaylarının toplamıdır; aşırı uzama, destek mesafesi, fitil
bilgisi içermez. Yani güçlü SHORT'u reddederken fiyatın zaten dipte olup olmadığına bakmaz.

## 2) Tasarlanan aday kurallar (sonuç görülmeden önce değil, ilk 7 aday raporu görüldükten SONRA; eşiksiz)
Tükenme işaretleri `analysis.py`'deki trap_score SHORT sabitleriyle AYNI eşikleri kullanır (yeni serbest parametre yok):
uzama > 2 ATR, RSI < 28, destek mesafesi < 1 ATR, alt fitil/gövde > 1.5. Düşük hacim (< 0.9) tükenme sayılmaz, zayıf
harekettir.
- `EXHAUST_80`: SHORT yalnız `alignment >= 80` VE en az bir tükenme işareti varsa reddedilir. Yüksek güven tek başına ret
  sebebi değildir.
- `EXHAUST_ANY`: alignment'a hiç bakmadan, en az bir tükenme işareti varsa reddedilir.
İşaretler bilinmiyorsa (alan eksik ya da beklenmeyen biçim) kural ENGELLEMEZ; sessizce yanlış sınıflamaz. Aday yalnız giriş
eler, giriş eklemez.

## 3) trap_score ve breakout_quality ile çakışma (hesaplanmış)
- Trap kapısı `trap_score <= 35`; SHORT'ta taban 10, ağırlıklar: düşük hacim 24, alt fitil 20, uzama 18, destek yakın 15,
  RSI düşük 10. Kapıyı yalnız şu kombinasyonlar geçer: hiç uyarı yok, tek uyarı (herhangi biri) ve destek+RSI çifti.
  Diğer iki-ve-üzeri kombinasyonların hepsi kapıda ZATEN reddedilir (`trap_gate_pairs()`, testle doğrulanır).
  Sonuç: tükenme kuralı yalnız tek-uyarılı (ve destek+RSI) durumlarda ek bir şey yapabilir; çakışma büyük.
- `breakout_quality >= 50`: taban 35 + hacim (0/5/20) + ADX>=20 (15) + güçlü trend (15) + momentum (10) − 0.35*trap. Güçlü
  hizalı SHORT'larda trend/momentum puanları zaten dolu olduğu için bu kapı onları nadiren eler.
- Rapordaki "Çakışma analizi" bölümü (OFF kümesi üstünden) bunu veriyle gösterir: mevcut filtrenin reddettiği SHORT'larda
  hangi işaretler ne sıklıkla var, ortalama kırılım kalitesi/güven, ve (işlem düzeyinde) işaret var/yok gruplarının ort. R'si.
  İşlemler (sembol, açılış zamanı) ile sinyal özelliklerine bağlanır; bağlanamayan işlem sayısı raporda yazılır.

## 4) "Gerçekten daha iyi" için önceden sabit ölçüt
Aday, tabandan (CUR_80, çalıştırıldıysa OFF) YALNIZCA şu koşulda "daha iyi KANITLANDI" sayılır: TRAIN ve VALIDATION'da ortalama R
farkı pozitif VE farkın %95 bootstrap aralığının alt sınırı > 0 (sabit tohum, bağımsız yeniden örnekleme). Aksi hâlde rapor
`KANITLANMADI` yazar; bu "daha kötü" değil, "bu veriyle ayırt edilemiyor" demektir.

## 5) Çalıştırma (laptop, repo klasöründe; ilk kez çalıştırılan klasörün üstüne yazmaz)
```
.\.venv\Scripts\python.exe backend\short_filter_v2_compare_cli.py --metadata C:\Users\ahmtt\kaistrade-data\donchian-holdout-evidence\current-metadata.json --variants CUR_80 OFF EXHAUST_80 EXHAUST_ANY --output-name short-filter-v2-exhaust
```
Çıktı: `kaistrade-data\short-filter-v2-exhaust\REPORT.md` (+ results.json, işlem CSV'leri). CUR_80 kontrolü yine run3 ve ön tarama
ile birebir eşleşmek zorundadır.

## 6) Sınırlamalar
- Bu PR'da yeni kural gerçek veriyle ÇALIŞTIRILMADI (veri yalnız laptopta). Birim testleri: bayraklar gerçek `analyze()`
  çıktısında bağımsız alt fitil hesabıyla doğrulandı; filtre mantığı, kanıt ölçütü, özet ve rapor testli.
- İlk 7 aday sonucu görüldükten sonra tasarlandığı için bu veri bu aday ailesi için "kör" değildir; toplam 9 aday, çoklu
  karşılaştırma düzeltmesi yok. Hiçbir şey 17 canlı işlemden hareketle seçilmedi.
- Yeni kural canlıya alınmaz; canlıya alma ayrı PR ve açık onay gerektirir.
