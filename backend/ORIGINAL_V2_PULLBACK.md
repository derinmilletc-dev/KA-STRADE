# Original V2 yeni giriş modeli: `pullback_v1`

Yalnız **giriş kararı**. Stop, TP1/TP2/TP3, risk, pozisyon büyüklüğü, kaldıraç ve emir yürütme değişmez.

## Açma / kapama
`PROTREBOT_ORIGINAL_V2_ENTRY_MODEL`

| Değer | Sonuç |
|---|---|
| (boş) / `legacy` | Eski giriş sistemi, aynen (varsayılan) |
| `pullback_v1` | Yeni model |
| başka herhangi bir şey | Tarama durur (fail-closed) |

Yalnız canlı Original V2 taraması okur. Demo, backtest ve araştırma yolları değişmez.

## Kurallar (LONG ve SHORT aynı; sayılar başlangıç değerleridir, doğrulanmamıştır)
`d` = +1 LONG, −1 SHORT. `t` = son kapanmış 15 dakikalık mum.

| Anahtar | Kural |
|---|---|
| `pb_trend` (K1) | 1h ve 4h yönü `d` (mevcut `shared_mtf_decision`, güven eşiği yok) |
| `pb_structure` (K2) | `d·(EMA20 − EMA50) ≥ 0,15·ATR` (yön buradan gelir) |
| `pb_pullback` (K4) | Son 6 mumda (t dahil) fiyat EMA20 bölgesine değdi (±0,25·ATR) |
| `pb_trigger` (K5) | t mumunda: kapanış EMA20'nin trend tarafında + önceki mumun ucunu geçti + kapanış mumun doğru %40'lık ucunda + MACD histogramı trend yönünde döndü |
| `pb_extension` (K6) | `d·(kapanış − EMA20) ≤ 1,25·ATR` |
| `pb_room` (K7) | Önceki 80 mumun (t **hariç**) karşı ucuna ≥ 0,5·ATR. Kapanış bu uçu geçmişse (mesafe negatif) reddedilir |

Emir öncesi (karar katmanı, emir koduna dokunmaz): canlı fiyat sinyal kapanışından işlem aleyhine 0,4·ATR'den fazla uzaksa aday atlanır (`price_moved`).

Sıralama: önce 0,25–1,0 ATR uzaklıkta yeniden güçlenenler; aynı grupta hacim oranı, sonra mevcut fırsat puanı. EMA20'ye sıfıra yakın olmak tek başına kalite sayılmaz.

## Değişmeyenler
* Stop ve hedefler `analysis.entry_levels` ile hesaplanır; eski `analyze` da aynı fonksiyonu kullanır (testle birebir doğrulanır).
* Güvenlik kapıları aynen çalışır: stop ≤ %6, minimum işlem tutarı, marjin, spread, pozisyon ve aynı yön sınırları, günlük kilitler, arm/sahiplik, yinelenen parite.
* Yalnız bu modülün ürettiği, süreç içi işaretli sonuçlar için `evaluate_entry_gates` içindeki **güven** ve **tuzak** karşılaştırmaları nötrleşir (bu model o puanları üretmez). Diğer tüm kapılar testle "hâlâ reddediyor" olarak doğrulanır.

## Kayıt
Giriş kaydına `entry_model` (`pullback-v1` veya `legacy`) ve her kuralın ölçülen değerleri (`pullback`) yazılır.
