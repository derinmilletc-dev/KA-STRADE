// Master Trade yatay tam ekran denetleyicisi.
// Yalnızca ön yüz: Fullscreen API ve Screen Orientation API özellik algılamasıyla kullanılır; emir, bağlantı
// veya analiz verisine dokunmaz. React'e bağımlı değildir (node testleri doğrudan yükler).

export type OrientationLockState = 'none' | 'locked' | 'unsupported' | 'failed'

export type LandscapeSnapshot = {
  /** Kullanıcı tam ekran düzenini açtı (dönme uyarısı veya yatay terminal görünür). */
  open: boolean
  /** Cihaz/pencere şu an yatay. */
  landscape: boolean
  /** Tarayıcı tam ekran modunda (Fullscreen API ile girildi). */
  fullscreen: boolean
  lock: OrientationLockState
}

type Listener = () => void
type Target = {addEventListener: (type: string, handler: (event: any) => void, options?: any) => void; removeEventListener: (type: string, handler: (event: any) => void, options?: any) => void}

type MediaListLike = {matches: boolean; addEventListener?: (type: 'change', handler: () => void) => void; removeEventListener?: (type: 'change', handler: () => void) => void}

export type LandscapeEnv = {
  window: Target & {innerWidth: number; innerHeight: number; history: History; matchMedia?: (query: string) => MediaListLike}
  document: Target & {
    documentElement: Element & {requestFullscreen?: (options?: FullscreenOptions) => Promise<void>; webkitRequestFullscreen?: () => Promise<void> | void}
    body: Element
    fullscreenElement?: Element | null
    webkitFullscreenElement?: Element | null
    fullscreenEnabled?: boolean
    exitFullscreen?: () => Promise<void>
    webkitExitFullscreen?: () => Promise<void> | void
  }
  screen: {orientation?: {type?: string; lock?: (orientation: 'landscape') => Promise<void>; unlock?: () => void; addEventListener?: (type: 'change', handler: () => void) => void; removeEventListener?: (type: 'change', handler: () => void) => void}}
}

export type LandscapeOptions = {
  /** Yön okuması bu kadar ms boyunca değişmeden kalınca onaylanır (düzen oturana kadar bekler). */
  settleMs?: number
}
/** Dönme sonrası yerleşim için varsayılan bekleme. */
export const ORIENTATION_SETTLE_MS = 300

export const FULLSCREEN_CLASS = 'kaisChartFullscreen'
export const HISTORY_MARKER = 'kaisChartFullscreen'

export function browserEnv(): LandscapeEnv {
  return {window: window as unknown as LandscapeEnv['window'], document: document as unknown as LandscapeEnv['document'], screen: screen as unknown as LandscapeEnv['screen']}
}

export const isLandscape = (width: number, height: number) => width > height

/**
 * Cihaz/pencere şu an gerçekten yatay mı? Tek bir sinyale güvenilmez: boyutlar yatay olmalı, `(orientation: landscape)`
 * medya sorgusu aynı fikirde olmalı ve Screen Orientation API varsa dikey demiyor olmalı. Tam ekran durumu yön kanıtı sayılmaz.
 */
export function readLandscape(env: LandscapeEnv): boolean {
  if (!isLandscape(env.window.innerWidth, env.window.innerHeight)) return false
  const query = env.window.matchMedia?.('(orientation: landscape)')
  if (query && !query.matches) return false
  const type = env.screen.orientation?.type
  if (typeof type === 'string' && type.startsWith('portrait')) return false
  return true
}

const fullscreenElementOf = (doc: LandscapeEnv['document']) => doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null

/** Tarayıcı bu belgeyi tam ekran yapabilir mi? (iPhone Safari yapamaz → elle döndürme yedeği.) */
export function canRequestFullscreen(doc: LandscapeEnv['document']): boolean {
  if (doc.fullscreenEnabled === false) return false
  return typeof doc.documentElement.requestFullscreen === 'function' || typeof doc.documentElement.webkitRequestFullscreen === 'function'
}

/** Ekran yönü kilidi desteklenir mi? (Çoğu tarayıcıda yalnız tam ekranda çalışır; masaüstünde yoktur.) */
export function canLockOrientation(scr: LandscapeEnv['screen']): boolean {
  return typeof scr.orientation?.lock === 'function'
}

