# KaisTrade — Google Play (Android) hazırlık raporu

Durum (5. tur): Paketleme hazırlığı yapıldı (Windows rehberi `ANDROID-YEREL-DERLEME.md`). Hesap silme ve Android ödeme/Kais AI korumaları KOD olarak yazıldı; backend testleri çalıştırılamadığı için DOĞRULANMIŞ SAYILMAZ. Android projesi/AAB ÜRETİLMEDİ (bu ortamda npm, Maven ve Google depolarına erişim yok; Android SDK ve Bubblewrap kurulamadı). Commit/push/deploy/Play yüklemesi yapılmadı.

## 1. Seçilen mimari: TWA (Trusted Web Activity)
Gerekçe (kod doğrulamalı):
- Oturum çerezleri `HttpOnly; SameSite=Lax; path=/api` (`browser_security.py`). Uygulama kaistrade.com origin'inde çalışmalı; yerel dosya (capacitor://localhost) çerezi çapraz-site yapar ve oturum bozulur.
- Google girişi sunucu taraflı web akışı (`POST /auth/google/start` → accounts.google.com → `/api/v22/auth/google/callback` → `https://kaistrade.com/?…` 303). Google, gömülü WebView'de OAuth'u engeller; TWA gerçek Chrome olduğu için bu engel yok.
- Sesli asistan `webkitSpeechRecognition` kullanıyor (`assistant-voice.ts`); Android WebView'de yok, Chrome'da (TWA) var.
- Web Push (`sw.js`) WebView'de çalışmaz, TWA'da Chrome üzerinden çalışır.
- Capacitor'a GEÇİLMEDİ (onay şartı). Capacitor gerekirse: Custom Tab + App Link + backend devir kodu, yerel konuşma tanıma, FCM, dosya indirme eklentileri gerekir (auth/ses/bildirim değişikliği).

TWA engelleri / riskleri (raporlanan):
1. Google OAuth TWA'da çalışıyor mu: STATİK analiz uyumlu, CİHAZDA DOĞRULANMADI (aşağıda).
2. Play "minimum işlevsellik / web sarmalayıcı" incelemesinde ret riski (sahibi gönderiyor; yine de risk var). Ret gelirse Capacitor için onay istenecek.
3. TWA'da Digital Asset Links zorunlu: `https://kaistrade.com/.well-known/assetlinks.json` Play App Signing SHA-256 parmak iziyle yayınlanmalı. Parmak izi Play Console'dan gelir; bu yüzden dosya EKLENMEDİ (sahte parmak izi koymamak için).
4. Chrome yüklü/etkin değilse TWA yerine Custom Tab'a düşer (nadir).

## 2. Google OAuth — TWA doğrulaması
Yapılan (statik, `google_oauth.py` + `AuthGate.tsx`):
- Akışta User-Agent kontrolü yok; `Origin` başlığı `https://kaistrade.com` olmalı (`ORIGINS`, `trusted_origin`) — TWA içeriği bu origin'den geldiği için uyumlu.
- Bağlama çerezi `SameSite=Lax`, callback üst düzey GET navigasyonu → Lax çerez gönderilir. TWA ile Chrome aynı çerez havuzunu paylaşır.
- Başlangıç URL'si yalnızca `accounts.google.com/o/oauth2/v2/auth` ise yönlendirilir (istemci doğrulaması) — değişmedi.
Yapılamayan: gerçek cihaz/Google hesabıyla uçtan uca test. Çalıştırılabilir test: Google, ağ ve cihaz gerektirir.
Cihazda kontrol listesi: (a) "Google ile giriş" → Google ekranı açılıyor mu, (b) hesap seçince uygulamaya dönülüyor mu (Custom Tab kapanıyor mu), (c) oturum açık mı (çerez), (d) 2FA'lı hesapta `mfa_challenge` yolu, (e) çıkış yapınca oturum kapanıyor mu, (f) uygulamayı kapat-aç, oturum sürüyor mu.
Çalışmazsa güvenliği koruyan seçenekler: (1) Girişi uygulama içinde e-posta+parola ile bırakmak (zaten var), (2) Google'ı Android'de gizlemek, (3) yerel Google Sign-In (Credential Manager) ile ID token → yeni backend uç noktası (nonce/audience doğrulaması mevcut `validate_identity` ile; yeni çerez bağlaması gerekir) — onay şart, (4) Capacitor + Custom Tab + tek kullanımlık devir kodu.

