"""DCA maliyet ve güvenlik payı sabitleri: TEK yer. Plan (`plan.py`) ve motor (`engine.py`) buradan okur.

Bunlar TAHMİN/GÜVENLİK PAYIDIR, borsadan okunan kesin değer değildir:
- Kayma (slippage): piyasa emirlerinde (taban giriş, stop) fiyat kötü yana kayabilir. Limit emir limit fiyatında ya da daha iyisinde dolar,
  bu yüzden limit kayması varsayılan 0'dır; istenirse yükseltilir.
- Funding: gelecekteki funding bilinmez. Kesin değer gibi varsayılmaz; bunun yerine pozisyonun notional'ına oranla açık bir pay ayrılır.
- Likidasyon: gerçek bakım teminatı tablosu okunmadığı için tahmindir (`MAINTENANCE_RATE_ESTIMATE`). Borsa likidasyon fiyatı okunabiliyorsa
  motor onu ayrıca denetler.
Yüzdeler "%" cinsindendir (0.03 = %0,03); komisyon oranları ondalıktır (0.0005 = %0,05)."""
from __future__ import annotations

MAKER_FEE, TAKER_FEE = 0.0002, 0.0005

# --- kayma payları (%): en kötü zarar ve net kâr hesabına eklenir ---
SLIPPAGE_MARKET_PCT = 0.03          # taban piyasa girişi ve piyasa stop (STOP_MARKET) için kötü yana kayma
SLIPPAGE_LIMIT_ENTRY_PCT = 0.0      # ek (limit) emir girişleri: limit fiyatında ya da daha iyisinde dolar
SLIPPAGE_TP_EXIT_PCT = 0.0          # kâr hedefi limit emirdir; piyasa gibi kayma varsaymıyoruz (>0 yapılırsa net kârdan düşer)

# --- funding payı: kesin gelecekteki funding DEĞİL, açık ve ayarlanabilir güvenlik payı ---
FUNDING_ALLOWANCE_PCT = 0.03        # pozisyonun toplam notional'ının %'si (net kâr ve en kötü zarar hesabına eklenir)

# --- asgari net kâr: ücret + kayma + funding payı düşüldükten sonra bunun altında kalan plan reddedilir ---
MIN_NET_PROFIT_USDT = 0.01
MIN_NET_PROFIT_PCT = 0.02           # ilgili aşamadaki toplam notional'ın %'si; ikisinden büyüğü geçerlidir

# --- likidasyon (TAHMİN) ---
MAINTENANCE_RATE_ESTIMATE = 0.005   # gerçek bakım teminatı tablosu okunmuyor; bu bir varsayımdır, kesin likidasyon fiyatı değildir
LIQ_BUFFER_PCT = 1.0                # stop, (tahmini ya da borsanın bildirdiği) likidasyondan en az bu kadar (ortalamaya göre %) önde olmalı

# --- motor: borsa ↔ defter ortalama giriş uyuşmazlığı ---
AVG_MISMATCH_TOLERANCE_PCT = 0.5    # kısmi hedef dolumu yokken borsa ortalaması defter ortalamasından en çok bu kadar (%) sapabilir
AVG_RANGE_TOLERANCE_PCT = 0.5       # kısmi çıkış varsa: borsa ortalaması turun dolum fiyat aralığının (en düşük..en yüksek) bu kadar dışına çıkamaz

# --- motor: doğrulanmış stop yokken pozisyonun büyümesini engelle ---
# Pozisyon açıkken doğrulanmış koruyucu stop TAMAMEN kapsamıyorsa: (1) yeni ek emir gönderilmez, (2) borsada bekleyen ek emirler iptal edilir ve
# iptal borsada DOĞRULANIR, (3) stop her kontrolde yeniden kurulmayı dener. Bu kadar art arda TUR (tick) kurulamazsa pozisyon piyasadan kapatılır
# (INTERVENTION_REQUIRED yalnız kapatma da doğrulanamazsa son güvenlik durumudur).
STOP_RETRY_TICKS = 3
CANCEL_VERIFY_ATTEMPTS = 3          # bekleyen ek emir iptali için aynı kontrolde en çok bu kadar iptal + durum sorgusu; sonra kapatmaya geçilir
