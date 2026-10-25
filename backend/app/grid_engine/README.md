# Grid motoru (Aşama 2) — SADECE SAHTE BORSA

Canlıya, API rotalarına, bota ya da gerçek/Testnet borsaya **bağlı değil**. Gerçek anahtar/para kullanılmaz.

Testler: `cd backend && python -m unittest tests.test_grid_engine_fake -v`

Davranış: önce deftere yaz, sonra gönder; kimlik = grid+yön+kademe+rol+sürüm; yanıt gelmezse kimlikle sor;
defter↔borsa uyuşmazlığında yeni emir yok; stop dolu miktarı kapsar (yeni stop doğrulanmadan eskisi iptal edilmez);
"Grid'i durdur" = girişler iptal, pozisyon stop/hedefle kalır; "Acil durdur" = girişler iptal → hedefler iptal →
reduceOnly piyasa kapanış → borsadan doğrula → ANCAK SONRA stoplar iptal. Doğrulanamazsa NEEDS_MANUAL, stoplar kalır.

Henüz yok (sonraki adımlar): gerçek borsa adaptörü, borsa filtreleri ve teminat tablosunun sunucudan okunması,
açık pozisyonların stopa kadar zararının portföy riskine eklenmesi, Testnet testleri, uyarıların kullanıcıya iletilmesi,
kademe tamamlanınca yeniden kurma (şimdilik her kademe bir kez).