## 3. Ödemeler (karar 2) — uygulandı
Android uygulamasında yeni dijital abonelik satın alma yüzeyleri gizlenir; web ödeme sistemi ve backend DEĞİŞMEDİ.
- Algılama (`android-app.ts`): referrer `android-app://…` veya `?source=android-app`; yalnızca `sessionStorage`'a yazılır (normal Chrome oturumlarına sızmaz).
- Android'de gizlenen/engellenenler: Abonelik menü düğmeleri (2), `/pricing` ve `/billing` rotaları ve ekranları (SubscriptionCenter/BinanceManualPayment erişilemez), Master Trade kilidindeki "UPGRADE TO PREMIUM", "Premium'a geç" bağlantıları (premium-access, ChartAiPanel), canlı bağlantı kartındaki "Abonelik sayfası", MobileHome plan kartındaki "Yükselt".
- Web davranışı: `isAndroidApp()` false → hiçbir şey değişmez.
Mevcut abone erişimi: Giriş yapan mevcut Master Mode/deneme üyesi için Master Trade, analiz, grafikler çalışır (yetki sunucudan gelir, değişmedi).
Play politikası (https://support.google.com/googleplay/android-developer/answer/9858738): uygulama içi ücretli özellik/abonelik Play Faturalandırma kullanmalı; kullanıcıyı başka ödeme yöntemine yönlendiren buton/bağlantı/webview/kayıt akışı yasak (§4). Başka yerde satın alınmış erişimi tüketmek bu sayfada açıkça yasaklanmamış ama açıkça izinli de değil → "uygun" demiyorum; Play inceleme sonucuna bağlı. Kalan riskler: (a) Kais AI: ÇÖZÜLDÜ (bkz. §3b), (b) Premium kilit penceresi "Premium üyeliğe dahildir" bilgisini gösterir ama bağlantısız, (c) hesap ekranındaki mevcut paket bilgisi görünür, (d) Destek ve e-postalar ödemeyi anlatabilir. 30 Haziran 2026'dan itibaren ABD/UK/AEA'da harici faturalandırma programı var (https://9to5google.com/2026/06/24/google-play-store-external-billing-june-30/); Türkiye/Güney Afrika için kapsam doğrulanmadı.

## 3b. Android'de Kais AI plan/fiyat anlatımı — uygulandı
Uygulama içinde plan fiyatı, paket, satın alma yolu veya siteye yönlendirme anlatmak Play'in ödeme politikasına (§4: başka ödeme yöntemine yönlendirme yasak) aykırı olabilir. Önlem yalnızca Android'de (istemci `platform:"android"` gönderir; web varsayılanı `"web"`):
- `ChatInput.platform` (`Literal["web","android"]`).
- Hızlı yol: plan/fiyat soruları modele ve araca gitmeden "Bu bilgiyi uygulamada veremiyorum." döner.
- Model: `get_plans` aracı sunulmaz; sistem istemine Android kuralı eklenir (fiyat/paket/satın alma/ödeme/yönlendirme anlatma); model yine de `get_plans` çağırırsa istek durdurulur, araç çalışmaz.
- Yardım makaleleri `plans`, `billing`, `premium` Android'de arama sonuçlarından çıkarılır.
- Kullanıcının kendi hesabındaki mevcut erişim (`get_my_access`) ve kredi kullanımı anlatılabilir.
- Web davranışı: `platform` verilmezse varsayılan `web`; web istemi/araçları değişmedi (testle doğrulanacak şekilde yazıldı, çalıştırılamadı).
Güvenlik sınırı DEĞİL: `platform` istemciden gelir, sunucu bunu doğrulayamaz (TWA, Chrome'dan ayırt edilebilir bir sunucu işareti göndermez). Kısıtlama Play içerik politikası uyumu içindir; yetki/ödeme/kota kararlarında kullanılmaz (kodda yalnızca araç/istem/hızlı yanıt seçer). Atlatmak yalnızca zaten herkese açık plan bilgisini gösterir. Yanıt önbelleği yok; Android ve web yanıtları karışmaz.
Kalan: sesli/yerel `kais-agent-intents` "paketler/fatura" komutları Android'de ana ekrana düşer (satın alma ekranı açılmaz) ama asistan yine de "açıyorum" diyebilir; destek ve e-posta içerikleri ayrıca gözden geçirilmeli.
Ek hata düzeltmesi (benim önceki işimde): Kais akıl yürütme PR'ında `run_conversation` döngüsü `LLMRequest`'i yeniden kurarken `effort` alanını düşürüyordu; bu yüzden "medium" düşünme hiçbir istekte uygulanmıyordu. `request.effort` aktarılarak düzeltildi ve test eklendi.

## 4. Hesap silme — uygulandı (kod hazır, test ÇALIŞTIRILMADI)
Mevcut durum (kod okuma): "Hesabı kapat" (`POST /account/close`) yalnızca devre dışı bırakır, kayıtlar saklanır → Play'in veri silme şartını karşılamaz. `DELETE /api/v22/profile` gerçek silmeyi yapar (`erase_user_account`) ama yeniden doğrulama/onay/açık pozisyon kontrolü yoktur ve arayüzden çağrılmaz; DOKUNULMADI (bkz. §11 risk).
Yapılan:
- `POST /api/v22/account/delete` (`account_settings.py`): oturum + OWNER engeli + `close_blocker` (açık pozisyon/emir/otomasyon/borsa bağlantısı → 409) + `proof` (parola veya e-posta kodu + varsa TOTP; yanlışsa 401) + yazılı onay `HESABIMI SIL` (yanlışsa 422, fazladan alan 422). Hepsi geçerse `erase_user_account` TAM BİR KEZ çalışır, oturum çerezi temizlenir, `{ok, reauthenticate}` döner.
- `push_subscriptions` silme listesine eklendi (`account_erasure.PERSONAL_TABLES`): kullanıcının cihaz abonelikleri silinir ve silinen kullanıcı için yenisi eklenemez (koruma tetikleyicisi).
- Arayüz (`ProfileSettings.tsx`): Tehlikeli işlemler'de "Hesabı ve verilerimi sil" satırı + iletişim kutusu (geri alınamaz uyarısı, anonim mali kayıt bilgisi, kanıt alanları, HESABIMI SIL onayı). Web ve Android aynı ekranı kullanır; web'deki mevcut "Hesabı kapat" aynen durur.
- Testler yazıldı: `test_account_settings.py` (409/422/401 yolları silmeyi çalıştırmaz, doğru kanıtla bir kez çalışır) ve `PERSONAL_TABLES` kontrolü; mevcut `ErasureStore` testi `push_subscriptions`'ı otomatik kapsar. Hiçbiri bu ortamda ÇALIŞTIRILAMADI (Playwright/npm paketleri de yok: güncellenen `seo-static-files`, `public-policies` spec'leri ve metadata fixture'ı; ayrıca fastapi/asyncpg yok).
Silme ve saklama davranışı (yerel PostgreSQL 16'da gerçek migrasyonlarla DOĞRULANDI, üretim verisi yok):
- Tombstone (`commercial_erased_users`) eklenince veritabanı tetikleyicileri: `member_free_trials` satırı `user_id='ERASED'` + iptal; `support_cases` konu/mesaj/yanıt notu boşaltılır (satırdaki `user_id` KALIR); `notification_outbox` alıcı `ERASED`, yük `{}`; `customer_events`, `email_preferences`, `campaign_test_limits`, `campaign_recipients` silinir; ödeme istekleri ve `binance_manual_periods` `ERASED` olarak anonimleşir (kod okuma; bu ikisi veritabanında çalıştırılmadı).
- `push_subscriptions` uygulama kodu tarafından silinir; başkasının aboneliği korunur (test edildi).
- Silinmiş kullanıcı için yeni deneme/kimlik satırı eklenemez (test edildi); tekrar tombstone eklemek zararsız.
- Anonim bırakılanlar (mali/işlem/denetim kanıtı) kasıtlı: `subscriptions`, ödeme istekleri, işlem kanıtları, denetim kaydı. Kimlik yerine `ERASED` yazılır.
- Kararınızı bekleyenler: `support_cases.user_id`, `audit_log.actor_user_id`, `chart_ai_*` ve `member_free_trials.user_hash` (kimliğin SHA-256 özeti) silinmiyor; takma ad niteliğinde. Sunucu günlükleri, yedekler ve borsadaki emirler kapsam dışı. Gizlilik politikası ve Play Veri Güvenliği formu bunu açıklamalı. Bu tetikleyiciler yalnızca ilgili migrasyonlar uygulanmışsa vardır; üretimde uygulandığı bu ortamdan doğrulanamaz (`tools/db_check.py` ile kontrol edilmeli).
- Eski `DELETE /api/v22/profile` (4. tur): ÇÖZÜLDÜ. Arayüz/test dışında çağıranı yoktu; artık oturum doğrular ve 410 Gone döner, hiçbir silme çalışmaz, oturum bozulmaz. Kalıcı silme yalnızca `POST /api/v22/account/delete`. Bu yol: eşzamanlı istek koruması (aynı hesap için ikinci istek 409; kilit başarı/hatada bırakılır), açık pozisyon/emir/borsa blokajı kanıttan ÖNCE ve geri alınamaz adımdan hemen ÖNCE tekrar kontrol edilir. Kalan küçük pencere: ikinci kontrol ile silme arasında milisaniyeler (blokaj `erase_user_account` içinde kilit altında değil). Not: owner'ın `DELETE /admin/users/{id}` yolu aynı blokajı uygulamaz (yönetici kararı, DEĞİŞTİRİLMEDİ).
Web silme URL'si (Play): `https://kaistrade.com/hesap-silme` eklendi (herkese açık, girişsiz, `privacy` ile aynı altyapı: önceden üretilen `hesap-silme.html`, başlık/canonical, `vercel.json` ve `frontend/vercel.json` yönlendirmeleri). İçerik uygulamadaki gerçek akışı anlatır. DOĞRULANMAMIŞ ve yer tutucu olarak bırakılanlar: `[GİZLİLİK E-POSTASI]` (girişsiz silme talebi adresi), `[VERİ SORUMLUSU / HUKUKİ UNVAN]`, `[SAKLAMA SÜRELERİ]` → yayından önce doldurulmadan Play'e verilmemeli. Sitemap'e eklenmedi (sitemap testleri değişmesin diye).

## 4c. assetlinks.json ve SPA yönlendirmesi
İki yapılandırmada da (`vercel.json`, `frontend/vercel.json`) SPA yedek kuralı `.well-known/` yollarını artık yakalamıyor ve `assetlinks.json` için `Content-Type: application/json` başlığı var. Dosya yokken istek HTML değil 404 döner. Dosyanın kendisi parmak iziniz gelene dek eklenmedi; eklendiğinde biçim/yer tutucu kontrolü `tools/vercel-routing.test.mjs` içinde.

## 5. Ülkeler — finansal özellikler ve lisans (karar 4; ülkeler KESİNLEŞTİRİLMEDİ)
Hukuki tavsiye değildir; yerel avukat/uyum danışmanı onayı gerekir. KaisTrade borsa/cüzdan değil; analiz + sinyal + kullanıcının kendi Binance API anahtarıyla işlem yürütme ("Master Trade", otomasyon). Emir iletimi/otomasyon kısmı "kripto varlık hizmeti" veya "aracılık/danışmanlık" sayılabilir mi sorusu avukata sorulmalı.
- Play genel: Uygulama içeriği > Finansal özellikler beyanı zorunlu; borsa/yazılım cüzdanı beyanı ülkeye özel lisans ister, lisans yoksa o ülke hedeflenmemeli (https://support.google.com/googleplay/android-developer/answer/16329703).
- **Güney Afrika**: Play listesinde: FSCA kaydı (borsa). Hukuk: kripto varlıklar 19.10.2022'de finansal ürün ilan edildi (Genel Duyuru 1350); CASP lisansı 1 Haziran 2023'ten beri; ticaret platformu, cüzdan ve YATIRIM DANIŞMANLARI CASP lisansı almalı; FSCA lisanssız faaliyete karşı soruşturma yürütüyor (https://www.dlapiperafrica.com/en/south-africa/insights/2026/FSCA_Update_on_Licensing_and_Supervision_of_Crypto_Asset_Service_Providers). Sinyal/analizin kapsamı kaynakta ele alınmıyor → belirsiz; kişiselleştirilmiş tavsiye FAIS kapsamına girebilir. Lisans doğrulanmadan hedeflenmemesi önerilir.
- **Türkiye**: Play listesinde YOK; yayın yapılabilir ama yerel lisans yükümlülüğü geliştiricide, Google ek bilgi isteyebilir. Hukuk: 7518 sayılı Kanun (02.07.2024) ile kripto varlık hizmet sağlayıcıları SPK izin/denetimine tabi (alım-satım, takas, transfer, saklama); izinsiz faaliyet 109/A: 3-5 yıl hapis; yurt dışı platform için Türkçe site/tanıtım "Türkiye'ye yönelik" ölçütü (https://spk.gov.tr/duyurular/basin-duyurulari/2024/kripto-varlik-hizmet-saglayicilara-iliskin-duyuru_02072024). Sinyal/danışmanlık hakkında kaynakta bilgi yok → belirsiz. Türkçe arayüz + Türkiye pazarlaması bu ölçüte girebilir → avukat görüşü şart.
- **ABD/AB/UK/Kanada/Japonya vb. (Play listesinde)**: borsa/cüzdan için kayıt/lisans gerekli (AB: MiCA CASP; ABD: FinCEN MSB + eyalet; UK: FCA). Lisans yoksa hedeflenmemeli.
- Diğer pazarlar: tek tek doğrulanmadı.

## 6. Play gereklilikleri
- Target API: 31.08.2026'dan beri yeni uygulama/güncellemeler API 36+ (https://developer.android.com/google/play/requirements/target-sdk). Bubblewrap'in güncel sürümünün hedef SDK'sı kurulum sırasında doğrulanmalı.
- applicationId önerisi `com.kaistrade.app`; repoda mevcut Android paketi yok. Play Console benzersizliği doğrular.
- Beyanlar: Gizlilik URL (https://kaistrade.com/privacy), Veri güvenliği formu, Finansal özellikler, Uygulama erişimi (test hesabı: e-posta doğrulama + 2FA notu), hedef kitle, hesap silme URL'si (bkz. §4), kapalı test şartı (yeni bireysel hesap).

## 7. TWA paketini üretme
Tek kaynak: `ANDROID-YEREL-DERLEME.md` (komutlar, assetlinks, doğrulama, cihaz listesi). Bu belgede ayrıca komut tutulmaz; iki belge arasındaki çelişkiler (anahtar takma adı, Node sürümü, SDK kurulumu, parmak izi kaynağı) bu şekilde giderildi.

## 8. Yapılan değişiklikler (çalışma kopyası, commit YOK)
Yeni: `android-app.ts`, `ANDROID-HAZIRLIK.md`, `ANDROID-YEREL-DERLEME.md`, `tools/vercel-routing.test.mjs`. 4. tur ek değişenler: `backend/app/v22_commercial.py` (legacy DELETE → 410), `backend/tests/test_security_auth_hardening.py`, `compliance-content.ts`, `public-page-metadata.ts`, `PublicPolicyPage.tsx`, `tools/public-policy-html.ts`, `vercel.json`, `frontend/vercel.json`, `frontend/tests/{seo-static-files,public-policies}.spec.ts`, `frontend/tests/fixtures/public-page-metadata.tsx`. Değişen: `.gitignore`, `TestnetFirstApp.tsx`, `premium-access.tsx`, `ChartAiPanel.tsx`, `LiveConnectionCard.tsx`, `MobileHome.tsx`, `ProfileSettings.tsx`, `AssistantChat.tsx`, `backend/app/account_settings.py`, `backend/app/account_erasure.py`, `backend/app/assistant_models.py`, `backend/app/assistant_llm.py`, `backend/app/assistant_fastpath.py`, `backend/app/assistant_api.py`, `backend/tests/test_account_settings.py`, `backend/tests/test_assistant_reasoning.py`. Ödeme, abonelik, veritabanı şeması değişmedi.

## 9. Test durumu (gerçek)
ÇALIŞTIRILDI ve geçti: `node --test tools/vercel-routing.test.mjs` (8/8; `.well-known` istisnası kaldırılınca 1 test düşüyor, yani test gerçekten koruyor); `/hesap-silme` için `renderPublicPageHtml` ve yol eşleştirme (başlık/canonical/og:url doğru, `/Hesap-silme` ve `/hesap-silme/x` eşleşmiyor); `android-app.ts` mantığı (node); değişen TS/TSX dosyalarının esbuild derlemesi; değişen tüm Python dosyalarının sözdizimi; `account_erasure.scrub_payload/scrub_commercial_state` (fastapi stub'ıyla, mali kanıt anonimleşiyor, diğer kiracılar korunuyor, `push_subscriptions` listede); `needs_deep_reasoning` (effort testinin dayanağı); yerel PostgreSQL 16'da gerçek migrasyonlarla silme tetikleyicileri (bkz. §4).
ÇALIŞTIRILAMADI (fastapi/pydantic/anthropic/asyncpg/pyotp yok, PyPI erişimi yok): `test_account_settings.py` ve `test_assistant_reasoning.py` içindeki yeni testler dahil tüm pytest/unittest testleri; `/account/delete` HTTP akışı; frontend production build; e2e; Android derleme/AAB; Google OAuth; cihaz testleri. Bunlar test edilmiş sayılmaz; CI'daki ilk çalıştırma gerçek sonucu verecek.

## 10. Çözülmemiş riskler (ayrı liste)
Lisans/ülke: (1) Hedef ülkeler seçilmedi. (2) Güney Afrika: FSCA CASP lisansı gereği, sinyal/analiz kapsamı belirsiz. (3) Türkiye: SPK izni (7518), Türkiye'ye yönelik Türkçe pazarlama ölçütü, sinyal/danışmanlık kapsamı belirsiz. (4) Emir iletimi/otomasyonun hukuki sınıfı. (5) Play finansal özellikler beyanı ve diğer pazarlar (AB/ABD/UK…) için lisans doğrulanmadı. Avukat görüşü gerekir.
Politika: (6) Gizlilik Politikası "taslak": `[GİZLİLİK E-POSTASI]` yer tutucusu, veri sorumlusu unvanı/adres/saklama süreleri belirsiz → Play için tamamlanmalı. (7) Başka yerde satın alınmış aboneliğe uygulamada erişim Play'de açıkça izinli değil; inceleme sonucuna bağlı. (8) Web sarmalayıcı/minimum işlevsellik ret riski.
Teknik: (9) TWA'da Google OAuth cihazda doğrulanmadı. (10) `DELETE /api/v22/profile` 410'a çevrildi (çözüldü); owner admin silme yolu blokajsız. (11) Silme kapsamı: `member_free_trials`, `binance_manual_periods`, günlükler/yedekler. (12) `assetlinks.json` parmak izi sende. (13) `/hesap-silme` sayfası var ama üç yer tutucu doldurulmalı.

## 11. Onay bekleyen sonraki adımlar
0. Bu turdaki değişiklikleri incele/onayla (commit için ayrıca "Canlıya al").
1. Yerelde TWA derleme ve doğrulama (`ANDROID-YEREL-DERLEME.md`), sonuçları paylaş.
2. Gizlilik politikası + `/hesap-silme` yer tutucularının doğrulanmış bilgiyle doldurulması.
3. `assetlinks.json` yayını (gerçek parmak izi sende olunca).
4. Eski `DELETE /profile` kararı; ülke/lisans kararı.

## 12. Paketleme öncesi engeller (5. tur)
Yayını engelleyenler:
1. Canlıya alma: Android ödeme gizleme, Kais AI Android kısıtı, `/account/delete`, `DELETE /profile` 410, `/hesap-silme` ve `vercel.json` düzeltmesi canlıda DEĞİL (commit/deploy yok). Render backend manuel yeniden dağıtım gerektirir. Bu olmadan üretilen paket Play'e verilemez.
2. Doğrulanmamış davranışlar: hesap silme HTTP akışı, açık pozisyon blokajı, eşzamanlı istek koruması, Android ödeme/Kais AI kısıtları — testleri çalıştırılamadı; CI ve test hesabıyla doğrulanmalı.
3. Google OAuth: TWA'da cihazda denenmedi (Custom Tab dönüşü, çerez, 2FA).
4. Digital Asset Links: `assetlinks.json` yok; canlıda istek büyük olasılıkla HTML dönüyor; Play App Signing parmak izi Play Console'dan gelecek; ilk yükleme sonrası alınabilir.
5. Gizlilik sayfası: `/privacy` taslak; `[GİZLİLİK E-POSTASI]`, `[VERİ SORUMLUSU / HUKUKİ UNVAN]`, `[SAKLAMA SÜRELERİ]`, `[UYGULANACAK HUKUK]`, `[ÖDEME / ALTYAPI SAĞLAYICILARI]`, `[ÇEREZ ENVANTERİ]`, `[ALICILAR / ÜLKELER / AKTARIM DAYANAKLARI]` doldurulmadı; `/hesap-silme` aynı ilk üç yer tutucuyu içerir.
6. Hedef ülkeler ve finansal özellik/lisans kararı verilmedi; Play finansal özellikler beyanı bunu gerektirir.
7. Play Console: veri güvenliği formu, uygulama erişimi (test hesabı), kapalı test şartı, mağaza girişi.
Araç zinciri: JDK 17 gerekli (bu ortamda 21 var), SDK yolu boşluksuz olmalı; AAB üretimi bu ortamda yapılamadı.
