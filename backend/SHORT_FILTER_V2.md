# Original V2 yeni SHORT giriş filtresi (yalnız canlı giriş)

**Kaldırılan:** üç zaman diliminin ağırlıklı uyumu ≥ %80 ise SHORT'u otomatik reddetme — yalnız `evaluate_live_universe` yolunda.
**Korunan:** MTF hesabı (15m %25, 1s %35, 4s %40), 1s ve 4s yön onayı, güven ≥ 80, tuzak ≤ 35, kırılım ≥ 50, LONG, stop/TP/risk/emir mantığı.

## Yeni kurallar (`main.evaluate_short_entry_filter`, saf hesap)
| Kural | Eşik | Ret nedeni |
|---|---|---|
| Fiyat EMA20'nin ATR cinsinden altında | ≥ 2,0 ATR | `SHORT_EXTENDED_BELOW_EMA20` |
| RSI | < 25 | `SHORT_RSI_OVERSOLD` |
| En yakın destek mesafesi | ≤ 0,5 ATR | `SHORT_SUPPORT_TOO_CLOSE` |
| Destek verisi yok/geçersiz (ATR, EMA20, RSI sağlam) | — | `SHORT_SUPPORT_UNAVAILABLE` (uydurma destek yok; "desteğe yakın" ile karıştırılmaz) |
| Diğer gerekli veri yok/geçersiz | — | `SHORT_FILTER_DATA_UNAVAILABLE` (varsayılan değer üretilmez, giriş reddedilir) |

Giriş zamanlaması (E) zorunlu kural değildir; eklenmedi (işlem sayısını gereksiz düşürmemek için).

Sınırlar: EMA20 mesafesi tam olarak `(EMA20 - fiyat) / ATR >= 2`; ATR oranı karşılaştırmalarında 1e-9 tolerans sınırda **engelleme** yönündedir (kayan nokta gürültüsü bir SHORT'u yanlışlıkla geçirmez).

## Kapsam
`canonical_historical_decision(..., short_filter_rules=None)`: varsayılan davranış aynen eski. Kural yalnız `original_v2_demo.evaluate_live_universe` tarafından verilir (`LIVE_SHORT_FILTER_RULES`). Demo (`evaluate`), backtest, araştırma ve paper yolları eski %80 kuralıyla kalır. Çağıran taraf kural enjekte edemez (`CALLER_SHORT_FILTER_OVERRIDE_DENIED`).
Akıllı Kâr Koruma aynı fonksiyondan yalnız `analysis` (puan) okur; analiz canlı ve demo'da birebir aynıdır.

## Log
Filtrenin karar verdiği her SHORT adayı için tek satır: `ORIGINAL_V2_SHORT_FILTER symbol decision reason alignment dir15m dir1h dir4h rsi extension_atr support_distance_atr legacy_alignment_would_block version`.
Karar sonucunda `legacy.mtf.short_filter_v2` (ölçümler) ve kalite kontrolü `short_filter` bulunur; ret nedeni `reason` alanındadır.

Tarama başına özet: `ORIGINAL_V2_SHORT_FILTER_SCAN` satırı (`short_filter_counts`: kabul, toplam ret, nedene göre ret; `SHORT_SUPPORT_UNAVAILABLE` ayrı sayılır).

## PR #237 (gölge kayıt) uyumu
Gölge kayıt `mtf.short_filter_v2.accepted == False` ve `gates_before_passed` ile "filtre engelledi" grubunu bu filtreyle de tanır; kayıtta ret nedeni ve `legacy_would_block` bulunur.

## Not
`mtf.blocked_by_short_filter` canlıda artık hep `False`'tur; eski kuralın "olsaydı engellerdi" bilgisi `legacy_short_alignment_would_block` alanındadır.
