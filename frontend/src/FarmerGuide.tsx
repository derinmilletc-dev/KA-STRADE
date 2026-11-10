import {useEffect, useRef, useState} from 'react'
import {createPortal} from 'react-dom'
import {BookOpen, ChevronDown, MessageCircle, X} from 'lucide-react'
import type {Lang} from './farmerView'
import './farmer.css'

/** KAIS REHBER › KAIS FARMER içeriği. Aynı pencere deseni KAIS LONG bilgi kartıyla aynıdır (portal + Esc + odak); içerik Farmer'a özeldir.
 *  Dil sade tutulur: belge değil, ürün içi koç. Hedefler "garanti" olarak sunulmaz. */
type Topic = {id: string; q: Record<Lang, string>; a: Record<Lang, string>}

export const FARMER_GUIDE_TOPICS: Topic[] = [
  {id: 'what', q: {tr: 'KAIS FARMER ne yapar?', en: 'What does KAIS FARMER do?'},
    a: {tr: 'Piyasayı yüksek kaliteli kurulumlar için tarar. Bir kurulum senin kalite şartlarını geçerse küçük bir pozisyon açar ve onu önceden belirlenmiş net kâr hedefi ve en fazla kayıp sınırıyla yönetir. Amaç: küçük, kontrollü, tekrarlanan sonuçlar.',
      en: 'It scans the market for high-quality setups. When a setup passes your quality requirements it opens a small position and manages it with a predefined net profit target and maximum loss. The goal: small, controlled, repeatable results.'}},
  {id: 'vs-long', q: {tr: 'KAIS LONG’dan farkı ne?', en: 'How is it different from KAIS LONG?'},
    a: {tr: 'KAIS LONG yalnız yükseliş (LONG) fırsatlarına bakar. KAIS FARMER küçük, sık ve net hedefli pozisyonlara odaklanır; kendi marjin tavanı, kendi bekleme kuralları ve kendi ölü pozisyon koruması vardır. İkisi birbirinden bağımsız çalışır.',
      en: 'KAIS LONG looks only at upward (LONG) opportunities. KAIS FARMER focuses on small, net-targeted positions with its own margin cap, its own waiting rules and its own dead-position protection. The two run independently.'}},
  {id: 'vs-dca', q: {tr: 'DCA’dan farkı ne?', en: 'How is it different from DCA?'},
    a: {tr: 'DCA zarardayken kademeli alım yapar ve ortalamayı düşürür. KAIS FARMER pozisyonu büyütmez: tek giriş, tek stop, tek hedef. Zarardaki pozisyona daha fazla para eklenmez.',
      en: 'DCA adds in steps while a position is losing to lower the average price. KAIS FARMER never adds to a position: one entry, one stop, one target. No more money goes into a losing position.'}},
  {id: 'target', q: {tr: 'Kâr hedefi (Profit Target) nedir?', en: 'What is the Profit Target?'},
    a: {tr: 'Pozisyonun, ücretler düşüldükten sonra ulaşmasını istediğin tahmini NET kazanç. Bu bir hedeftir, garanti değildir: gerçek kapanış ücretler, fonlama ve piyasa koşullarına göre farklı olabilir.',
      en: 'The estimated NET gain, after fees, that you want a position to reach. It is a target, not a guarantee: the actual close can differ because of fees, funding and market conditions.'}},
  {id: 'maxloss', q: {tr: 'En fazla kayıp (Max Loss) nedir?', en: 'What is Max Loss?'},
    a: {tr: 'Tek bir pozisyonda göze aldığın planlanan en yüksek net kayıp. Stop emri borsada bu sınıra göre durur. Hızlı piyasada gerçekleşen kayıp planlanandan biraz farklı olabilir.',
      en: 'The highest planned net loss you accept on one position. The stop order sits on the exchange based on this limit. In fast markets the realised loss can differ slightly from the plan.'}},
  {id: 'score', q: {tr: 'Minimum skor nedir? Düşük skor bot bozuk demek mi?', en: 'What is Minimum Score? Does a low score mean it is broken?'},
    a: {tr: 'Skor, KAIS FARMER modeline göre kurulumun gücüdür: yüksek skor = daha güçlü kurulum. Minimum skor altındaki kurulumlar atlanır. Düşük skorlar botun bozuk olduğu anlamına gelmez; piyasa o an fırsat vermiyor demektir.',
      en: 'The score is the strength of a setup according to the KAIS FARMER model: higher means stronger. Setups below your minimum are skipped. Low scores do not mean the bot is broken — the market simply is not offering a good setup right now.'}},
  {id: 'dead', q: {tr: 'Ölü pozisyon koruması nedir?', en: 'What is Dead Position Protection?'},
    a: {tr: 'Pozisyon açıldıktan sonra kurulum belirgin biçimde bozulduysa ve piyasa ters yönde onaylanıyorsa pozisyon beklemek yerine erken kapatılabilir. “Dead score” ve “onay sayısı” bunun ne kadar hassas olduğunu belirler. İstersen kapalı tutabilirsin.',
      en: 'If, after entry, the setup clearly deteriorates and the market confirms the opposite direction, the position can be closed early instead of waiting. “Dead score” and “confirmations” set how sensitive this is. You can keep it off.'}},
  {id: 'noposition', q: {tr: 'Neden pozisyonsuz kalabilir?', en: 'Why may it stay without a position?'},
    a: {tr: 'Çünkü işlem zorlanmaz. Nitelikli bir kurulum yoksa, skor yetmiyorsa, ARM bittiyse ya da bir güvenlik kapısı açıksa KAIS FARMER bekler. Boş kalan yer bir sorun değildir.',
      en: 'Because trades are never forced. If there is no qualified setup, the score is too low, ARM expired or a safety gate is active, KAIS FARMER waits. An empty slot is not a problem.'}},
  {id: 'slots', q: {tr: 'Neden tüm yerleri doldurmaz?', en: 'Why does it not fill all available slots?'},
    a: {tr: 'Yer sayısı yalnızca üst sınırdır, “şu kadar işlem aç” talimatı değil. Ayrıca marjin tavanı gerçek kapasiteyi düşürebilir: örneğin tavan 100 ve pozisyon marjini 50 ise aynı anda en fazla 2 pozisyon olabilir.',
      en: 'The number of positions is only a ceiling, not an instruction to open that many. The margin cap can also lower the real capacity: with a cap of 100 and 50 per position, at most 2 can exist at once.'}},
  {id: 'arm', q: {tr: 'ARM nedir?', en: 'What is ARM?'},
    a: {tr: 'ARM, yeni giriş yapmak için verdiğin süreli işlem iznidir (işlem oturumu). Süresi dolarsa YENİ girişler durur; açık pozisyonların koruma emirleri borsada çalışmaya devam eder ve pozisyonlar kapatılmaz. ARM’ı yenileyince girişler yeniden başlayabilir.',
      en: 'ARM is the time-limited permission you give for new entries (your trading session). When it expires, NEW entries stop; protection orders on open positions keep working on the exchange and positions are not closed. Renewing ARM lets entries resume.'}},
  {id: 'stop', q: {tr: 'STOP BOT ne yapar?', en: 'What does STOP BOT do?'},
    a: {tr: 'Yalnızca YENİ girişleri durdurur. Açık pozisyonlar kapatılmaz; koruma emirleriyle kendi hedeflerinde ya da stoplarında kapanır.',
      en: 'It only stops NEW entries. Open positions are not closed; they continue under their protection orders and close at their own target or stop.'}},
  {id: 'emergency', q: {tr: 'Acil kapat (Emergency Close) ne yapar?', en: 'What does Emergency Close do?'},
    a: {tr: 'Yalnızca KAIS FARMER’a ait pozisyonları reduce-only emirlerle kapatmaya çalışır ve sonucu borsadan doğrular. Kapanış fiyatı ekrandakinden farklı olabilir. Başka botlara ve manuel pozisyonlara dokunmaz. STOP BOT’tan farklıdır: STOP BOT pozisyon kapatmaz.',
      en: 'It tries to close only KAIS FARMER positions using reduce-only orders and then verifies the result on the exchange. The close price may differ from what you see. It does not touch other bots or manual positions. It is different from STOP BOT, which closes nothing.'}},
  {id: 'pnl', q: {tr: 'Doğrulanmış / doğrulanmamış PnL ne demek?', en: 'What do verified / unverified PnL mean?'},
    a: {tr: 'Net sonuç; işlem, komisyon ve fonlama borsa kayıtlarıyla eşleşince DOĞRULANMIŞ olur. Önce “doğrulanıyor” görürsün. Kayıtlar eksik ya da gecikmiş olabilir; bu durumda “doğrulanamadı” yazar ve sahte bir 0 gösterilmez (—).',
      en: 'The net result is VERIFIED once trades, commissions and funding match exchange records. You first see “being verified”. Records can be late or incomplete; then it says “could not be verified yet” and no fake zero is shown (—).'}},
  {id: 'open', q: {tr: 'Pozisyon neden açık kalabilir?', en: 'Why can a position stay open?'},
    a: {tr: 'Hedef ya da stop fiyatına henüz ulaşılmadığı için. Zaman aşımı yoktur; pozisyon hedefini, stopunu ya da ölü pozisyon korumasını bekler. Bu sırada koruma emirleri borsada yerindedir.',
      en: 'Because neither the target nor the stop price has been reached yet. There is no time limit; a position waits for its target, its stop or dead-position protection. Protection orders stay on the exchange meanwhile.'}},
  {id: 'guarantee', q: {tr: 'Kâr hedefi garanti mi?', en: 'Are profit targets guaranteed?'},
    a: {tr: 'Hayır. Hedefler hedeftir. Piyasa hedefe ulaşmadan stopa gidebilir; kayıplar olabilir. KAIS FARMER yatırım tavsiyesi vermez.',
      en: 'No. Targets are targets. The market can reach your stop before your target; losses can happen. KAIS FARMER does not give investment advice.'}},
  {id: 'protection', q: {tr: 'Borsa tarafı koruma neden önemli?', en: 'Why is exchange-side protection important?'},
    a: {tr: 'Stop ve hedef emirleri borsada durur. İnternetin ya da uygulamanın kapanması pozisyonun korunmasını durdurmaz. Koruma emirleri doğrulanamazsa KAIS FARMER yeni pozisyon açmaz ve sana haber verir.',
      en: 'Stop and target orders live on the exchange. If your internet or the app goes down, the position stays protected. If protection cannot be verified, KAIS FARMER opens nothing new and tells you.'}},
]