export function createLandscapeController(env: LandscapeEnv = browserEnv(), options: LandscapeOptions = {}) {
  const settleMs = options.settleMs ?? ORIENTATION_SETTLE_MS
  let snapshot: LandscapeSnapshot = {open: false, landscape: readLandscape(env), fullscreen: false, lock: 'none'}
  const listeners = new Set<Listener>()
  let attached = false
  let enteredFullscreen = false
  let historyPushed = false
  let escapeGuard: () => boolean = () => false
  // open()/close() asenkron adımlar arasında iptal edilebilir; her açılışa bir kimlik verilir.
  let session = 0
  // Tam ekran/kilit isteği sürerken oluşan geçici boyut değişimleri yön kanıtı sayılmaz.
  let busy = false
  let settleTimer: ReturnType<typeof setTimeout> | undefined
  let orientationQuery: MediaListLike | undefined

  const emit = (patch: Partial<LandscapeSnapshot>) => {
    const next = {...snapshot, ...patch}
    if (next.open === snapshot.open && next.landscape === snapshot.landscape && next.fullscreen === snapshot.fullscreen && next.lock === snapshot.lock) return
    snapshot = next
    listeners.forEach(listener => listener())
  }

  // Yön değişimi hemen onaylanmaz: sinyaller durulana ve düzen oturana kadar beklenir. Geçici bir yatay ölçüm (ör. tam ekran
  // geçişi ya da kilit isteğinin yan etkisi) hemen eski hâline dönerse terminal hiç açılmaz; dikeyde uyarı kalır.
  const commitOrientation = () => {
    settleTimer = undefined
    if (!snapshot.open || busy) return
    emit({landscape: readLandscape(env)})
  }
  const scheduleSettle = () => {
    if (!snapshot.open) return
    clearTimeout(settleTimer)
    settleTimer = setTimeout(commitOrientation, settleMs)
  }
  const onOrientationSignal = () => scheduleSettle()
  const onFullscreenChange = () => {
    const active = fullscreenElementOf(env.document) !== null
    if (!active && enteredFullscreen && snapshot.open) {
      enteredFullscreen = false
      // Terminal açıkken sistem hareketiyle tam ekrandan çıkış düzeni kapatır. Uyarı aşamasında ise istem dışı bir çıkış
      // uyarıyı kapatmaz: kullanıcı elle döndürmeye devam eder veya İptal'e basar.
      if (snapshot.landscape) { close(); return }
    }
    emit({fullscreen: active})
    scheduleSettle()
  }
  const onPopState = () => {
    if (!snapshot.open) return
    // Yalnız bizim geçmiş kaydımız geri alındıysa (durumda işaret kalmadıysa) kapat. Uygulamanın kendi gönderdiği sentetik
    // `popstate` olayları (sekme geçişleri vb.) işaret hâlâ durduğu için uyarıyı kapatmaz.
    if (env.window.history.state?.[HISTORY_MARKER]) return
    historyPushed = false
    close()
  }
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !snapshot.open || escapeGuard()) return
    close()
  }

  const attach = () => {
    if (attached) return
    attached = true
    env.window.addEventListener('resize', onOrientationSignal)
    env.window.addEventListener('orientationchange', onOrientationSignal)
    env.window.addEventListener('popstate', onPopState)
    orientationQuery = env.window.matchMedia?.('(orientation: landscape)')
    orientationQuery?.addEventListener?.('change', onOrientationSignal)
    env.screen.orientation?.addEventListener?.('change', onOrientationSignal)
    env.document.addEventListener('fullscreenchange', onFullscreenChange)
    env.document.addEventListener('webkitfullscreenchange', onFullscreenChange)
    // Yakalama aşamasında: menüleri kapatan mevcut Esc dinleyicilerinden önce, menü hâlâ açıkken sorgulanır.
    env.document.addEventListener('keydown', onKeyDown, true)
  }
  const detach = () => {
    if (!attached) return
    attached = false
    env.window.removeEventListener('resize', onOrientationSignal)
    env.window.removeEventListener('orientationchange', onOrientationSignal)
    env.window.removeEventListener('popstate', onPopState)
    orientationQuery?.removeEventListener?.('change', onOrientationSignal)
    orientationQuery = undefined
    env.screen.orientation?.removeEventListener?.('change', onOrientationSignal)
    clearTimeout(settleTimer)
    settleTimer = undefined
    env.document.removeEventListener('fullscreenchange', onFullscreenChange)
    env.document.removeEventListener('webkitfullscreenchange', onFullscreenChange)
    env.document.removeEventListener('keydown', onKeyDown, true)
  }

  const setPageLock = (on: boolean) => {
    for (const element of [env.document.documentElement, env.document.body]) element?.classList[on ? 'add' : 'remove'](FULLSCREEN_CLASS)
  }

  const releaseLock = () => {
    if (snapshot.lock === 'locked') { try { env.screen.orientation?.unlock?.() } catch { /* kilit zaten kalkmış olabilir */ } }
  }

  async function enterFullscreenAndLock(id: number) {
    try { await requestFullscreenAndLock(id) } finally { if (id === session) { busy = false; scheduleSettle() } }
  }

  async function requestFullscreenAndLock(id: number) {
    const root = env.document.documentElement
    let fullscreenOk = false
    if (canRequestFullscreen(env.document) && fullscreenElementOf(env.document) === null) {
      try {
        // Kullanıcı dokunuşunun içinde senkron çağrılır; tarayıcı izin vermezse sessizce yedeğe düşülür.
        const result = root.requestFullscreen ? root.requestFullscreen({navigationUI: 'hide'}) : root.webkitRequestFullscreen?.()
        await result
        fullscreenOk = true
      } catch { /* reddedildi: kullanıcı cihazı elle çevirebilir */ }
    } else if (fullscreenElementOf(env.document) !== null) {
      fullscreenOk = true
    }
    if (id !== session || !snapshot.open) {
      // Beklerken kapatıldı: az önce alınan tam ekranı hemen bırak.
      if (fullscreenOk) { try { await env.document.exitFullscreen?.() } catch { /* yok say */ } }
      return
    }
    if (fullscreenOk) { enteredFullscreen = true; emit({fullscreen: true}) }
    if (!canLockOrientation(env.screen)) { emit({lock: 'unsupported'}); return }
    try {
      await env.screen.orientation!.lock!('landscape')
      if (id !== session || !snapshot.open) { try { env.screen.orientation?.unlock?.() } catch { /* yok say */ } return }
      emit({lock: 'locked'})
    } catch {
      // Tam ekran dışında veya izin yokken NotSupportedError/SecurityError fırlatır; elle döndürme yeterli.
      if (id === session) emit({lock: 'failed'})
    }
  }

  function open(): Promise<void> {
    if (snapshot.open) return Promise.resolve()
    session += 1
    const id = session
    attach()
    setPageLock(true)
    try {
      env.window.history.pushState({...(env.window.history.state ?? {}), [HISTORY_MARKER]: true}, '')
      historyPushed = true
    } catch { historyPushed = false }
    busy = true
    // Cihaz zaten yataysa terminal hemen açılır; dikeydeyse uyarı, yön gerçekten yatay ve oturmuş olana kadar kalır.
    emit({open: true, landscape: readLandscape(env), fullscreen: fullscreenElementOf(env.document) !== null, lock: 'none'})
    return enterFullscreenAndLock(id)
  }

  function close() {
    if (!snapshot.open) return
    session += 1
    busy = false
    detach()
    setPageLock(false)
    releaseLock()
    const wasFullscreen = enteredFullscreen
    enteredFullscreen = false
    emit({open: false, fullscreen: false, lock: 'none'})
    if (wasFullscreen && fullscreenElementOf(env.document) !== null) {
      try { void Promise.resolve(env.document.exitFullscreen ? env.document.exitFullscreen() : env.document.webkitExitFullscreen?.()).catch(() => undefined) } catch { /* yok say */ }
    }
    if (historyPushed) {
      historyPushed = false
      try { if (env.window.history.state?.[HISTORY_MARKER]) env.window.history.back() } catch { /* yok say */ }
    }
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: Listener) { listeners.add(listener); return () => { listeners.delete(listener) } },
    open,
    close,
    /** Esc'nin tam ekranı kapatmaması gereken durumlar (açık menü, çizim modu). */
    setEscapeGuard(guard: () => boolean) { escapeGuard = guard },
    dispose() { close(); listeners.clear() },
  }
}

export type LandscapeController = ReturnType<typeof createLandscapeController>
