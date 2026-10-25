# Original V2 gölge kayıt

Amaç: canlı stratejiyi değiştirmeden SHORT filtresinin (alignment ≥ 80) reddettiği sinyallerin ileriye dönük sonucunu toplamak.

* **Emir yok.** Modül borsa istemcisi/anahtar kullanmaz; yalnız dosyaya yazar (`ORIGINAL_V2_SHADOW_DIR`, varsayılan `DATA_DIR/original-v2-shadow`).
* **VARSAYILAN KAPALI.** Yalnız `ORIGINAL_V2_SHADOW_ENABLED=1` ile açılır; kapalıyken hiçbir iş parçacığı, dosya veya hesap oluşmaz.
* **Canlıya tek dokunuş:** `live_candidates(..., observer=None)` isteğe bağlı parametre; tarama bitince (tüm kararlardan sonra) korumalı bir çağrı. Hata yutulur.
* **Gruplar:** `FILTER_BLOCKED_SHORT` (yalnız bu filtre reddetti; güven/trap/breakout geçti) ve `LIVE_ELIGIBLE` (canlı adaylar). İkisi aynı sanal modelle izlenir.
* **Sanal çıkış (canlı V2 ile aynı):** TP1 = sinyal TP1 mesafesinin %80'i, %70 kapanır; kalan %30 ilk stop veya TP3; komisyon 5 bps + kayma 3 bps (taraf başına). Aynı mumda stop+hedef: STOP önce. Sonuç R = net / (stop mesafesi + gidiş-dönüş maliyet).
* **Tekrar yok:** kayıt anahtarı `grup|sembol|sinyal mumu`; yeniden başlatmada da korunur.
* **Dosyalar:** `signals.jsonl` (puanlar, EMA/MACD/RSI, fiyat konumu, HTF, trap, breakout), `trades.jsonl` (giriş, stop, hedef, kapanış nedeni, komisyon, R), `state.json`, `status.json`.
* **Rapor:** `python original_shadow_report.py`.

## Sınırlamalar
Sanal işlemler portföy/teminat/günlük limit kapılarına tabi değildir (minimum miktar ve stop tavanı yalnız `live_openable` olarak işaretlenir); (grup, sembol) başına tek açık işlem; tetikleme fiyat mumu high/low (çevrimdışı model mark fiyatı kullanır); ilk mumda giriş anından önceki birkaç saniyelik hareket de sayılır (ihmal edilebilir, bilinen yaklaşım); eksik mum varsa işlem `DATA_GAP` olarak çözülmemiş sayılır. Az veri kanıt değildir.

## Giriş fiyatı modeli
* **Referans:** tarama anındaki son fiyat = kline getirildiği an oluşmakta olan 15m mumun son fiyatı (`SCAN_LAST_PRICE`). Canlı emir ondan saniyeler sonra, anlık fiyattan girer; ek ağ çağrısı yoktur. İki grup (reddedilen SHORT ve canlı adaylar) aynı taramadan, aynı yöntemle girer.
* **Seviyeler canlıyla aynı:** giriş/stop/TP2/TP3 tick'e yuvarlanır (ROUND_HALF_UP), TP1 = `round_tick(giriş + (sinyal TP1 − giriş) × 0,80)`, canlıdaki kesin sıralama (stop < giriş < TP1 < TP2 < TP3) bozulursa canlı girmezdi, sanal da girmez. Testte rastgele fiyat ve tick'lerde canlı kod ile birebir karşılaştırılır.
* **Tarama fiyatı yoksa/güvenilmezse** (mum zamanı uyuşmuyor ya da sinyal kapanışından %2'den uzak) giriş = sinyal kapanışı ve işlem `APPROXIMATE_ENTRY` ile işaretlenir.
* **Ölçülüp raporlananlar (her sinyalde):** tarama–kapanış farkı (bps), giriş ve TP1 tick yuvarlama farkı (bps), canlıda minimum miktar/notional veya stop tavanına takılıp takılmayacağı (`live_openable`; canlının kendi hesabı, tarama fiyatıyla). Rapor, tüm işlemleri ve yalnız canlıda açılabilenleri ayrı ayrı gösterir.
* **Toplanmayanlar (yeni ağ çağrısı veya emir akışına bağlantı gerektirir, bu PR'da yok):** mark fiyatı–mum fiyatı farkı ve gerçekleşen dolum fiyatı farkı. Kayıtlarda `null` ve `not_collected` olarak durur; tahmin edilmez.

## Canlı ile sanal hesap farkları (ölçüldü)
* **Miktar:** canlı miktarı 10 adım katına yuvarlar; TP1 miktarı tam %70'tir (test: her adım için). R miktardan bağımsızdır.
* **Gerçek dolum:** canlı gerçek borsa dolumunu kullanır; sanal sabit 5+3 bps modelidir. Çıkış emirleri canlıda STOP_MARKET/TAKE_PROFIT_MARKET (mark fiyatı tetikli) olduğundan her çıkışa kayma uygulamak tutarlıdır; tetikleme sanalda kontrat mumu high/low ile yapılır.
* **Giriş duyarlılığı:** %2 stop'ta ±5 bps giriş farkı ≈ ±0,055R (testte ölçülür).


## Yeni SHORT filtresiyle uyum (PR #238)

`FILTER_BLOCKED_SHORT` grubu iki rejimde de çalışır: eski kuralda `mtf.blocked_by_short_filter`, yeni canlı filtrede
`mtf.short_filter_v2.accepted == False` ve `gates_before_passed == True` (diğer kapılar geçmişse). Her SHORT kaydında
`short_filter` alanı vardır: `regime` (`LEGACY_ALIGNMENT` / `EXHAUSTION_V2`), `reason(s)`, ölçümler (`extension_atr`,
`support_distance_atr`, `rsi`) ve `legacy_would_block` (eski %80 kuralı bunu engellerdi mi). Özette ret nedeni dağılımı ve
"eski kural engellerdi" alt grubu ayrıca görünür. `SHORT_SUPPORT_UNAVAILABLE` (veri yok) ile `SHORT_SUPPORT_TOO_CLOSE`
(gerçekten yakın) ayrı nedenlerdir.
