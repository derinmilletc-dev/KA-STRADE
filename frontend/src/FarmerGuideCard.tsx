import type {CSSProperties} from 'react'
import {BookOpen, Check, MessageCircle, Zap} from 'lucide-react'
import {armRemaining, money, tr_, whyKey, type CopyKey, type FarmerPreview, type FarmerStatus, type Screen} from './farmerView'

/** KAIS REHBER kartı (KAIS FARMER). DCA ve Kais Long'daki rehber gibi adım adım yönlendirir.
 *  Yalnız sunucudan gelen durumu okur: sayı hesaplamaz, emir göndermez. Birincil düğme yalnız sayfada ilgili yere götürür ya da mevcut komutu çağırır. */
type Tone = 'go' | 'warn' | 'stop'
export type FarmerGuideCardProps = {
  status: FarmerStatus; screen: Screen; L: (tr: string, en: string) => string; now: number
  preview: FarmerPreview | null; onConnect?: () => void; onRenew: () => void; onOpenGuide: () => void; onAsk: (question: string) => void; busy: boolean
}

function scrollTo(testId: string) {
  const el = document.querySelector(`[data-testid="${testId}"]`)
  if (el instanceof HTMLElement) { if (el instanceof HTMLDetailsElement) el.open = true; el.scrollIntoView({behavior: 'smooth', block: 'center'}) }
}

