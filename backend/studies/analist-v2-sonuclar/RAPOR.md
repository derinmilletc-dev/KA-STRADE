# Analist V2 – Yön tahmini geliştirme sonuçları (2026-11-04)

Kapsam: onkayit-v2'nin geliştirme bölümü. Kullanıcı kararıyla ileriye dönük test yapılmadı; tüm sonuçlar
2022-01…2026-04 walk-forward dış-örneklem (17 kat) sonuçlarıdır, "geçmiş veride test edildi" niteliğindedir.
Veri: 30 USDⓈ-M perpetual, 15m OHLCV, 2020-01…2026-04 (2026-05 sonrası okunmadı). Satır: ~1,0 milyon/ufuk.
Maliyet: 16 bp gidiş-dönüş. Güven aralıkları: iki yönlü küme bootstrap (7 günlük blok × coin), B = 2000.

## Özet

| Ufuk | Seçilen | Log loss iyileşmesi (B0'a göre) | AUC_LS | LONG/SHORT isabeti (taban) | Sinyal sıklığı | Kalibrasyon | Maliyet sonrası |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 saat | M2 | +0,0219 [+0,0144, +0,0314] | 0,536 | %37,2 (%26,4) / %37,3 (%26,6) | %7 | Geçti (ECE ≤ 0,004; eğim 0,95–0,99) | −6,1 bp [−18,7, +5,0] |
| 4 saat | M2 | +0,0057 [+0,0036, +0,0083] | 0,518 | %35,5 (%27,8) / %36,3 (%28,6) | %3 | Kaldı (LONG eğimi 0,65) | +9,2 bp [−14,9, +36,1] |
| 24 saat | Yok | +0,0003 [−0,0034, +0,0038] | 0,49–0,50 | — | — | — | −4,8 bp [−59, +54] |

## Bulgular

1. 1 saatlik ufukta istatistiksel olarak gerçek ama küçük bir yön bilgisi var; 17 katın 17'sinde tabandan iyi.
2. Bu bilgi maliyet sonrası kâra dönmüyor: en güvenli %20'lik sinyallerde ortalama −6 bp.
3. Kazanımın büyük kısmı yönden değil volatiliteden ("fiyat hareket edecek mi") geliyor; volatilite grubu çıkarılınca
   1 saatlik log loss 0,0062 kötüleşiyor, diğer grupların katkısı ≤ 0,0003.
4. 1h ve 4h zaman dilimleri ek bilgi katmıyor (yalnız 15m ≈ tüm set).
5. 24 saatlik yön, OHLCV ile tahmin edilemiyor.

## Sapmalar

- İleriye dönük test yapılmadı (kullanıcı kararı); bağımsız test yok.
- Ablation ve MTF merdiveni hesap süresi nedeniyle LR (C = 0,1, kalibrasyonsuz) düzeniyle ölçüldü.
- 1 saatlik çalışma bellek yetersizliğinden bir kez kesildi; olasılıklar önbellekten yeniden kullanıldı, sonuç etkilenmedi.
- Etiket, k_H, özellik, model ve seçim kuralları ön kayıttaki gibi uygulandı.

Ayrıntılı sayılar: `models_1s.json`, `models_4s.json`, `models_24s.json`, `scan.json`, `k_fixed.json`.
