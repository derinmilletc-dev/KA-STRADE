import { Children, cloneElement, createContext, isValidElement, type MouseEvent as ReactMouseEvent, type ReactElement, type ReactNode, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Crown, LockKeyhole, X } from 'lucide-react'
import { API_BASE, userSessionToken } from './api'
import {emitKaisTargetReaction} from './kais-reactions'
import {clearKaisChatHistory, resumeKaisChatHistory} from './frontend/src/kais-chat-storage'
import {setStaffView} from './error-visibility'
import {PAID_PLAN_AMOUNT} from './smart-trade-plans'
import './premium-access.css'
import {showToast} from './toast-center'

type MemberAccess = {premium: boolean; ready: boolean; userId: string | null; displayName?: string; owner: boolean; error: string; assistantAllowed:boolean; freeTrial:boolean; liveAllowed:boolean; automationAllowed?:boolean; openUpgrade: (label: string, kind?: 'master') => void}
const missingProvider = () => { throw new Error('MemberAccessProvider is required for premium interactions.') }
const AccessContext = createContext<MemberAccess>({premium: false, ready: false, userId: null, owner: false, error: '', assistantAllowed:false, freeTrial:false, liveAllowed:false, openUpgrade: missingProvider})
export const useMemberAccess = () => useContext(AccessContext)

