import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Info, X} from 'lucide-react'
import './kais-info.css'

export default function KaisOriginalInfo() {
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
    <button type="button" className="kaisInfoBtn" aria-label="Kais Original V2 hakkında bilgi" title="Strateji nasıl çalışır?" onClick={() => setOpen(true)}><Info size={16}/></button>
    {open && createPortal(<div className="kaisInfoBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section className="kaisInfoDialog" role="dialog" aria-modal="true" aria-labelledby="kais-info-title">
        <header><div><small>STRATEJİ ÖZETİ</small><h2 id="kais-info-title">Kais Original V2</h2></div><button ref={closeRef} type="button" aria-label="Kapat" onClick={() => setOpen(false)}><X size={18}/></button></header>
        <div className="kaisInfoBody">
          <h3>Genel bakış</h3>
          <p>Kais Original V2, işlem hacmine göre öne çıkan USDT-M vadeli işlem çiftlerini 15 dakikalık periyotta tarayan, kurala dayalı bir otomatik işlem stratejisidir. Kararlar önceden tanımlı kurallara göre verilir.</p>
          <p>Platformda şu an tek otomatik strateji Kais Original V2'dir. Otomatik işlemin yanında manuel emir de verebilirsin.</p>
          <h3>Sinyal üretimi</h3>
          <ul>
            <li>Trend, momentum ve volatilite göstergeleri (EMA, MACD, RSI, ATR, ADX, Bollinger) birlikte değerlendirilir.</li>
            <li>Sinyal gücü ve kırılım kalitesi belirlenen eşikleri geçmelidir.</li>
            <li>1 saatlik ve 4 saatlik grafikler aynı yönü desteklemelidir (çoklu zaman dilimi teyidi).</li>
            <li>Yüksek tuzak riski taşıyan kurulumlar elenir. Veri eksik ya da güncel değilse yeni işlem açılmaz.</li>
          </ul>
          <h3>Risk yönetimi</h3>
          <ul>
            <li>Her pozisyon stop-loss ve kâr alma seviyeleriyle açılır.</li>
            <li>Kaldıraç 3x ile sınırlıdır.</li>
            <li>İşlem başı risk ve günlük zarar limitini kendin belirlersin. Günlük limite ulaşılınca yeni işlem açılmaz.</li>
            <li>Aynı anda açılabilecek pozisyon sayısı sınırlıdır.</li>
            <li>Oturum süresi (1 / 4 / 12 / 24 saat) dolunca yeni giriş yapılmaz. Açık pozisyonların koruma emirleri devam eder.</li>
          </ul>
          <h3>Piyasa koşullarına duyarlılık</h3>
          <p>Strateji, yönü net ve hacmi yeterli piyasalarda daha çok sinyal üretme eğilimindedir. Yatay ya da sürekli yön değiştiren piyasalarda sinyal sayısı azalabilir ve stopla kapanan işlem oranı artabilir. Pozisyonlar bazen birkaç gün açık kalabilir.</p>
          <h3>Risk bildirimi</h3>
          <p className="kaisInfoRisk">Kripto vadeli işlemler yüksek risk taşır. Kazanç garantisi verilmez. Geçmiş performans gelecekteki sonuçların göstergesi değildir. Yatırdığın sermayenin tamamını kaybedebilirsin. Bu içerik yatırım tavsiyesi değildir. Yalnızca kaybetmeyi göze alabileceğin tutarla işlem yap.</p>
        </div>
      </section>
    </div>, document.body)}
  </>
}
