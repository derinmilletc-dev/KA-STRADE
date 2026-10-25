# Original V2 gölge kayıt

Amaç: canlı stratejiyi değiştirmeden SHORT filtresinin (alignment ≥ 80) reddettiği sinyallerin ileriye dönük sonucunu toplamak.

* **Emir yok.** Modül borsa istemcisi/anahtar kullanmaz; yalnız dosyaya yazar (`ORIGINAL_V2_SHADOW_DIR`, varsayılan `DATA_DIR/original-v2-shadow`).
* **Kapatma:** `ORIGINAL_V2_SHADOW_ENABLED=0`.
* **Canlıya tek dokunuş:** `live_candidates(..., observer=None)` isteğe bağlı parametre; tarama bitince (tüm kararlardan sonra) korumalı bir çağrı. Hata yutulur.
* **Gruplar:** `FILTER_BLOCKED_SHORT` (yalnız bu filtre reddetti; güven/trap/breakout geçti) ve `LIVE_ELIGIBLE` (canlı adaylar). İkisi aynı sanal modelle izlenir.
* **Sanal çıkış (canlı V2 ile aynı):** TP1 = sinyal TP1 mesafesinin %80'i, %70 kapanır; kalan %30 ilk stop veya TP3; komisyon 5 bps + kayma 3 bps (taraf başına). Aynı mumda stop+hedef: STOP önce. Sonuç R = net / (stop mesafesi + gidiş-dönüş maliyet).
* **Tekrar yok:** kayıt anahtarı `grup|sembol|sinyal mumu`; yeniden başlatmada da korunur.
* **Dosyalar:** `signals.jsonl` (puanlar, EMA/MACD/RSI, fiyat konumu, HTF, trap, breakout), `trades.jsonl` (giriş, stop, hedef, kapanış nedeni, komisyon, R), `state.json`, `status.json`.
* **Rapor:** `python original_shadow_report.py`.

## Sınırlamalar
Sanal işlemler portföy/teminat/günlük limit/minimum miktar kapılarına tabi değildir; (grup, sembol) başına tek açık işlem; giriş sinyal mumu kapanışı; tetikleme fiyat mumu high/low (çevrimdışı model mark fiyatı kullanır); eksik mum varsa işlem `DATA_GAP` olarak çözülmemiş sayılır. Az veri kanıt değildir.
