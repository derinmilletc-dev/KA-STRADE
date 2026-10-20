# KaisTrade Android (TWA) — Windows'ta derleme rehberi

DURUM: AAB bu ortamda ÜRETİLMEDİ (Android SDK ve npm/Maven/Google depolarına erişim yok). Aşağıdaki komutlar bu ortamda çalıştırılmadı; Windows'ta senin makinende çalışacak. Hiçbir dosya yolu "üretildi" diye verilmez: `bubblewrap build` başarılı olunca dosya adını kendisi yazar, yolu oradan al.

## 0. Önce bunu oku: derlemeden ÖNCE canlıya alınması gerekenler
TWA, canlı `https://kaistrade.com` sitesini açar. Uygulamanın içeriği derleme dosyasında değil sitede durur. Bu yüzden:
- Android'e özel ödeme/abonelik gizleme (frontend), Kais AI Android kısıtı, yeni `/account/delete`, eski `DELETE /profile` → 410 (backend) ve `/hesap-silme` sayfası şu an SADECE çalışma kopyasında; commit/deploy yapılmadı. Canlı site bunları içermiyor.
- Canlı sitede `/.well-known/assetlinks.json` şu an büyük olasılıkla SPA'nın HTML sayfasını döndürüyor (araçla görülen yanıt HTML'e benziyordu; kesinleştirmek için §3'teki `curl.exe` kontrolünü çalıştır). Düzeltme (`vercel.json`) de henüz canlıda değil.
- Dolayısıyla bu aşamada üretilen paket Play'e GİDEMEZ (ödeme yüzeyleri görünür olur). Yerel TWA derlemesi yalnızca araç zincirini ve cihaz davranışını denemek içindir.
- Sıra: kodu incele → "Canlıya al" onayı → Render'ı elle yeniden dağıt (backend) + Vercel (frontend) → sonra Play'e yüklenecek paketi üret.

## 1. Ön koşullar (doğrulanan / doğrulanamayan)
| Bileşen | Gereksinim | Kaynak / durum |
|---|---|---|
| Windows | 10/11 x64, PowerShell 5.1+ | — |
| Node.js | Bubblewrap en az 14.15; güncel LTS (20/22) önerilir | Bubblewrap README; proje geliştirme ortamı Node 22 |
| JDK | TAM OLARAK 17 (alt sürüm derlemeyi bozar; 17'den yüksek Android komut satırı araçlarıyla uyumsuz) | Bubblewrap README. Bu ortamda JDK 21 vardı, uygun DEĞİL |
| Android SDK | "Command line tools" (Android Studio şart değil). Yol BOŞLUK İÇEREMEZ | Bubblewrap README |
| Bubblewrap | `@bubblewrap/cli`; GitHub'da son sürüm v1.25.0 ve "targetSdkVersion 36" yükseltmesini içeriyor (npm'den doğrulanamadı) | GitHub sürüm sayfası. Kurulumdan sonra §4-C ile hedef SDK'yı SEN doğrula |
| Telefon | Android + USB hata ayıklama, Chrome güncel | — |
| `adb` | Platform-tools (SDK ile gelir) | — |

Bubblewrap ilk çalıştırmada JDK ve SDK'yı kendisi indirmeyi önerir (resmî öneri). Windows kullanıcı adında boşluk/Türkçe karakter varsa (`C:\Users\Ahmet Yılmaz`) bu indirme yolu sorun çıkarabilir; o durumda §2'deki `C:\android` yollarını kullan.

## 2. Hazırlık (PowerShell)
Tüm komutlar PowerShell'de PROJE KÖK DİZİNİNDE çalışır. Kök dizin = içinde `package.json`, `vercel.json` ve `ANDROID-HAZIRLIK.md` bulunan klasör (git deposunun en üstü). Üretilen Android projesi kökte `android\` klasörüne gider; anahtarlar repo dışında `C:\kaistrade-keys\` altında durur.
```powershell
cd C:\proje\yolu\ka-strade                # kendi yolunu yaz
Test-Path .\package.json, .\vercel.json   # ikisi de True olmalı; değilse yanlış dizindesin
git branch --show-current                 # chore/android-prep (değişiklikler push edilmediği için bu kopyayı kendin taşımış olmalısın)
```
Araçlar (yalnızca eksik olanları kur; komutlar bu ortamda denenmedi):
```powershell
winget install OpenJS.NodeJS.LTS
winget install EclipseAdoptium.Temurin.17.JDK
node -v
java -version                             # "17.x" görmelisin
npm i -g @bubblewrap/cli
npm ls -g @bubblewrap/cli                 # kurulan sürüm
bubblewrap doctor                         # JDK/SDK yollarını ve sürümlerini kontrol eder
```
Bubblewrap JDK/SDK sorarsa "indir" de (varsayılan). Kendi yolunu vereceksen BOŞLUKSUZ olmalı:
```powershell
mkdir C:\android
bubblewrap updateConfig --jdkPath="C:\android\jdk-17" --androidSdkPath="C:\android\sdk"
bubblewrap doctor
```
Temurin varsayılan olarak `C:\Program Files\...` altına kurulur (yolda boşluk var); boşluksuz bir JDK 17 yolu kullan veya Bubblewrap'in indirmesine izin ver.