export function MemberAccessProvider({children}: {children: ReactNode}) {
  const [access, setAccess] = useState<Omit<MemberAccess, 'openUpgrade'>>({premium: false, ready: false, userId: null, owner: false, error: '', assistantAllowed:false,freeTrial:false,liveAllowed:false})
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [lockedLabel, setLockedLabel] = useState('')
  const [upgradeKind, setUpgradeKind] = useState<'both' | 'master'>('both')
  const [toast, setToast] = useState('')
  const toastTimer = useRef<number | null>(null)
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    let controller = new AbortController()
    const refreshAccess = () => {
      controller.abort()
      controller = new AbortController()
      const requestController = controller
      const sessionToken = userSessionToken()
      if (!sessionToken) {
        clearKaisChatHistory()
        setStaffView(false)
        setAccess({premium: false, ready: true, userId: null, owner: false, error: 'Üyelik oturumu gerekli.',assistantAllowed:false,freeTrial:false,liveAllowed:false})
        return
      }
      void fetch(`${API_BASE}/v22/profile`, {signal: requestController.signal})
        .then(async response => {
          if (response.status === 401 && !requestController.signal.aborted && userSessionToken() === sessionToken) clearKaisChatHistory()
          const payload = await response.json() as {user?: {id?: string; role?: string; display_name?: string}; access?: {isPremium?: boolean;assistantAllowed?:boolean;freeTrial?:boolean;canExecuteLive?:boolean;entitlements?:{canUseAutomation?:boolean}}; detail?: string}
          if (!response.ok || !payload.user?.id) throw new Error(payload.detail || 'Üyelik yetkisi doğrulanamadı.')
          if (!requestController.signal.aborted) {
            setAccess({premium: payload.access?.isPremium === true || payload.user.role === 'OWNER', ready: true, userId: payload.user.id, displayName: typeof payload.user.display_name === 'string' ? payload.user.display_name : '', owner: payload.user.role === 'OWNER', error: '',
              assistantAllowed:payload.access?.assistantAllowed !== false,freeTrial:payload.access?.freeTrial === true,
              liveAllowed:payload.access?.canExecuteLive === true || payload.user.role === 'OWNER',
              automationAllowed:payload.access?.entitlements?.canUseAutomation !== false})
            setStaffView(payload.user.role === 'OWNER')
            if (userSessionToken() === sessionToken) resumeKaisChatHistory(payload.user.id)
          }
        })
        .catch((error: unknown) => {
          if (requestController.signal.aborted) return
          setStaffView(false)
          setAccess({premium: false, ready: true, userId: null, owner: false, error: error instanceof Error ? error.message : 'Üyelik yetkisi doğrulanamadı.',assistantAllowed:false,freeTrial:false,liveAllowed:false})
        })
    }
    refreshAccess()
    const timer = window.setInterval(refreshAccess, 60000)
    window.addEventListener('focus', refreshAccess)
    window.addEventListener('protrebot-access-refresh', refreshAccess)
    return () => {
      controller.abort()
      window.clearInterval(timer)
      window.removeEventListener('focus', refreshAccess)
      window.removeEventListener('protrebot-access-refresh', refreshAccess)
    }
  }, [])
  useEffect(() => {
    if (!upgradeOpen || !dialog.current) return
    const previousFocus = document.activeElement
    const element = dialog.current
    element.showModal()
    emitKaisTargetReaction('premium-open', element)
    return () => {
      element.close()
      if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus()
    }
  }, [upgradeOpen])
  useEffect(() => () => { if (toastTimer.current !== null) window.clearTimeout(toastTimer.current) }, [])
  const openUpgrade = (label: string, kind?: 'master') => {
    if (!access.ready || !access.userId) return
    if (kind === 'master') { setUpgradeKind('master'); setLockedLabel(label); setUpgradeOpen(true); return }
    setUpgradeKind('both')
    const seenKey = `protrebot-premium-card-seen:${access.userId}`
    if (!access.freeTrial && sessionStorage.getItem(seenKey)) {
      setToast(`${label} için Premium üyelik gerekli.`)
      showToast({title: 'Premium gerekli', body: `${label} için Premium üyelik gerekli.`, tone: 'warn', action: {label: "Premium'a geç", href: '/pricing'}})
      if (toastTimer.current !== null) window.clearTimeout(toastTimer.current)
      toastTimer.current = window.setTimeout(() => setToast(''), 4500)
    } else {
      sessionStorage.setItem(seenKey, '1')
      setLockedLabel(label)
      setUpgradeOpen(true)
    }
  }
  return <AccessContext.Provider value={{...access, openUpgrade}}>
    {access.error && <p className="memberAccessError" role="alert">{access.error}</p>}{children}
    {createPortal(<>
      {upgradeOpen && upgradeKind === 'master' && <dialog ref={dialog} className="premiumDialog isMasterOnly" aria-labelledby="premium-title" aria-describedby="premium-description" onCancel={event => { event.preventDefault(); setUpgradeOpen(false) }} onClick={event => { if (!(event.target as HTMLElement).closest('a')) setUpgradeOpen(false) }}>
        <div className="premiumCard">
          <button className="premiumClose" type="button" aria-label="Kapat" onClick={() => setUpgradeOpen(false)}><X aria-hidden="true"/></button>
          <span className="premiumBadge" aria-hidden="true"><Crown/></span>
          <small className="premiumEyebrow">MASTER MODE</small>
          <h2 id="premium-title">{lockedLabel} Master Mode ile açılır</h2>
          <p id="premium-description">Smart Trade'de canlı işlem elle yapılır. Canlı stratejiler ve Auto Trade için Master Mode'a geçebilirsin.</p>
          <article className="premiumPlan isMaster">
            <header><span>MASTER MODE</span><strong>{PAID_PLAN_AMOUNT.MASTER_MODE}<small> USDT / 30 gün</small></strong></header>
            <ul><li><Check aria-hidden="true"/>Canlı işlem ve Auto Trade</li><li><Check aria-hidden="true"/>KAIS stratejileri canlıda</li><li><Check aria-hidden="true"/>Öncelikli, hızlı 7/24 destek</li></ul>
          </article>
          <div className="premiumCardActions isSingle"><a className="premiumPrimary" href="/billing">Master Mode'a geç</a></div>
          <p className="premiumFoot">Herhangi bir yere dokunarak kapatabilirsin.</p>
        </div>
      </dialog>}
      {upgradeOpen && upgradeKind === 'both' && <dialog ref={dialog} className="premiumDialog" aria-labelledby="premium-title" aria-describedby="premium-description" onCancel={event => { event.preventDefault(); setUpgradeOpen(false) }} onClick={event => { if (event.target === event.currentTarget) setUpgradeOpen(false) }}>
        <div className="premiumCard">
          <button className="premiumClose" type="button" aria-label="Kapat" onClick={() => setUpgradeOpen(false)}><X aria-hidden="true"/></button>
          <span className="premiumBadge" aria-hidden="true"><LockKeyhole/></span>
          <small className="premiumEyebrow">ÜCRETLİ ÖZELLİK</small>
          <h2 id="premium-title">{lockedLabel} ücretli planlarda açılır</h2>
          <p id="premium-description">{access.freeTrial ? 'Denemende analizleri ve demo işlemleri sınırlar içinde kullanabilirsin. Gerçek parayla işlem için bir plan seç.' : 'Bu özellik ücretli üyeliğe dahildir.'} Mevcut işlem güvenlik kapıları aynen geçerlidir.</p>
          <div className="premiumPlans">
            <article className="premiumPlan isSmart">
              <header><span>SMART TRADE</span><strong>{PAID_PLAN_AMOUNT.SMART_TRADE}<small> USDT / 30 gün</small></strong></header>
              <ul><li><Check aria-hidden="true"/>Manuel işlem · Demo ve Canlı</li><li><Check aria-hidden="true"/>KAIS AI asistanı</li><li><Check aria-hidden="true"/>7/24 destek</li></ul>
            </article>
            <article className="premiumPlan isMaster">
              <i className="premiumPlanTag"><Crown aria-hidden="true"/>ÖNERİLEN</i>
              <header><span>MASTER MODE</span><strong>{PAID_PLAN_AMOUNT.MASTER_MODE}<small> USDT / 30 gün</small></strong></header>
              <ul><li><Check aria-hidden="true"/>Canlı işlem ve Auto Trade</li><li><Check aria-hidden="true"/>KAIS stratejileri canlıda</li><li><Check aria-hidden="true"/>Öncelikli, hızlı 7/24 destek</li></ul>
            </article>
          </div>
          <div className="premiumCardActions"><a className="premiumPrimary" href="/billing">Planları gör</a><button className="premiumSecondary" type="button" onClick={() => setUpgradeOpen(false)}>Şimdi değil</button></div>
          <p className="premiumFoot">Otomatik ücret yok. Ödeme Binance Pay ile, manuel doğrulanır.</p>
        </div>
      </dialog>}
    </>, document.body)}
  </AccessContext.Provider>
}

