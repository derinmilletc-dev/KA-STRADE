import {useEffect, useState, type CSSProperties, type ReactNode} from 'react'
import {BookOpen, Check, MessageCircle, Zap} from 'lucide-react'
import {money, tr_, whyKey, type CopyKey, type FarmerPreview, type FarmerStatus, type Screen} from './farmerView'

/** KAIS FARMER için KAIS REHBER / KAIS KOÇ kartı. Kais Long ve DCA rehberiyle AYNI yapı ve AYNI sınıflar (lcGuide…):
 *  kurulumda adım adım yönlendirir, çalışırken KAIS KOÇ olur (mum sayacı, oturum, kontrol listesi, koç yorumu).
 *  Yalnız sunucudan gelen durumu gösterir; emir göndermez. Tek hesap yapılan şey saat: 15 dakikalık mum kapanışına kalan süre. */
type Tone = 'go' | 'warn' | 'stop' | 'wait'
export type FarmerGuideCardProps = {
  status: FarmerStatus; screen: Screen; L: (tr: string, en: string) => string; now: number; preview: FarmerPreview | null
  onConnect?: () => void; onRenew: () => void; onOpenGuide: () => void; onAsk: (question: string) => void; busy: boolean; children?: ReactNode; editing?: boolean; wizard?: boolean
  /** Kokpit, KAIS KOÇ görünümü için ızgaraya `data-coach` koyar (Kais Long ile aynı stiller). */
  onCoachChange?: (on: boolean) => void
}

const CANDLE = 900
const pad2 = (n: number) => String(n).padStart(2, '0')
const mmss = (t: number) => `${pad2(Math.floor(t / 60))}:${pad2(Math.floor(t % 60))}`
const hms = (t: number) => `${pad2(Math.floor(t / 3600))}:${pad2(Math.floor((t % 3600) / 60))}:${pad2(Math.floor(t % 60))}`
const clockAt = (sec: number) => new Date(sec * 1000).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})

function scrollTo(testId: string) {
  const el = document.querySelector(`[data-testid="${testId}"]`)
  if (el instanceof HTMLElement) { if (el instanceof HTMLDetailsElement) el.open = true; el.scrollIntoView({behavior: 'smooth', block: 'center'}) }
}

