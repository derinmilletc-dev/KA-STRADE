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

## Canlı ile sanal hesap farkları (ölçüldü)
* **Giriş fiyatı:** canlı, emir anındaki anlık fiyatı kullanır ve TP1'i buna göre kurar; sanal, sinyal mumu kapanışını kullanır. Stop ve TP3 iki tarafta da sinyalin mutlak seviyesidir. %2 stop'ta ±5 bps giriş farkı ≈ ±0,055R (testte ölçülür). Yönsüz gürültüdür, iki gruba aynı uygulanır.
* **Miktar:** canlı miktarı 10 adım katına yuvarlar; TP1 miktarı tam %70'tir (test: her adım için). R miktardan bağımsızdır, bu yüzden yuvarlama R'yi değiştirmez; yalnız canlıda minimum notional'a takılma farkı vardır (sanalda yok).
* **Tick:** canlı TP1/stop/TP3'ü tick'e yuvarlar; sanal yuvarlamaz (fark ≤ 1 tick).
* **Gerçek dolum:** canlı gerçek borsa dolumunu kullanır; sanal sabit 5+3 bps modelidir. Çıkış emirleri canlıda STOP_MARKET/TAKE_PROFIT_MARKET (mark fiyatı tetikli) olduğundan her çıkışa kayma uygulanması tutarlıdır; tetikleme sanalda kontrat mumu high/low ile yapılır.
