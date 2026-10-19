# admin.kaistrade.com kurulumu

Kod hazırdır; alan adı bağlanana kadar hiçbir şey değişmez (`VITE_ADMIN_ORIGIN` boşken /admin ve /moderator eskisi gibi ana sitede açılır).

## Yapı
- `index.html` → müşteri uygulaması (`main.tsx`). Admin/moderatör panelleri bu pakete girmez, yalnızca ilgili yol açılırsa ayrı parça olarak indirilir.
- `admin.html` → yönetim uygulaması (`admin-main.tsx`). Yalnızca giriş + AdminPanel (OWNER) + ModeratorPanel (MODERATOR). Müşteri ekranları yok; CUSTOMER hesabı 403 ekranı görür.
- Yetkiler ayrı kalır: OWNER `/admin`, MODERATOR `/moderator`; MODERATOR `/admin`'e giremez. Asıl yetki kontrolü her zaman backend'dedir.
- Moderatör MFA kurulumu admin alan adında `/settings` üzerinden yapılır.

## Açma adımları (sırayla)
1. Vercel → proje → Settings → Domains → `admin.kaistrade.com` ekle; DNS'te Vercel'in verdiği CNAME kaydını oluştur.
2. Vercel → Settings → Environment Variables → `VITE_ADMIN_ORIGIN=https://admin.kaistrade.com` (Production) ve yeniden deploy et.
3. `https://admin.kaistrade.com` aç, yönetici hesabıyla giriş yap. Oturum çerezleri alan adına özeldir; ana sitedeki oturum burada geçerli değildir, ayrıca giriş gerekir.
4. Backend `ADMIN_WEB_ORIGIN` değerini `https://admin.kaistrade.com` olarak zaten kabul eder (Render ek ayar istemez).

Geri alma: `VITE_ADMIN_ORIGIN` değişkenini silip yeniden deploy etmek yeterlidir.