## 3. Canlı site ön kontrolü
```powershell
curl.exe -sI https://kaistrade.com/manifest.webmanifest         # 200 beklenir
curl.exe -sI https://kaistrade.com/icon-512.png                 # 200 ve image/png
curl.exe -s  https://kaistrade.com/.well-known/assetlinks.json  # JSON değil HTML görürsen: düzeltme henüz canlıda değil
```

## 4. Derleme
### A. Yükleme (upload) anahtarı — repo DIŞINDA
```powershell
mkdir C:\kaistrade-keys
$jdk = (Get-Content "$env:USERPROFILE\.bubblewrap\config.json" | ConvertFrom-Json).jdkPath   # kendi JDK'nı kullandıysan o yolu yaz
& "$jdk\bin\keytool.exe" -genkeypair -v -keystore C:\kaistrade-keys\kaistrade-upload.jks -alias upload `
  -keyalg RSA -keysize 2048 -validity 10000
& "$jdk\bin\keytool.exe" -list -v -keystore C:\kaistrade-keys\kaistrade-upload.jks -alias upload   # SHA256 satırı = YÜKLEME anahtarı parmak izi
```
Keystore dosyasını ve parolalarını en az iki yere yedekle; repoya KOYMA (`.gitignore` `*.jks`, `*.keystore`, `*.aab`, `*.apk` dosyalarını dışlar). Parolayı komut satırına yazma; keytool/Bubblewrap sorduğunda gir.

### B. TWA projesini oluştur (kökte `android\`)
```powershell
mkdir android
cd android
bubblewrap init --manifest=https://kaistrade.com/manifest.webmanifest
```
Soruların karşılığı (metinler sürüme göre değişebilir; emin olmadığın yerde varsayılanı kabul et):
| Soru | Cevap |
|---|---|
| Domain / host | `kaistrade.com` |
| Application ID (package) | `com.kaistrade.app` |
| App name / Launcher name | `KaisTrade` |
| Starting URL | `/?source=android-app` (Android bayrağı bununla açılır) |
| Display / orientation | `standalone` / `portrait` (manifestten gelir) |
| Status bar ve splash rengi | `#05070c` |
| İkon / maskable ikon URL | manifestteki `icon-512.png` / `icon-maskable-512.png` |
| Play Billing desteği | HAYIR (uygulamada Play Billing kullanılmıyor; ödeme akışı değişmedi) |
| Konum izni | HAYIR |
| Bildirim yetkilendirme | Varsayılan (web push için) |
| Anahtar dosyası / alias | `C:\kaistrade-keys\kaistrade-upload.jks` / `upload` |

### C. Üretilen yapılandırmayı kontrol et (hâlâ `android\` içinde)
```powershell
Get-Content .\twa-manifest.json                                          # packageId, host, startUrl, name, signingKey.path/alias
Select-String -Path .\app\build.gradle -Pattern "targetSdk|compileSdk"   # targetSdk 36 veya üzeri olmalı; değilse dur ve haber ver
```

### D. Derle
```powershell
bubblewrap build        # keystore ve anahtar parolasını sorar
Get-ChildItem -Recurse -Include *.aab,*.apk | Select-Object FullName,Length,LastWriteTime
```
Listede AAB görünmüyorsa AAB üretilmemiştir; çıktıdaki hatayı kaydet. Var olmayan bir yolu "AAB burada" diye kullanma. APK cihaza kurulum içindir; Play'e yüklenen dosya AAB'dir.

### E. Parmak izleri (yalnızca yerel test)
```powershell
bubblewrap fingerprint list
bubblewrap fingerprint generateAssetLinks --output=assetlinks.local.json   # YÜKLEME anahtarı içindir; repoya veya siteye yayınlama
cd ..                                                                       # proje köküne dön
```

