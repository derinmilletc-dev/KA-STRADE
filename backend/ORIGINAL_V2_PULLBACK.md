# Original V2 yeni giriş modeli: `pullback_v1`

**Giriş kararı** ve bu modele özgü **stop seviyesi**. TP oranları (R katları), risk, pozisyon büyüklüğü, kaldıraç ve emir yürütme değişmez.

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

Emir öncesi (karar katmanı, emir koduna dokunmaz): canlı fiyat sinyal kapanışından işlem aleyhine 0,4·ATR'den fazla uzaksa aday atlanır (`price_moved`). Ayrıca fiyat stopa doğru kaydıysa ve canlı fiyat–stop mesafesi 0,75·ATR'nin altına indiyse aday yine atlanır (pozisyon miktarı şişmesin).

Sıralama: önce 0,25–1,0 ATR uzaklıkta yeniden güçlenenler; aynı grupta hacim oranı, sonra mevcut fırsat puanı. EMA20'ye sıfıra yakın olmak tek başına kalite sayılmaz.

## Stop (yalnız yeni modelde; sahibin onayıyla)
Stop geri çekilmenin ucuna bağlanır: LONG için son 6 mumun (sinyal mumu dahil) en düşüğünün, SHORT için en yükseğinin 0,15 ATR ötesi; fiyata en az 1 ATR uzak. Eski sistemde stop son 80 mumun ucu ve en az 1,5 ATR idi; o yol değişmez (`entry_levels` varsayılanı 1,5). Hedefler aynı formülle (1R/2R/3R) yeni risk üzerinden hesaplanır; canlı katmandaki TP1 (%80 mesafe, %70 miktar) ve TP3 mantığı değişmez. Risk bütçesi aynı kalır: stop yakınlaşınca pozisyon büyür, dolar kayıp bütçesi aynı kalır. Dolar kazanç artmaz; işlem daha kısa sürede sonuçlanır.

## Değişmeyenler
* Risk bütçesi, pozisyon boyutlandırma formülü, kaldıraç, emir yürütme.
* Güvenlik kapıları aynen çalışır: stop ≤ %6, minimum işlem tutarı, marjin, spread, pozisyon ve aynı yön sınırları, günlük kilitler, arm/sahiplik, yinelenen parite.
* Yalnız bu modülün ürettiği, süreç içi işaretli sonuçlar için `evaluate_entry_gates` içindeki **güven** ve **tuzak** karşılaştırmaları nötrleşir (bu model o puanları üretmez). Diğer tüm kapılar testle "hâlâ reddediyor" olarak doğrulanır.

## Kayıt
Giriş kaydına `entry_model` (`pullback-v1` veya `legacy`) ve her kuralın ölçülen değerleri (`pullback`) yazılır.