export function PremiumBoundary({label, children, compact = false}: {label: string; children?: ReactNode; compact?: boolean}) {
  const {premium, ready, userId, openUpgrade} = useMemberAccess()
  if (premium) return children
  return <section className={`premiumLocked${compact ? ' premiumLockedCompact' : ''}`} data-premium-locked={label}>
    {!compact && <div className="premiumPlaceholder" aria-hidden="true"><span/><span/><span/></div>}
    <button type="button" disabled={!ready || !userId} onClick={() => openUpgrade(label)}><LockKeyhole aria-hidden="true"/><span>{label} · Premium</span></button>
  </section>
}

type PresentationElement = ReactElement<{className?: string; children?: ReactNode; 'aria-label'?: string}>
const PRIVATE_CLASSES = new Set([
  'decisionList', 'masterTradeCases', 'triggerMonitor', 'masterTradeScannerDetails',
  'liveUxOrderGrid', 'masterTradeLiveConnectionCard', 'masterTradeLiveConfirmations',
  'masterTradeLiveControl', 'masterTradeLiveSignalGrid',
])
const LOCK_LABELS: Record<string, string> = {
  decisionList: 'Why this decision', masterTradeCases: 'Long / Short case',
  triggerMonitor: 'Trigger monitor', masterTradeScannerDetails: 'İlk 3 fırsat detayı',
  liveUxOrderGrid: 'Manuel emir ve dry-run', masterTradeLiveConnectionCard: 'Connect Account',
  masterTradeLiveConfirmations: 'LIVE onay akışı', masterTradeLiveControl: 'Arm ve Auto Trade',
  masterTradeLiveSignalGrid: 'Giriş / SL / TP seviyeleri',
}

