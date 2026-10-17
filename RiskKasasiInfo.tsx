import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Info, X} from 'lucide-react'
import './kais-info.css'

export default function RiskKasasiInfo() {
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
    <button type="button" className="kaisInfoBtn" aria-label="Risk Kasası hakkında bilgi" title="Risk Kasası nasıl kullanılır?" onClick={() => setOpen(true)}><Info size={16}/><span>Nasıl kullanılır?</span></button>
    {open && createPortal(<div className="kaisInfoBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section className="kaisInfoDialog" role="dialog" aria-modal="true" aria-labelledby="risk-info-title">
        <header><div><small>RİSK YÖNETİMİ</small><h2 id="risk-info-title">Risk Kasası</h2></div><button ref={closeRef} type="button" aria-label="Kapat" onClick={() => setOpen(false)}><X size={18}/></button></header>
        <div className="kaisInfoBody">
          <h3>Bu bölüm ne işe yarar?</h3>
          <p>Risk Kasası, işleme girmeden önce “Stop olursa en fazla ne kadar kaybederim?” sorusuna cevap verir. Önce kaybetmeyi göze aldığın tutarı belirlersin, pozisyon büyüklüğü buna göre hesaplanır. Aynı zamanda günlük zarar ve işlem sayısı için üst sınırlar koyar.</p>
          <h3>Nasıl kullanılır?</h3>
          <ol className="kaisInfoSteps">
            <li><b>Pozisyon boyutlandırma:</b> Giriş ve stop fiyatını yaz (analizden otomatik de gelir). “Azami risk” kutusuna bu işlemde en fazla kaybetmeyi göze aldığın USDT tutarını yaz, sonra “Güvenli boyutu hesapla”ya bas.</li>
            <li><b>Günlük risk limiti:</b> Günlük zarar kilidini, işlem başı en fazla kaybı, günlük işlem sayısını ve aynı anda açık pozisyon sayısını belirle.</li>
            <li><b>Minimum güven:</b> Sinyalin işleme dönüşmesi için gereken en düşük güven puanıdır. Yükselttikçe daha az ama daha seçici işlem açılır.</li>
            <li><b>Stop modları:</b> Başabaş stop, kâr belirli bir seviyeye gelince stopu giriş fiyatına çeker. İz süren stop, fiyat ilerledikçe stopu da onunla birlikte taşır.</li>
            <li>Ayarları yaptıktan sonra “Risk politikasını kaydet”e bas. Kaydetmezsen değişiklikler geçerli olmaz.</li>
          </ol>
          <h3>Dikkat edilecekler</h3>
          <ul>
            <li>Demo hesapta üst sınır 100 USDT ve 2x kaldıraçtır.</li>
            <li>Günlük zarar kilidine ulaşılınca o gün yeni işlem açılmaz.</li>
            <li>Başlangıçta küçük risk tutarıyla başlamak daha güvenlidir.</li>
            <li>Bu bölüm yalnızca hesap yapar ve sınır koyar, işlem tavsiyesi vermez.</li>
          </ul>
          <h3>Risk bildirimi</h3>
          <p className="kaisInfoRisk">Kripto vadeli işlemler yüksek risk taşır. Kazanç garantisi verilmez. Risk limitleri kaybı sınırlamayı amaçlar ama kaybı tamamen engellemez. Yatırdığın sermayenin tamamını kaybedebilirsin. Bu içerik yatırım tavsiyesi değildir.</p>
        </div>
      </section>
    </div>, document.body)}
  </>
}