export default function FarmerGuideCard({status, screen, L, now, preview, onConnect, onRenew, onOpenGuide, onAsk, busy}: FarmerGuideCardProps) {
  const t = (key: CopyKey) => tr_(L('tr', 'en') as 'tr' | 'en', key)
  const steps = [L('Hesap', 'Account'), L('Ayarlar', 'Settings'), L('Oturum', 'Session'), L('Başlat', 'Start'), L('Çalışıyor', 'Running')]
  const cfg = status.config
  const configured = status.configured
  const armOn = !!status.arm.active
  const state = status.state
  const positions = status.positions.length
  const arm = armRemaining(status.arm.expires_at, now)

  let step = 0
  let tone: Tone = 'go'
  let title = ''
  let message = ''
  let action: {label: string; run: () => void; disabled?: boolean} | null = null
  let ask = L('KAIS FARMER nasıl çalışıyor? Kurulumu adım adım anlat.', 'How does KAIS FARMER work? Walk me through the setup.')

  if (screen === 'account_unavailable') {
    step = 0; tone = 'warn'
    title = L('Önce Binance hesabını bağla', 'Connect your Binance account first')
    message = L('KAIS FARMER canlı hesabındaki bakiyeye ve pozisyonlara bakarak çalışır. Bağlantıdan sonra buradan devam edersin.', 'KAIS FARMER works from your live balance and positions. After connecting you continue here.')
    if (onConnect) action = {label: L('Bağlantıyı aç', 'Open connection'), run: onConnect}
  } else if (screen === 'disabled') {
    step = 0; tone = 'warn'
    title = L('Hesabında henüz etkin değil', 'Not enabled for your account yet')
    message = t('disabled.body')
  } else if (screen === 'onboarding') {
    step = 1
    title = L('Kâr, kayıp ve tavanı sen belirle', 'Set your profit, loss and cap')
    message = L('Üç rakamı gir: kâr hedefi, en fazla kayıp ve toplam marjin tavanı. Kaldıraç 10×. Girdiğin değerler açılacak her pozisyon için aynı geçerli olur; tavan, aynı anda kullanılacak toplam teminatı sınırlar.', 'Enter three numbers: profit target, max loss and total margin cap. Leverage is 10×. Your values apply to every position it opens; the cap limits the total collateral used at once.')
    action = {label: L('Ayarlara git', 'Go to settings'), run: () => scrollTo('farmer-setup')}
    ask = L('KAIS FARMER için kâr hedefi, en fazla kayıp ve toplam tavanı nasıl seçmeliyim? 10x kaldıraçta nelere dikkat etmeliyim?', 'How should I pick profit target, max loss and total cap for KAIS FARMER at 10x leverage?')
  } else if (state === 'RUNNING') {
    step = 4
    title = positions > 0 ? L(`${positions} pozisyon yönetiliyor`, `Managing ${positions} position${positions === 1 ? '' : 's'}`) : L('Fırsat bekliyor', 'Waiting for a setup')
    const why = t(whyKey(status.why.code))
    message = positions > 0
      ? L('Her pozisyonun stop ve kâr emri borsada duruyor. Yeni giriş yalnız kurallara uyan kurulum çıkınca açılır.', 'Every position has its stop and target on the exchange. New entries open only when a qualified setup appears.')
      : `${why} ${L('Boş durması bir sorun değil; zorla işlem açmaz.', 'Idle is not a problem; it never forces a trade.')}`
    ask = L('KAIS FARMER şu an ne yapıyor ve neden pozisyon açmıyor?', 'What is KAIS FARMER doing right now and why is it not opening positions?')
  } else if (state === 'ENTRY_PAUSED_ARM' || (state === 'OFF' && !armOn)) {
    step = 2; tone = 'warn'
    title = state === 'OFF' ? L('İşlem oturumunu aç (ARM)', 'Open a trading session (ARM)') : L('Oturum süresi doldu', 'Session expired')
    message = L('Yeni pozisyon açabilmesi için geçerli bir işlem oturumu gerekir. Açık pozisyonların korunması sürer; yalnız yeni giriş durur.', 'A valid trading session is needed to open new positions. Open positions stay protected; only new entries pause.')
    action = {label: L('Oturumu yenile', 'Renew session'), run: onRenew, disabled: busy}
  } else if (state === 'ENTRY_PAUSED_SAFETY') {
    step = 4; tone = 'warn'
    title = L('Yeni giriş duraklatıldı', 'New entries paused')
    message = t(whyKey(status.why.code))
    ask = L('KAIS FARMER neden yeni giriş açmayı durdurdu?', 'Why did KAIS FARMER pause new entries?')
  } else if (state === 'INTERVENTION_REQUIRED') {
    step = 4; tone = 'stop'
    title = L('Dikkatin gerekiyor', 'Your attention is needed')
    message = L('Bir pozisyon için güvenli devam edilemedi. Koruma emirleri iptal edilmez. Aşağıdaki durumu oku ve gerekirse Kais’e sor.', 'A position could not safely continue. Protection orders are not cancelled. Read the status below and ask Kais if needed.')
  } else if (state === 'STOPPING') {
    step = 4; tone = 'warn'
    title = L('Durduruluyor', 'Stopping')
    message = L('Yeni giriş yok. Pozisyonlar kapanınca KAIS FARMER kapanır.', 'No new entries. KAIS FARMER switches off once positions close.')
  } else {
    step = 3
    title = L('Hazırsın, etkinleştir', 'Ready — activate')
    message = L('Ayarların kayıtlı ve oturum açık. Etkinleştirince KAIS FARMER nitelikli kurulum çıktıkça gerçek pozisyon açar. Hedefler garanti değildir.', 'Settings saved and session open. Once activated, KAIS FARMER opens real positions when a qualified setup appears. Targets are not guarantees.')
    action = {label: L('Etkinleştir’e git', 'Go to activate'), run: () => scrollTo('farmer-status')}
    ask = L('KAIS FARMER’ı etkinleştirmeden önce neleri kontrol etmeliyim?', 'What should I check before activating KAIS FARMER?')
  }

  const checks: Array<{text: string; ok: boolean}> = [
    {text: L('Hesap bağlı', 'Account connected'), ok: screen !== 'account_unavailable'},
    {text: configured ? L('Ayarlar kayıtlı', 'Settings saved') : L('Ayarlar girilmedi', 'Settings not entered'), ok: configured},
    {text: armOn ? L(`Oturum açık${arm ? ` · ${arm}` : ''}`, `Session open${arm ? ` · ${arm}` : ''}`) : L('Oturum kapalı', 'Session closed'), ok: armOn},
  ]

  // Akıllı öneriler: YALNIZ sunucunun önizleme ve tarama verisinden; arayüz hesap yapmaz.
  const pct = (v: number | null | undefined) => (typeof v === 'number' && Number.isFinite(v) ? `%${v.toFixed(2).replace('.', ',')}` : '—')
  const tips: Array<{tone: 'ok' | 'warn'; text: string}> = []
  if (preview && (screen === 'onboarding' || screen === 'dashboard') && state !== 'RUNNING') {
    if (preview.warnings.includes('STOP_TOO_TIGHT')) tips.push({tone: 'warn', text: L(`Stop mesafen fiyatın yalnızca ${pct(preview.stop_pct)}'i. ${preview.leverage}× kaldıraçta normal dalgalanma stop'u vurabilir; en fazla kaybı biraz artırmayı düşün.`, `Your stop is only ${pct(preview.stop_pct)} of price. At ${preview.leverage}× normal noise can hit it; consider a larger max loss.`)})
    if (preview.warnings.includes('TARGET_TOO_SMALL')) tips.push({tone: 'warn', text: L(`İşlem maliyeti hedefinin yaklaşık %${Math.round((preview.cost_over_target ?? 0) * 100)}'ini yiyor. Hedefi biraz büyütmek daha anlamlı olur.`, `Costs eat about ${Math.round((preview.cost_over_target ?? 0) * 100)}% of your target. A slightly larger target makes more sense.`)})
    if (preview.warnings.includes('MARGIN_CAP_LIMITS')) tips.push({tone: 'warn', text: L(`Tavanın aynı anda en fazla ${preview.effective_capacity} pozisyona izin veriyor.`, `Your cap allows at most ${preview.effective_capacity} positions at once.`)})
    if (!tips.length && preview.verdict === 'GOOD') tips.push({tone: 'ok', text: L(`Rakamların dengeli: hedef ${pct(preview.target_pct)}, stop ${pct(preview.stop_pct)} fiyat hareketi gerektiriyor.`, `Your numbers look balanced: target needs ${pct(preview.target_pct)}, stop ${pct(preview.stop_pct)} of price movement.`)})
    if (preview.verdict === 'NOT_RECOMMENDED') tips.unshift({tone: 'warn', text: L('Bu rakamlarla pozisyon kurulamıyor: kayıp, tahmini maliyetin altında kalıyor.', 'These numbers cannot form a position: the loss is below estimated costs.')})
  }
  if (state === 'RUNNING' && status.scanner.length > 0) {
    const qualified = status.scanner.filter(row => row.status === 'QUALIFIED').length
    tips.push({tone: 'ok', text: L(`Şu an ${status.scanner.length} aday izleniyor, ${qualified} tanesi nitelikli.`, `Watching ${status.scanner.length} candidates, ${qualified} qualified.`)})
  }
  if (state === 'RUNNING' && status.today && status.today.verified_net_pnl !== null) tips.push({tone: status.today.verified_net_pnl < 0 ? 'warn' : 'ok', text: L(`Bugünkü doğrulanmış net sonuç ${money(status.today.verified_net_pnl, true)}.`, `Today's verified net result is ${money(status.today.verified_net_pnl, true)}.`)})
  const shown = preview ?? null
  const tiles: Array<{k: string; v: string}> = []
  if (cfg && configured) tiles.push({k: L('Kâr hedefi', 'Target'), v: money(cfg.target_usdt, true)}, {k: L('En fazla kayıp', 'Max loss'), v: `-${money(cfg.max_loss_usdt).replace('-', '')}`}, {k: L('Toplam tavan', 'Total cap'), v: `${cfg.total_margin_cap} USDT`}, {k: L('Kaldıraç', 'Leverage'), v: `${cfg.leverage}×`})
  else if (shown && screen === 'onboarding') tiles.push({k: L('Pozisyon', 'Position'), v: `${shown.notional_usdt} USDT`}, {k: L('Kaldıraç', 'Leverage'), v: `${shown.leverage}×`}, {k: L('Aynı anda', 'At once'), v: `${shown.effective_capacity}`})

  return <section className="fmGuideCard" data-tone={tone} data-step={step} data-testid="farmer-guide-card" aria-live="polite">
    <header className="fmGcHead">
      <span className="fmGcEye" aria-hidden="true"/>
      <div><b>KAIS REHBER</b><small>{L(`Adım ${step + 1} / ${steps.length} · ${steps[step]}`, `Step ${step + 1} / ${steps.length} · ${steps[step]}`)}</small><em>{L('Seçili strateji: KAIS FARMER', 'Selected strategy: KAIS FARMER')}</em></div>
      <span className="fmGcRing" role="img" aria-label={L(`Adım ${step + 1} / ${steps.length}`, `Step ${step + 1} of ${steps.length}`)} style={{'--p': `${Math.round(((step + 1) / steps.length) * 100)}%`} as CSSProperties}><b>{step + 1}</b></span>
    </header>
    <ol className="fmGcSteps" aria-label={L('KAIS FARMER adımları', 'KAIS FARMER steps')}>{steps.map((label, index) => <li key={label} data-state={index < step ? 'done' : index === step ? 'current' : 'todo'} aria-current={index === step ? 'step' : undefined}><i>{index < step ? <Check aria-hidden="true"/> : index + 1}</i><span>{label}</span></li>)}</ol>
    <h3 className="fmGcTitle">{title}</h3>
    <p className="fmGcMsg">{message}</p>
    {tiles.length > 0 && <dl className="fmGcTiles">{tiles.map(tile => <div key={tile.k}><dt>{tile.k}</dt><dd>{tile.v}</dd></div>)}</dl>}
    {tips.length > 0 && <ul className="fmGcTips" aria-label={L('Akıllı öneriler', 'Smart tips')}>{tips.slice(0, 3).map(tip => <li key={tip.text} data-tone={tip.tone}><i aria-hidden="true">{tip.tone === 'ok' ? '✦' : '!'}</i>{tip.text}</li>)}</ul>}
    <ul className="fmGcPills" aria-label={L('Durum', 'Status')}>{checks.map(item => <li key={item.text} data-ok={item.ok || undefined}><i aria-hidden="true">{item.ok ? '✓' : '○'}</i>{item.text}</li>)}</ul>
    <div className="fmGcActions">
      {action && <button type="button" className="fmGcBtn fmGcPrimary" data-testid="farmer-guide-action" disabled={action.disabled} onClick={action.run}><Zap aria-hidden="true"/>{action.label}</button>}
      <button type="button" className="fmGcBtn" onClick={() => onAsk(ask)}><MessageCircle aria-hidden="true"/>{L('Kais’e sor', 'Ask Kais')}</button>
      <button type="button" className="fmGcBtn" onClick={onOpenGuide}><BookOpen aria-hidden="true"/>{L('Rehberi aç', 'Open guide')}</button>
    </div>
  </section>
}