## 5. Cihazda doğrulama (proje kökünden; APK yolunu §4-D listesinden al)
```powershell
adb devices                                  # cihaz "device" görünmeli
adb install -r .\android\app-release-signed.apk   # yol §4-D listesinde farklıysa onu kullan; veya: cd android; bubblewrap install
adb shell pm get-app-links com.kaistrade.app
adb logcat -v brief | Select-String "TWAProviderPicker"
```
Adres çubuğu görünüyorsa TWA değil Custom Tab açılmıştır: Digital Asset Links doğrulanmamış demektir (canlıda dosya yokken beklenen durum). Yerel test için Chrome belgelerinde geçen "belirli URL için DAL doğrulamasını kapat" yöntemi kullanılabilir (Chrome sürümüne bağlı; burada doğrulanmadı) veya yükleme anahtarının parmak izi canlı `assetlinks.json`'a eklenir (deploy gerektirir, onayına bağlı).

Cihaz kontrol listesi (HİÇBİRİ henüz test edilmedi):
- Google ile giriş: Google ekranı → uygulamaya dönüş → oturum açık mı → çıkış → yeniden aç.
- E-posta + parola, 2FA, oturumun uygulama kapanıp açılınca sürmesi.
- Kais AI yazılı ve SESLİ (mikrofon izni; Web Speech TWA'da çalışıyor mu).
- Geri tuşu, uçak modu/bağlantı kesilmesi, çentik/güvenli alan.
- Android'de Abonelik/Premium/fiyat yüzeyleri görünmemeli; Kais AI plan/fiyat sorusuna "Bu bilgiyi uygulamada veremiyorum." demeli (yalnızca canlıya alındıktan sonra görülür).
- Hesap silme: YALNIZCA test hesabıyla; silme sonrası aynı hesapla giriş reddedilmeli.
- Web Push bildirimine dokununca uygulama içinde mi açılıyor, Android bayrağı korunuyor mu.
- Harici bağlantılar ve dosya indirme.

## 6. Play App Signing ve assetlinks.json
- Play Console'da uygulamayı oluşturup AAB'yi (iç/kapalı test) yükleyince Play App Signing devreye girer; kullanıcıların indirdiği uygulama Play'in anahtarıyla imzalanır. Bu yükleme SENİN kararına bağlıdır; bu aşamada yapılmadı.
- Parmak izi: Play Console > Uygulama > Test ve yayınlama > Uygulama bütünlüğü > Uygulama imzalama > "App signing key certificate" SHA-256. Değeri uydurma, buradan kopyala. Yükleme anahtarının parmak izi ayrıdır.
- Dosya: `frontend/public/.well-known/assetlinks.json` (canlı adres `https://kaistrade.com/.well-known/assetlinks.json`, HTTPS, yönlendirmesiz, `application/json`):
```
[{"relation":["delegate_permission/common.handle_all_urls"],
  "target":{"namespace":"android_app","package_name":"com.kaistrade.app",
            "sha256_cert_fingerprints":["<PLAY_APP_SIGNING_SHA256>"]}}]
```
  `<...>` yer tutucudur; dosya gerçek parmak izi gelmeden repoya eklenmeyecek. Yükleme anahtarı izini de listeye ekleyebilirsin (iç test için). Eklendiğinde `node --test tools/vercel-routing.test.mjs` biçimi ve yer tutucuyu denetler.
- Yayından sonra `curl.exe -s https://kaistrade.com/.well-known/assetlinks.json` JSON döndürmeli; `adb shell pm get-app-links com.kaistrade.app` doğrulanmış görünmeli. Google API: `https://digitalassetlinks.googleapis.com/v1/statements:list?source.web.site=https://kaistrade.com&relation=delegate_permission/common.handle_all_urls`

## 7. Sık hatalar
| Belirti | Neden / çözüm |
|---|---|
| `bubblewrap doctor` JDK uyarısı | JDK 17 değil (21 vb.) → 17 kur |
| SDK yolu hatası | Yolda boşluk var → `C:\android\sdk` kullan |
| Uygulama adres çubuğuyla açılıyor | DAL doğrulanmadı (§5, §6) |
| `keytool` bulunamadı | §4-A'daki `$jdk` yolunu kontrol et |
| AAB yok, yalnızca APK | `bubblewrap build` hata verdi; çıktıyı oku, SDK/JDK sürümünü doğrula |