export default function FarmerGuide({lang, open, topic, onClose, onAsk}: {lang: Lang; open: boolean; topic?: string; onClose: () => void; onAsk?: (question: string) => void}) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const [active, setActive] = useState<string | null>(topic ?? 'what')
  useEffect(() => { if (open) setActive(topic ?? 'what') }, [open, topic])
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); previous?.focus?.() }
  }, [open, onClose])
  if (!open || typeof document === 'undefined') return null
  const t = (tr: string, en: string) => (lang === 'tr' ? tr : en)
  return createPortal(<div className="fmBackdrop" role="presentation" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <section className="fmModal fmGuide" role="dialog" aria-modal="true" aria-labelledby="fm-guide-title" data-testid="farmer-guide">
      <header className="fmModalHead">
        <span className="fmModalIcon" aria-hidden="true"><BookOpen/></span>
        <div><small>KAIS REHBER</small><h3 id="fm-guide-title">KAIS FARMER</h3></div>
        <button ref={closeRef} type="button" className="fmX" aria-label={t('Kapat', 'Close')} onClick={onClose}><X size={18}/></button>
      </header>
      <p className="fmLead">{t('Kısa ve sade: KAIS FARMER’ı güvenle kullanmak için bilmen gerekenler.', 'Short and simple: what you need to know to use KAIS FARMER with confidence.')}</p>
      <div className="fmAccordion">
        {FARMER_GUIDE_TOPICS.map(item => {
          const isOpen = active === item.id
          return <div key={item.id} className="fmAcc" data-open={isOpen || undefined}>
            <button type="button" className="fmAccBtn" aria-expanded={isOpen} aria-controls={`fm-acc-${item.id}`} onClick={() => setActive(isOpen ? null : item.id)}>
              <span>{item.q[lang]}</span><ChevronDown aria-hidden="true"/>
            </button>
            {isOpen && <p id={`fm-acc-${item.id}`} className="fmAccBody">{item.a[lang]}</p>}
          </div>
        })}
      </div>
      {onAsk && <button type="button" className="fmGhostBtn" onClick={() => { onClose(); onAsk(t('KAIS FARMER nasıl çalışır? Başlamadan önce neye dikkat etmeliyim?', 'How does KAIS FARMER work? What should I check before starting?')) }}><MessageCircle aria-hidden="true"/>{t('Kais’e sor', 'Ask Kais')}</button>}
    </section>
  </div>, document.body)
}
