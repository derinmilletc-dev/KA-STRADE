# Sınırlar (tek doğru kaynak: kod)

Bu tablo koddaki sabitlerden alınmıştır. Bir sayı değişirse önce kodu, sonra bu dosyayı güncelleyin.

| Konu | Değer | Kaynak |
|---|---|---|
| Bir gridin kademe sayısı | 2–20 | `backend/app/manual_grid.py` (`MAX_LEVELS`), `backend/app/grid_live.py` (`LIVE_MAX_LEVELS`), `studio-grid.ts` (`GRID_MAX_COUNT`) |
| Aynı anda çalışan grid (kullanıcı başına, coin başına bir) | 5 | `backend/app/grid_live.py` (`MAX_GRIDS_PER_USER`) |
| Canlı grid | Varsayılan AÇIK; `KAISTRADE_GRID_LIVE=0` ile kapanır (stop / acil kapat kapalıyken de çalışır) | `backend/app/grid_live.py` (`live_enabled`) |
| Analist kredisi (premium olmayan) | Günde 5 analiz = 50 kredi, analiz başı 10 | `backend/app/analyst_credits.py` |
| Chart AI | Ücretsiz toplam 3, deneme günde 5, Smart Trade günde 10, Master Mode günde 15 | `backend/app/chart_ai_store.py` |
| Toplam marjin sert tavanı | 300 USDT | `execution_core.py` (`HARD_MAX_MARGIN_USDT`) |

Otomatik/yarı otomatik araçlar: Kais Original V2 (ana otomatik strateji), Grid, DCA. Her birinin kendi limitleri vardır.
