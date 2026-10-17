import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {Info, X} from 'lucide-react'
import './kais-info.css'

export default function ScannerInfo() {
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
    <button type="button" className="kaisInfoBtn" aria-label="Tarama hakkında bilgi" title="Tarama nasıl kullanılır?" onClick={() => setOpen(true)}><Info size={16}/><span>Nasıl kullanılır?</span></button>
    {open && createPortal(<div className="kaisInfoBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section className="kaisInfoDialog" role="dialog" aria-modal="true" aria-labelledby="scanner-info-title">
        <header><div><small>PİYASA ANALİZİ</small><h2 id="scanner-info-title">Piyasa Taraması</h2></div><button ref={closeRef} type="button" aria-label="Kapat" onClick={() => setOpen(false)}><X size={18}/></button></header>
        <div className="kaisInfoBody">
          <h3>Bu bölüm ne işe yarar?</h3>
          <p>Tarama, Binance vadeli işlem piyasasındaki işlem hacmi en yüksek pariteleri tek tek inceler ve teknik olarak öne çıkanları puanlayıp sıralar. Tek tek grafik açmadan hangi paritelerde dikkat çekici bir durum olduğunu görmeni sağlar.</p>
          <h3>Nasıl çalışır?</h3>
          <ul>
            <li>Seçtiğin zaman diliminde (örneğin 15 dakika) her paritenin trendine, momentumuna, işlem hacmine ve MACD ile RSI göstergelerine bakar.</li>
            <li>Ayrıca 1 saatlik ve 4 saatlik grafiklerin aynı yönü destekleyip desteklemediğini kontrol eder.</li>
            <li>Bu kontrollerden 0 ile 100 arasında bir puan çıkarır. Puan yükseldikçe sinyal daha güçlü sayılır.</li>
          </ul>
          <h3>Nasıl kullanılır?</h3>
          <ol className="kaisInfoSteps">
            <li><b>Ayarları seç:</b> Zaman dilimini, kaç pariteye bakılacağını (TOP 20 veya TOP 50), yönü (LONG, SHORT veya ikisi) ve sinyal gücünü seç. Belirli paritelere bakmak için CUSTOM’ı seçip paritelerini işaretle.</li>
            <li><b>Taramayı başlat:</b> “Scan market” veya “Scan now” düğmesine bas. Bitmesi birkaç saniye sürebilir.</li>
            <li><b>Sonuçlara bak:</b> Üstteki kutular güçlü long, güçlü short, kırılım ve yüksek hacimli parite sayısını gösterir. Sonuç listesinin üstündeki düğmelerle (ALL, LONG, SHORT, STRONG, BREAKOUT, HIGH VOLUME) listeyi süzebilirsin.</li>
            <li><b>Paritenin ayrıntısını aç:</b> Listeden bir pariteye basınca grafiği, göstergeleri ve sinyalin nedenleri görünür.</li>
            <li><b>Sıralama:</b> Listeyi sinyal puanına, hacme veya momentuma göre sıralayabilirsin.</li>
          </ol>
          <h3>Dikkat edilecekler</h3>
          <ul>
            <li>Ayarları değiştirdikten sonra sonuçlar eskimiş olur, taramayı yeniden başlat.</li>
            <li>Tarama yalnızca analiz yapar. Kendi başına işlem açmaz ve emir göndermez.</li>
            <li>Bir paritenin puanının yüksek olması işlemin kazançlı olacağı anlamına gelmez.</li>
            <li>İşleme karar vermeden önce grafiği, stop seviyesini ve Risk Kasası limitlerini kontrol et.</li>
          </ul>
          <h3>Risk bildirimi</h3>
          <p className="kaisInfoRisk">Kripto vadeli işlemler yüksek risk taşır. Kazanç garantisi verilmez. Tarama sonuçları teknik göstergelere dayanır, geçmiş veriler geleceği garanti etmez. Yatırdığın sermayenin tamamını kaybedebilirsin. Bu içerik yatırım tavsiyesi değildir.</p>
        </div>
      </section>
    </div>, document.body)}
  </>
}