function buttonText(node: ReactNode): string {
  return Children.toArray(node).map(child => typeof child === 'string' ? child : isValidElement(child) ? buttonText((child as PresentationElement).props.children) : '').join(' ')
}

function restrictTree(node: ReactNode): ReactNode {
  if (!isValidElement(node)) return node
  const element = node as PresentationElement
  const privateClass = element.props.className?.split(' ').find(className => PRIVATE_CLASSES.has(className))
  if (privateClass) return <PremiumBoundary key={element.key} label={LOCK_LABELS[privateClass]}/>
  const label = element.type === 'button' ? buttonText(element.props.children) : ''
  if (label && /START LIVE AUTO TRADE|CONNECT ACCOUNT|ARM LIVE|DRY.RUN|LIVE ORDER|LEVELS|SAVE SETUP|24 SAAT İZİN VER|LİMİTLERİ ONAYLA/i.test(label)) {
    return <PremiumBoundary key={element.key} label={label.trim()} compact/>
  }
  if (element.props.children === undefined) return element
  return cloneElement(element, {children: Children.map(element.props.children, restrictTree)})
}

export function PremiumWorkspace({children}: {children: ReactNode}) {
  const {premium} = useMemberAccess()
  return premium ? children : Children.map(children, restrictTree)
}

const GATE_ACTION = 'button, input, select, textarea, [role="button"]'

/** Deneme üyesi canlı işlem alanını görür; içindeki işlem düğmelerine basınca ücretli plan penceresi açılır. */
export function TrialLiveGate({children}: {children: ReactNode}) {
  const {freeTrial, liveAllowed, ready, openUpgrade} = useMemberAccess()
  if (!ready || !freeTrial || liveAllowed) return <>{children}</>
  const onClickCapture = (event: ReactMouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement
    if (target.closest('[aria-expanded]')) return
    const x = event.clientX, y = event.clientY
    const hit = target.closest(GATE_ACTION) ?? Array.from(event.currentTarget.querySelectorAll<HTMLElement>(GATE_ACTION)).find(element => {
      if (element.hasAttribute('aria-expanded')) return false
      const box = element.getBoundingClientRect()
      return box.width > 0 && box.height > 0 && x >= box.left && x <= box.right && y >= box.top && y <= box.bottom
    })
    if (!hit) return
    event.preventDefault(); event.stopPropagation()
    openUpgrade('Canlı işlem')
  }
  return <div className="trialLiveGate" onClickCapture={onClickCapture}>{children}</div>
}

/** Smart Trade üyesi Master Mode'a özel alana basınca tek kartlı Master Mode penceresi açılır. */
export function MasterOnlyGate({children, label}: {children: ReactNode; label: string}) {
  const {premium, owner, ready, automationAllowed, freeTrial, openUpgrade} = useMemberAccess()
  if (!ready || !premium || owner || freeTrial || automationAllowed !== false) return <>{children}</>
  const onClickCapture = (event: ReactMouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement
    if (target.closest('[aria-expanded]')) return
    const x = event.clientX, y = event.clientY
    const hit = target.closest(GATE_ACTION) ?? Array.from(event.currentTarget.querySelectorAll<HTMLElement>(GATE_ACTION)).find(element => {
      if (element.hasAttribute('aria-expanded')) return false
      const box = element.getBoundingClientRect()
      return box.width > 0 && box.height > 0 && x >= box.left && x <= box.right && y >= box.top && y <= box.bottom
    })
    if (!hit) return
    event.preventDefault(); event.stopPropagation()
    openUpgrade(label, 'master')
  }
  return <div className="trialLiveGate" onClickCapture={onClickCapture}>{children}</div>
}