export default function FarmerGuideCard({status, screen, L, now: initialNow, preview, onConnect, onRenew, onOpenGuide, onAsk, busy, onCoachChange, children, editing, wizard}: FarmerGuideCardProps) {
  // Sayaç saniyede bir akar (yalnız görünüm).
  const [now, setNow] = useState(initialNow)
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now() / 1000), 1000); return () => window.clearInterval(id) }, [])
  const [koView, setKoView] = useState<'candle' | 'session'>('candle')

  const t = (key: CopyKey) => tr_(L('tr', 'en') as 'tr' | 'en', key)
  const steps = [L('Hesap', 'Account'), L('Ayarlar', 'Settings'), L('Oturum', 'Session'), L('Başlat', 'Start')]
  const cfg = status.config
  const configured = status.configured
  const armOn = !!status.arm.active
  const state = status.state
  const positions = status.positions.length
  const capacity = Number(status.effective_capacity ?? 0)
  const sessionLeft = armOn && status.arm.expires_at ? Math.max(0, status.arm.expires_at - now) : 0
  const coach = screen === 'dashboard' && (state === 'RUNNING' || state === 'ENTRY_PAUSED_SAFETY' || state === 'STOPPING' || (state === 'ENTRY_PAUSED_ARM' && positions > 0))

  useEffect(() => { onCoachChange?.(coach) }, [coach, onCoachChange])
  useEffect(() => () => onCoachChange?.(false), [onCoachChange])

  // ---------------------------------------------------------------- rehber (kurulum) ve koç metni
  let step = 0
  let tone: Tone = 'go'
  let title = ''
  let message = ''
  let action: {label: string; run: () => void; disabled?: boolean} | null = null
  let ask = L('KAIS FARMER nasıl çalışıyor? Kurulumu adım adım anlat.', 'How does KAIS FARMER work? Walk me through the setup.')

  if (screen === 'account_unavailable') {
    tone = 'warn'
    title = L('Önce Binance hesabını bağla', 'Connect your Binance account first')
    message = L('KAIS FARMER canlı hesabındaki bakiye ve pozisyonlarla çalışır. Bağlantıdan sonra buradan devam edersin.', 'KAIS FARMER works from your live balance and positions. After connecting you continue here.')
    if (onConnect) action = {label: L('Bağlantıyı aç', 'Open connection'), run: onConnect}
  } else if (screen === 'disabled') {
    tone = 'warn'
    title = L('Canlı işlem şu an kapalı', 'Live trading is off right now')
    message = t('disabled.body')
  } else if (screen === 'onboarding' || editing) {
    step = 1
    title = L('Kâr, kayıp ve tavanı sen belirle', 'Set your profit, loss and cap')
    message = L('Üç rakamı gir: kâr hedefi, en fazla kayıp ve toplam marjin tavanı. Kaldıraç 10×. Girdiğin değerler açılacak her pozisyona aynı uygulanır; tavan aynı anda kullanılacak toplam teminatı sınırlar.', 'Enter three numbers: profit target, max loss and total margin cap. Leverage is 10×. Your values apply to every position; the cap limits total collateral used at once.')
    action = wizard ? null : {label: L('Ayarlara git', 'Go to settings'), run: () => scrollTo('farmer-setup')}
    ask = L('KAIS FARMER için kâr hedefi, en fazla kayıp ve toplam tavanı nasıl seçmeliyim? 10x kaldıraçta nelere dikkat etmeliyim?', 'How should I pick profit target, max loss and total cap for KAIS FARMER at 10x?')
  } else if (coach) {
    tone = state === 'RUNNING' ? 'go' : state === 'STOPPING' ? 'wait' : 'warn'
    title = state === 'STOPPING' ? L('Durduruluyor', 'Stopping')
      : state === 'ENTRY_PAUSED_SAFETY' ? L('Yeni giriş duraklatıldı', 'New entries paused')
      : state === 'ENTRY_PAUSED_ARM' ? L('Oturum bitti · pozisyonlar korunuyor', 'Session ended · positions protected')
      : positions > 0 ? L(`${positions} pozisyon yönetiliyor`, `Managing ${positions} position${positions === 1 ? '' : 's'}`) : L('Çalışıyor · fırsat bekliyor', 'Running · waiting for a setup')
    message = state === 'STOPPING' ? L('Yeni giriş yok. Pozisyonlar kapanınca KAIS FARMER kapanır.', 'No new entries. KAIS FARMER switches off once positions close.')
      : state === 'RUNNING' && positions === 0 ? `${t(whyKey(status.why.code))} ${L('Boş durması sorun değil; zorla işlem açmaz.', 'Idle is fine; it never forces a trade.')}`
      : state === 'RUNNING' ? L('Her pozisyonun stop ve kâr emri borsada duruyor. Yeni giriş yalnız kurallara uyan kurulum çıkınca açılır.', 'Every position has its stop and target on the exchange. New entries open only for qualified setups.')
      : t(whyKey(status.why.code))
    if (state === 'ENTRY_PAUSED_ARM') action = {label: L('Oturumu yenile', 'Renew session'), run: onRenew, disabled: busy}
    ask = L('KAIS FARMER şu an ne yapıyor, pozisyonlarım nasıl ve neden yeni işlem açmıyor?', 'What is KAIS FARMER doing, how are my positions and why no new trade?')
  } else if (state === 'INTERVENTION_REQUIRED') {
    tone = 'stop'; step = 3
    title = L('Dikkatin gerekiyor', 'Your attention is needed')
    message = L('Bir pozisyon güvenle devam edemedi. Koruma emirleri iptal edilmez. Aşağıdaki durumu oku, gerekirse Kais’e sor.', 'A position could not safely continue. Protection orders stay. Read the status below and ask Kais if needed.')
  } else if (!armOn) {
    tone = 'warn'; step = 2
    title = state === 'ENTRY_PAUSED_ARM' ? L('Oturum süresi doldu', 'Session expired') : L('İşlem oturumunu aç', 'Open a trading session')
    message = L('Yeni pozisyon açabilmesi için geçerli bir işlem oturumu (ARM) gerekir. Tek dokunuşla açılır.', 'A valid trading session (ARM) is needed to open positions. One tap opens it.')
    action = {label: L('Oturumu aç', 'Open session'), run: onRenew, disabled: busy}
  } else {
    step = 3
    title = L('Hazırsın · etkinleştir', 'Ready · activate')
    message = L('Ayarların kayıtlı, oturum açık. Etkinleştirince KAIS FARMER nitelikli kurulum çıktıkça gerçek pozisyon açar ve KAIS KOÇ seni bilgilendirir. Hedefler garanti değildir.', 'Settings saved, session open. Once activated KAIS FARMER opens real positions on qualified setups and KAIS KOÇ keeps you posted. Targets are not guarantees.')
    action = {label: L('Etkinleştir’e git', 'Go to activate'), run: () => scrollTo('farmer-status')}
    ask = L('KAIS FARMER’ı etkinleştirmeden önce neleri kontrol etmeliyim?', 'What should I check before activating KAIS FARMER?')
  }

  // ---------------------------------------------------------------- akıllı öneriler (sunucu önizlemesinden)
  const pct = (v: number | null | undefined) => (typeof v === 'number' && Number.isFinite(v) ? `%${v.toFixed(2).replace('.', ',')}` : '—')
  const tips: Array<{tone: 'ok' | 'warn'; text: string}> = []
  if (!coach && preview && (screen === 'onboarding' || screen === 'dashboard')) {
    if (preview.verdict === 'NOT_RECOMMENDED') tips.push({tone: 'warn', text: L('Bu rakamlarla pozisyon kurulamıyor: kayıp tahmini maliyetin altında kalıyor.', 'These numbers cannot form a position: the loss is below estimated costs.')})
    if (preview.warnings.includes('STOP_TOO_TIGHT')) tips.push({tone: 'warn', text: L(`Stop mesafen fiyatın yalnız ${pct(preview.stop_pct)}'i. ${preview.leverage}× kaldıraçta normal dalgalanma stop'u vurabilir; en fazla kaybı biraz artır.`, `Your stop is only ${pct(preview.stop_pct)} of price. At ${preview.leverage}× noise can hit it; raise max loss a bit.`)})
    if (preview.warnings.includes('TARGET_TOO_SMALL')) tips.push({tone: 'warn', text: L(`İşlem maliyeti hedefinin yaklaşık %${Math.round((preview.cost_over_target ?? 0) * 100)}'ini yiyor. Hedefi biraz büyüt.`, `Costs eat about ${Math.round((preview.cost_over_target ?? 0) * 100)}% of your target. Raise it a bit.`)})
    if (preview.warnings.includes('MARGIN_CAP_LIMITS')) tips.push({tone: 'warn', text: L(`Tavanın aynı anda en fazla ${preview.effective_capacity} pozisyona izin veriyor.`, `Your cap allows at most ${preview.effective_capacity} positions at once.`)})
    if (!tips.length && preview.verdict === 'GOOD') tips.push({tone: 'ok', text: L(`Rakamların dengeli: hedef ${pct(preview.target_pct)}, stop ${pct(preview.stop_pct)} fiyat hareketi istiyor. Pozisyon büyüklüğü ${preview.notional_usdt} USDT.`, `Balanced: target needs ${pct(preview.target_pct)}, stop ${pct(preview.stop_pct)} of price. Position size ${preview.notional_usdt} USDT.`)})
  }

  // ---------------------------------------------------------------- KAIS KOÇ: mum sayacı, oturum, kontrol, yorum
  const candleLeft = CANDLE - (Math.floor(now) % CANDLE)
  const lastClose = Math.floor(now) - (Math.floor(now) % CANDLE)
  const justClosed = CANDLE - candleLeft < 15
  const koSession = koView === 'session' && armOn
  const koFrac = justClosed ? 1 : koSession ? Math.min(1, sessionLeft / 3600) : candleLeft / CANDLE
  const koDeg = Math.round(Math.max(0, Math.min(1, koFrac)) * 360)
  const koTicks = Math.round(Math.max(0, Math.min(1, koFrac)) * 60)
  const koLabel = justClosed ? L('MUM KAPANDI', 'CANDLE CLOSED') : koSession ? L('OTURUM', 'SESSION') : L('SONRAKİ MUM', 'NEXT CANDLE')
  const koBig = justClosed ? L('Taranıyor', 'Scanning') : koSession ? hms(sessionLeft) : mmss(candleLeft)
  const koSub = justClosed ? L('adaylar kontrol ediliyor', 'checking candidates') : koSession ? L('sayaca dokun: mum', 'tap: candle') : L(`kapanış ${clockAt(lastClose + CANDLE)}`, `closes ${clockAt(lastClose + CANDLE)}`)
  const qualified = status.scanner.filter(row => row.status === 'QUALIFIED').length
  const koSmart = positions > 0 ? L(`${positions} pozisyon korunuyor · stop ve hedef borsada`, `${positions} position${positions === 1 ? '' : 's'} protected · stop and target on exchange`)
    : status.scanner.length > 0 ? L(`${status.scanner.length} aday izleniyor · ${qualified} nitelikli`, `${status.scanner.length} candidates · ${qualified} qualified`)
    : L('Fırsat yok · sıradaki mumu bekliyor', 'No setup · waiting for the next candle')
  const today = status.today?.verified_net_pnl ?? null
  const coachChecks: Array<{ok: boolean; text: string}> = coach ? [
    {ok: true, text: L('Binance bağlı', 'Binance connected')},
    {ok: configured, text: cfg ? L(`Kâr ${money(cfg.target_usdt, true)} · Kayıp -${money(cfg.max_loss_usdt).replace('-', '')} · ${cfg.leverage}×`, `Profit ${money(cfg.target_usdt, true)} · Loss -${money(cfg.max_loss_usdt).replace('-', '')} · ${cfg.leverage}×`) : L('Ayar yok', 'No settings')},
    {ok: status.why.code !== 'DAILY_LOSS', text: status.why.code === 'DAILY_LOSS' ? L('Günlük kayıp sınırı doldu', 'Daily loss limit reached') : L('Günlük kayıp koruması açık', 'Daily loss protection on')},
    {ok: !armOn || sessionLeft > 3600, text: armOn ? L(`Oturum ${hms(sessionLeft)} kaldı`, `Session ${hms(sessionLeft)} left`) : L('Oturum kapalı · yeni giriş yok', 'Session closed · no new entries')},
  ] : []
  const coachNote = coach ? [
    positions === 0 && state === 'RUNNING' ? t(whyKey(status.why.code)) : null,
    today !== null ? L(`Bugün doğrulanmış net: ${money(today, true)}.`, `Today verified net: ${money(today, true)}.`) : null,
    capacity > 0 ? L(`Kapasite ${positions} / ${capacity}.`, `Capacity ${positions} / ${capacity}.`) : null,
  ].filter(Boolean).join(' ') : ''
  const timeline = coach ? [
    {label: L('Son mum', 'Last candle'), value: clockAt(lastClose), state: 'done'},
    {label: L('Sıradaki mum', 'Next candle'), value: `${clockAt(lastClose + CANDLE)} · ${mmss(candleLeft)}`, state: 'now'},
    {label: L('Pozisyon', 'Positions'), value: `${positions} / ${capacity || '—'}`, state: positions > 0 ? 'done' : 'todo'},
    {label: L('Oturum biter', 'Session ends'), value: armOn && status.arm.expires_at ? clockAt(status.arm.expires_at) : '—', state: 'todo'},
  ] : []

  // Kurulum listesi (Kais Long'daki ile aynı halka)
  const setupItems = [
    {ok: configured, text: L('Kâr, kayıp ve tavan', 'Profit, loss and cap'), fix: configured ? null : {label: L('ayarla', 'set'), run: () => scrollTo('farmer-setup')}},
    {ok: armOn, text: L('İşlem oturumu', 'Trading session'), fix: armOn ? null : {label: L('aç', 'open'), run: onRenew}},
    {ok: state !== 'OFF', text: L('Etkinleştir', 'Activate'), fix: null},
  ]
  const setupDone = setupItems.filter(item => item.ok).length
  const showSetup = !wizard && !coach && (screen === 'onboarding' || screen === 'dashboard')

  return <section className="lcGuide fmGuide" data-tone={tone} data-phase={coach ? 'running' : 'setup'} data-testid="farmer-guide-card" aria-live="polite">
    <header className="lcGuideHead">
      <span className="lcEye" aria-hidden="true" data-tone={tone}/>
      <div><b>{coach ? 'KAIS KOÇ' : 'KAIS REHBER'}</b><small>{coach ? L('Kurulum tamam · seni bilgilendiriyorum', 'Setup done · keeping you posted') : L(`Adım ${step + 1} / ${steps.length} · ${steps[step]}`, `Step ${step + 1} / ${steps.length} · ${steps[step]}`)}</small><em className="lcStrat" data-testid="strategy-name">{L('Seçili strateji: KAIS FARMER', 'Selected strategy: KAIS FARMER')}</em></div>
      {!coach && <span className="lcMobileOnly lcGuideRing" role="img" aria-label={`${step + 1} / ${steps.length}`} style={{'--p': `${Math.round(((step + 1) / steps.length) * 100)}%`} as CSSProperties}><b aria-hidden="true">{step + 1}/{steps.length}</b></span>}
      {coach && <span className="lcMobileOnly lcKoPill">{state === 'RUNNING' ? L('ÇALIŞIYOR', 'RUNNING') : L('DURAKLADI', 'PAUSED')}</span>}
      <span className="lcHeadBtns">
        <button type="button" className="lcAsk" onClick={() => onAsk(ask)}><MessageCircle aria-hidden="true"/>{L('Kais’e sor', 'Ask Kais')}</button>
      </span>
    </header>
    {!coach && <ol className="lcSteps" aria-label={L('KAIS FARMER adımları', 'KAIS FARMER steps')}>{steps.map((label, index) => <li key={label} data-state={index < step ? 'done' : index === step ? (tone === 'stop' ? 'problem' : 'current') : 'todo'} aria-current={index === step ? 'step' : undefined}><i/><span>{label}</span></li>)}</ol>}
    <h2 className="lcGuideTitle">{title}</h2>
    <p className="lcGuideMsg" data-open>{message}</p>
    {tips.map(tip => <p key={tip.text} className="lcHelp fmTip" data-tone={tip.tone}><b>{tip.tone === 'ok' ? L('Kais diyor ki:', 'Kais says:') : L('Dikkat:', 'Heads up:')}</b> {tip.text}</p>)}

    {coach && <ol className="lcTimeline" aria-label={L('KAIS FARMER zaman çizelgesi', 'KAIS FARMER timeline')}>{timeline.map(item => <li key={item.label} data-state={item.state}><i aria-hidden="true"/><small>{item.label}</small><b>{item.value}</b></li>)}</ol>}
    {coach && <p className="lcCoachLine" data-testid="farmer-entry-status">{state === 'RUNNING' ? L('Yeni giriş açık · kurallara uyan kurulum çıkınca stop ve hedefiyle pozisyon açılır.', 'New entries open · a qualified setup opens a position with stop and target.') : L('Yeni giriş beklemede · açık pozisyonların koruması sürüyor.', 'New entries on hold · open positions stay protected.')}</p>}
    {coach && <div className="lcCoachDeck" data-testid="farmer-coach-deck" style={{'--p': `${Math.round((1 - candleLeft / CANDLE) * 100)}%`} as CSSProperties}>
      <div className="lcCoachRing" role="img" aria-label={L(`Sonraki mum kapanışına ${mmss(candleLeft)}`, `${mmss(candleLeft)} to next candle close`)}>
        <div><b>{mmss(candleLeft)}</b><small>{L('sonraki mum', 'next candle')}</small></div></div>
      <div className="lcCoachTimes" aria-label={L('Mum saatleri', 'Candle times')}>
        <span><small>{L('Son mum', 'Last candle')}</small><b>{clockAt(lastClose)}</b></span>
        <span><small>{L('Sonraki mum', 'Next candle')}</small><b>{clockAt(lastClose + CANDLE)}</b></span>
        <i className="lcCoachBar" aria-hidden="true"/>
      </div>
      <div className="lcCoachBody">
        <small className="lcCoachCap">{L('KONTROL', 'CHECKS')}</small>
        <ul className="lcCoachChecks">{coachChecks.map(item => <li key={item.text} data-ok={item.ok || undefined}><i aria-hidden="true">{item.ok ? '✓' : '!'}</i>{item.text}</li>)}</ul>
        {coachNote && <p className="lcCoachNote"><small>{L('KOÇ YORUMU', 'COACH NOTE')}</small>{coachNote}</p>}
      </div>
    </div>}
    {coach && <div className="lcMobileOnly lcKo" data-phase={justClosed ? 'scan' : 'wait'} data-urgent={(!koSession && candleLeft <= 60) || undefined} data-testid="farmer-coach-ring">
      <button type="button" className="lcKoRing" data-size={koSession ? 'hms' : justClosed ? 'word' : 'time'} style={{'--deg': `${koDeg}deg`} as CSSProperties} aria-label={L('Sayaç. Dokununca oturum / mum arasında geçer', 'Counter. Tap to switch session / candle')} onClick={() => setKoView(value => value === 'candle' ? 'session' : 'candle')}>
        <svg className="lcKoSvg" viewBox="0 0 224 224" aria-hidden="true">{Array.from({length: 60}, (_, index) => <line key={index} x1="112" y1="6" x2="112" y2="17" transform={`rotate(${index * 6} 112 112)`} data-on={index < koTicks || undefined}/>)}</svg><i className="lcKoSweep" aria-hidden="true"/><i className="lcKoHead" aria-hidden="true"/>
        <span className="lcKoTxt"><small>{koLabel}</small><b>{koBig}</b><em>{koSub}</em></span>
      </button>
      <p className="lcKoSmart" role="status"><Check aria-hidden="true"/>{koSmart}</p>
    </div>}
    {coach && armOn && <div className="lcMobileOnly lcKoSession" data-urgent={(sessionLeft > 0 && sessionLeft <= 3600) || undefined}>
      <div><small>{L('OTURUM', 'SESSION')}</small><b>{sessionLeft > 0 ? L(`${hms(sessionLeft)} kaldı`, `${hms(sessionLeft)} left`) : '—'}</b></div>
      <i aria-hidden="true"><u style={{width: `${Math.min(100, Math.round(sessionLeft / 3600 * 100))}%`}}/></i>
    </div>}

    {showSetup && <div className="lcSetup" data-complete={setupDone === setupItems.length || undefined} aria-label={L('Kurulum durumu', 'Setup status')}>
      <i className="lcSetupRing" style={{'--p': `${Math.round(setupDone / setupItems.length * 100)}%`} as CSSProperties} aria-hidden="true"><b>{setupDone}/{setupItems.length}</b></i>
      <div className="lcSetupBody">
        <b>{setupDone === setupItems.length ? L('Kurulum tamam', 'Setup complete') : L(`${setupItems.length - setupDone} adım kaldı`, `${setupItems.length - setupDone} steps left`)}</b>
        <ul>{setupItems.map(item => <li key={item.text} data-ok={item.ok || undefined}>{item.ok ? '✓' : '○'} {item.text}{item.fix && <button type="button" className="lcLink" disabled={busy} onClick={item.fix.run}>{item.fix.label}</button>}</li>)}</ul>
      </div>
    </div>}

    <div className="lcGuideActions">
      {action && !wizard && <button type="button" className="lcBtn lcPrimary" data-testid="farmer-guide-action" disabled={action.disabled} onClick={action.run}><Zap aria-hidden="true"/>{action.label}</button>}
      <button type="button" className="lcBtn lcGhost" data-testid="farmer-open-guide" onClick={onOpenGuide}><BookOpen aria-hidden="true"/>{L('Rehberi aç', 'Open guide')}</button>
    </div>
    {children && <div className="fmInner" data-testid="farmer-inner">{children}</div>}
  </section>
}