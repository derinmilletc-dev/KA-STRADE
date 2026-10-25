# SHORT filtresi karşılaştırma aracı

Amaç: mevcut SHORT filtresini (alignment >= 80 ise SHORT reddedilir) ve aday kuralları AYNI geçmiş mumlarda,
AYNI çıkış kuralı ve portföy motoruyla karşılaştırmak. Canlı stratejiye ve canlı parametrelere dokunmaz; yalnız
çevrimdışı araştırmadır. Hiçbir aday "kazanan" ilan edilmez.

## Ne yapar
- Eski Stage 5 deneyiyle aynı geliştirme verisini (TRAIN + VALIDATION) kullanır. Mühürlü TEST'e dokunmaz.
- Aday kurallar `app/short_filter_study.py` içinde sonuçlar görülmeden önce sabitlenmiştir
  (`CUR_80` kontrol, `OFF`, `THR_75`, `THR_85`, `THR_90`, `EXT_ONLY_80`, `SYM_80`).
- Adaylar yalnız giriş ELER; eski deneyin "filtre kapalı" kararlarının alt kümesidir.
- Çıktı: `<output>/short-filter-compare/` altında `REPORT.md`, `results.json`, her aday için işlem listesi,
  kontrolden EKLENEN/ELENEN işlemlerin tek tek listesi (`degisim-*.csv`).
- Raporda: işlem sayısı, ortalama R, kâr faktörü, kazanma oranı, en büyük düşüş; LONG ve SHORT ayrı; TRAIN ve
  VALIDATION ayrı; BTC'nin aylık getirisine göre YUKARI/ASAGI/YATAY dönem kırılımı.

## Güvenilirlik kontrolleri (başarısızsa sonuç üretilmez / rapor en üstte uyarır)
1. `CUR_80` kuralı, OFF kararlarına uygulanınca canlı filtre (ON) kararlarını birebir üretmeli (`decision-parity.json`).
2. `CUR_80` ve `OFF` sonuçları eski deneyin `regime-study.json` sonuçlarıyla (işlem sayısı ve ortalama R) aynı olmalı.
   Dosya yoksa durum `ATLANDI` yazılır ve başarılı sayılmaz.

## Çalıştırma (sunucuda, `backend` klasöründe; hiçbir anahtar gerekmez, ağ erişimi kapatılır)
```
# 1) Önbellek + canlı filtre uyumu (ilk seferde uzun sürebilir, sonra yeniden kullanılır)
python short_filter_compare_cli.py --output ~/kaistrade-data/regime --workers 4 --check-only
# 2) Tam karşılaştırma
python short_filter_compare_cli.py --output ~/kaistrade-data/regime --workers 4
# İsteğe bağlı: --orderings STOP_FIRST TP_FIRST   --variants CUR_80 OFF THR_85
```
`--output`, eski Stage 5 çıktı klasörüdür (içinde `development/`, `study-lock.json`, `regime-study.json`).
Önbellek, kod değiştiği için eski önbellekten farklı bir parmak iziyle yeniden üretilir; eski dosyalara dokunulmaz.

## Sınırlamalar
Betiğin dosya/veri tarafı (yükleme, motor, önbellek) bu geliştirme ortamında gerçek veriyle ÇALIŞTIRILAMADI
(Binance verisi ve bağımlılıklar yok). Saf hesap kısmı birim testlidir (`tests/test_short_filter_study.py`).
İlk gerçek çalıştırmada `--check-only` ile başlayın; hata olursa çıktıyı olduğu gibi bildirin.
