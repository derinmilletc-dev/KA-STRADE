import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Info, X} from 'lucide-react'
import './kais-info.css'

export default function CloudOpsInfo({owner = false}: {owner?: boolean}) {
  const [open, setOpen] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  return <>
    <button type="button" className="kaisInfoBtn" aria-label="Bulut Operasyon ve Kanıt Merkezi hakkında bilgi" title="Bu sayfa nasıl çalışır?" onClick={() => setOpen(true)}><Info size={16}/><span>Nasıl çalışır?</span></button>
    {open && createPortal(<div className="kaisInfoBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section className="kaisInfoDialog" role="dialog" aria-modal="true" aria-labelledby="cloudops-info-title">
        <header><div><small>SİSTEM İZLEME</small><h2 id="cloudops-info-title">Bulut Operasyon ve Kanıt Merkezi</h2></div><button ref={closeRef} type="button" aria-label="Kapat" onClick={() => setOpen(false)}><X size={18}/></button></header>
        <div className="kaisInfoBody">
          <h3>Bu sayfa ne işe yarar?</h3>
          <p>Otomatik işlem sisteminin ne yaptığını, neden işlem açtığını ya da neden beklediğini tek ekranda gösterir. Bu sayfa yalnızca izleme ve kayıt ekranıdır. Buradan işlem açılmaz veya kapatılmaz.</p>
          <h3>Sayfadaki bölümler</h3>
          <ol className="kaisInfoSteps">
            <li><b>Üstteki dört kart:</b> Demo API anahtarının şifreli kasada olup olmadığını, Binance test ağı bağlantısını, otomatik taramanın çalışıp çalışmadığını ve son kanıt kaydının durumunu gösterir. Anahtarın kendisi hiçbir zaman ekranda gösterilmez.</li>
            <li><b>Sayısal özet:</b> {owner ? 'Test cüzdanı, açık kâr/zarar, bugünkü otomatik giriş sayısı, sunucunun kesintisiz çalışma süresi ve demo kanıt skoru.' : 'Test cüzdanı, açık kâr/zarar, bugünkü otomatik giriş sayısı ve sunucunun kesintisiz çalışma süresi.'}</li>
            <li><b>Canlı Kanıt Monitörü:</b> Botun son olayını ve mevcut durumunu gösterir. “Bot neden işlem açtı ya da açmadı?” sorusunun cevabı burada görülür.</li>
            <li><b>Kanıt Geçmişi:</b> Sistemin kaydettiği olaylar veritabanında saklanır. Sunucu yeniden başlasa bile silinmez.</li>
            {owner && <li><b>Kanıt Sertifikası:</b> Demo sonuçlarının ne kadar güvenilir olduğunu ölçer. Yeterli gün sayısı, yeterli kapanmış işlem ve kurtarma tatbikatı gibi şartlar tamamlandıkça skor yükselir. Bu bölümü yalnızca yönetici görür.</li>}
            <li><b>Aktif pozisyon:</b> Test ağında açık olan pozisyonları gösterir.</li>
          </ol>
          <h3>Bilmen gerekenler</h3>
          <ul>
            <li>Sayfa kendiliğinden birkaç saniyede bir yenilenir. “Yenile” düğmesiyle hemen güncelleyebilirsin.</li>
            {owner && <li>“Kanıtı Şimdi Kaydet” düğmesi, mevcut durumu hemen veritabanına yazar.</li>}
            <li>“Önizleme sunucusu uyuyabilir” uyarısı bir işlem uyarısı değildir. Altyapı katmanına göre servis boşta durabilir, 7/24 tarama için sürekli çalışan sunucu gerekir.</li>
          </ul>
          <h3>Risk bildirimi</h3>
          <p className="kaisInfoRisk">Bu sayfadaki sonuçlar demo ve test ağı verileridir. Kazanç garantisi verilmez. Geçmiş performans gelecekteki sonuçların göstergesi değildir. Kripto vadeli işlemler yüksek risk taşır ve sermayenin tamamını kaybedebilirsin. Bu içerik yatırım tavsiyesi değildir.</p>
        </div>
      </section>
    </div>, document.body)}
  </>
}
