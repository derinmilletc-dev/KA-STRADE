import {useMemo, useState} from 'react'
import {ChevronDown, CreditCard, KeyRound, LifeBuoy, LineChart, MessageCircle, Search, ShieldCheck, UserRound, X, Zap} from 'lucide-react'
import SupportCard from './SupportCard'
import {FAQ_CATEGORIES, FAQS, type FaqCategory} from './support-faq'
import './support-center.css'

const ICONS: Record<FaqCategory, typeof LifeBuoy> = {account: UserRound, billing: CreditCard, binance: KeyRound, trading: LineChart, security: ShieldCheck}

const QUICK = [
  {title: 'Binance’e bağlanamıyorum', steps: ['API anahtarını eksiksiz kopyala.', 'Futures işlem izninin açık olduğunu kontrol et.', 'Binance’te IP kısıtlaması varsa geçici olarak kaldır.', 'Yine olmuyorsa hata ekranının görüntüsünü aşağıdaki formdan gönder.']},
  {title: 'Bot işlem açmıyor', steps: ['Master Trade’de botun son kararına bak.', 'Günlük zarar limitine ulaşılıp ulaşılmadığını kontrol et.', 'Piyasa belirsizse beklemesi normaldir.', 'Bakiyenin ve izinlerin yeterli olduğundan emin ol.']},
  {title: 'Ödeme veya abonelik sorunu', steps: ['Abonelik sayfasında durumunu kontrol et.', 'Ödeme başarısızsa ödeme yöntemini güncelle.', 'İptal için faturalama portalını kullan.', 'Çözülmezse faturayla ilgili bilgiyi forma yaz.']},
  {title: 'Bildirim gelmiyor', steps: ['Master Trade sayfasının açık olduğundan emin ol.', 'Ayarlar’daki Bildirimler bölümünde türleri aç.', 'Tarayıcı izin isterse “İzin ver” seç.', 'Tarayıcı ayarlarında siteyi engellemediğini kontrol et.']},
]

export default function SupportCenter() {
  const [query, setQuery] = useState('')
  const [cat, setCat] = useState<'all' | FaqCategory>('all')
  const [open, setOpen] = useState<string | null>(null)
  const [quick, setQuick] = useState<number | null>(0)
  const list = useMemo(() => {
    const q = query.trim().toLocaleLowerCase('tr')
    return FAQS.filter(f => (cat === 'all' || f.category === cat) && (!q || `${f.q} ${f.a}`.toLocaleLowerCase('tr').includes(q)))
  }, [query, cat])
  const goContact = () => document.getElementById('support-contact')?.scrollIntoView({behavior: 'smooth', block: 'start'})
  return <div className="scWrap">
    <section className="scHero">
      <span className="scHeroIcon"><LifeBuoy/></span>
      <div><h2>Nasıl yardımcı olabiliriz?</h2><p>Önce sıkça sorulan sorulara bak. Cevabı bulamazsan destek ekibine yaz.</p></div>
      <label className="scSearch"><Search/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Soru veya anahtar kelime ara" aria-label="Soru veya anahtar kelime ara"/>{query && <button type="button" aria-label="Aramayı temizle" onClick={() => setQuery('')}><X/></button>}</label>
    </section>

    <section className="scBlock">
      <h3><Zap/>Hazır cevaplar</h3>
      <div className="scQuick">
        {QUICK.map((item, i) => <article key={item.title} className={quick === i ? 'on' : ''}>
          <button type="button" aria-expanded={quick === i} onClick={() => setQuick(quick === i ? null : i)}><b>{item.title}</b><ChevronDown/></button>
          {quick === i && <ol>{item.steps.map(step => <li key={step}>{step}</li>)}</ol>}
        </article>)}
      </div>
    </section>

    <section className="scBlock">
      <h3><MessageCircle/>Sıkça sorulan sorular</h3>
      <div className="scCats" role="tablist">
        {FAQ_CATEGORIES.map(c => <button key={c.id} type="button" role="tab" aria-selected={cat === c.id} className={cat === c.id ? 'on' : ''} onClick={() => setCat(c.id)}>{c.label}</button>)}
      </div>
      <div className="scFaq">
        {list.map(f => {
          const Icon = ICONS[f.category]
          const isOpen = open === f.id
          return <article key={f.id} className={isOpen ? 'on' : ''}>
            <button type="button" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : f.id)}><span><Icon/></span><b>{f.q}</b><ChevronDown/></button>
            {isOpen && <p>{f.a}</p>}
          </article>
        })}
        {!list.length && <div className="scEmpty"><b>Sonuç bulunamadı</b><p>Aradığın cevabı bulamadıysan sorunu aşağıdan destek ekibine yazabilirsin.</p><button type="button" onClick={goContact}>Destek ekibine yaz</button></div>}
      </div>
    </section>

    <section className="scBlock scContact" id="support-contact">
      <h3><LifeBuoy/>Destek ekibine yaz</h3>
      <p className="scNote">Cevabı bulamadıysan konuyu seç, sorunu anlat ve gerekirse ekran görüntüsü ekle. Şifre veya gizli anahtar asla yazma.</p>
      <div className="scCard"><SupportCard/></div>
    </section>
  </div>
}
