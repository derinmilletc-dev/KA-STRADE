/** KAIS FARMER arayüz mantığı (SAF): sunucu yanıtını ekrana çevirir. Marjin, risk, PnL, ARM ve koruma durumunun KAYNAĞI sunucudur;
 *  burada hiçbir sayı hesaplanmaz — yalnız gösterim kararı (hangi ekran, hangi metin, hangi rozet) verilir.
 *  Çalıştırma (testler): node frontend/src/farmer.test.mts */

export type FarmerState = 'OFF' | 'RUNNING' | 'ENTRY_PAUSED_ARM' | 'ENTRY_PAUSED_SAFETY' | 'STOPPING' | 'INTERVENTION_REQUIRED'
export type PnlState = 'PENDING' | 'VERIFIED' | 'UNVERIFIED'
export type Lang = 'tr' | 'en'

export type FarmerSettings = {
  variant?: string; min_score: number; margin_usdt: number; leverage: number; target_usdt: number; max_loss_usdt: number
  max_positions: number; total_margin_cap: number; dead_score: number | null; dead_confirmations: number
}
export type FarmerPreview = {
  margin_usdt: number; leverage: number; notional_usdt: number; target_usdt: number; max_loss_usdt: number; est_cost_usdt: number
  target_pct: number | null; stop_pct: number | null; cost_over_target: number | null; effective_capacity: number; max_positions: number
  total_margin_cap: number; verdict: 'GOOD' | 'TIGHT' | 'NOT_RECOMMENDED'; warnings: string[]
}
export type FarmerPosition = {
  grid_id: string; symbol: string; side: 'LONG' | 'SHORT'; engine_status: string; slot_state: string | null; age_s: number; score: number | null
  target_usdt: number | null; max_loss_usdt: number | null; dead_score: number | null; dead_consecutive: number; margin_usdt: number | null
  notional_usdt: number | null; est_cost_usdt: number | null; target_pct: number | null; stop_pct: number | null; target_over_atr: number | null
  cost_over_target: number | null; close_reason: string | null; pnl_state: PnlState; verified_net_pnl: number | null
  entry_price: number | null; tp_price: number | null; stop_price: number | null; mark_price: number | null; unrealized_net_usdt: number | null
}
export type FarmerCandidate = {symbol: string; direction: string; score: number | null; status: 'QUALIFIED' | 'WAITING_CONFIRMATION' | 'REJECTED' | 'COOLING_DOWN'; reason: string | null}
export type FarmerSlots = {capacity: number; held: number; open: number; closing: number; free: number}
export type FarmerStatus = {
  ok: boolean; name: string; available: boolean; blockers: string[]; state: FarmerState; reason?: string; configured: boolean
  config: FarmerSettings | null; recommended: FarmerSettings & Record<string, unknown>; provisional: boolean; limits: Record<string, any>
  preview: FarmerPreview; arm: {active: boolean; expires_at: number | null}; positions: FarmerPosition[]; scanner: FarmerCandidate[]
  slots: FarmerSlots | null; effective_capacity: number | null; max_positions?: number; snapshot_ok?: boolean; recovered: boolean
  today: {basis: string; verified_net_pnl: number | null; verified_count: number; pending_count: number; unverified_count: number} | null
  why: {code: string; details: Array<{code: string; count: number}>}; hard_cap_usdt: number; signal_interval_s?: number
  margin_used_usdt?: number; available_margin_usdt?: number
}

/** Ekranlar: hiçbir durum boş ekran bırakmaz. */
export type Screen = 'loading' | 'backend_unavailable' | 'account_unavailable' | 'disabled' | 'onboarding' | 'dashboard'

export function pickScreen(input: {status: FarmerStatus | null; loading: boolean; error: boolean; accountConnected: boolean}): Screen {
  const {status, loading, error, accountConnected} = input
  if (!status) return error && !loading ? 'backend_unavailable' : 'loading'
  if (!accountConnected) return 'account_unavailable'
  if (!status.available) return 'disabled'
  if (!status.configured) return 'onboarding'
  return 'dashboard'
}

// ---------- metin: Türkçe + İngilizce (kullanıcıya görünen ad HER YERDE "KAIS FARMER") ----------
type Pair = {tr: string; en: string}
export const COPY = {
  tagline: {tr: 'Küçük, kontrollü fırsatlar. Otomatik yönetim.', en: 'Small controlled opportunities. Automatically managed.'},
  intro: {tr: 'KAIS FARMER piyasayı yüksek kaliteli kurulumlar için tarar ve her pozisyonu önceden belirlenmiş risk ve kâr hedefiyle yönetir.',
    en: 'KAIS FARMER scans the market for high-quality setups and manages each position with predefined risk and profit targets.'},
  // durum rozetleri
  'badge.OFF': {tr: 'KAPALI', en: 'OFF'},
  'badge.RUNNING': {tr: 'ÇALIŞIYOR', en: 'RUNNING'},
  'badge.ENTRY_PAUSED_ARM': {tr: 'GİRİŞ DURAKLADI', en: 'ENTRY PAUSED'},
  'badge.ENTRY_PAUSED_SAFETY': {tr: 'GİRİŞ DURAKLADI', en: 'ENTRY PAUSED'},
  'badge.STOPPING': {tr: 'DURDURULUYOR', en: 'STOPPING'},
  'badge.INTERVENTION_REQUIRED': {tr: 'MÜDAHALE GEREKLİ', en: 'INTERVENTION REQUIRED'},
  // durum mesajı: ne oluyor / ne bekleniyor / ne yapabilirim
  'msg.OFF': {tr: 'KAIS FARMER kapalı. Yeni pozisyon açılmıyor. Hazır olduğunda etkinleştir.', en: 'KAIS FARMER is off. No new positions are being opened. Activate it when you are ready.'},
  'msg.RUNNING': {tr: 'KAIS FARMER nitelikli kurulumları aktif olarak tarıyor.', en: 'KAIS FARMER is actively scanning for qualified setups.'},
  'msg.RUNNING_HOLDING': {tr: 'KAIS FARMER açık pozisyonlarını koruma emirleriyle yönetiyor ve yeni nitelikli kurulumları izliyor.',
    en: 'KAIS FARMER is managing your open positions with protection orders and watching for new qualified setups.'},
  'msg.ENTRY_PAUSED_ARM': {tr: 'İşlem oturumunun süresi doldu. Açık pozisyonlar korunmaya devam ediyor. Yeni girişlere izin vermek için ARM’ı yenile.',
    en: 'Your trading session has expired. Existing positions remain protected. Renew ARM to allow new entries.'},
  'msg.ENTRY_PAUSED_SAFETY': {tr: 'Yeni pozisyonlar geçici olarak duraklatıldı. Açık pozisyonlar korunmaya devam ediyor.',
    en: 'New positions are temporarily paused. Existing positions remain protected.'},
  'msg.STOPPING': {tr: 'Yeni pozisyon açılmıyor. Açık pozisyonlar koruma emirleriyle devam ediyor ve kendi hedeflerinde kapanacak.',
    en: 'New positions are stopped. Existing positions continue under their protection orders.'},
  'msg.INTERVENTION_REQUIRED': {tr: 'KAIS FARMER dikkatini gerektiren bir durum tespit etti. Çözülene kadar yeni pozisyon açılmayacak.',
    en: 'KAIS FARMER detected a condition that requires your attention. No new position will be opened until it is resolved.'},
  // "Neden pozisyon yok?"
  'why.OFF': {tr: 'KAIS FARMER kapalı.', en: 'KAIS FARMER is off.'},
  'why.UNAVAILABLE': {tr: 'KAIS FARMER bu hesapta henüz kullanılabilir değil.', en: 'KAIS FARMER is not available for this account yet.'},
  'why.NO_CANDIDATES': {tr: 'Şu an uygun bir kurulum yok. İşlem zorlanmıyor.', en: 'No setup currently meets your quality requirements. No trade is being forced.'},
  'why.SCORE_TOO_LOW': {tr: 'Hiçbir kurulum gereken skora ulaşmadı.', en: 'No setup reached the required score.'},
  'why.MOMENTUM_MISMATCH': {tr: 'Piyasa momentumu yönle uyumlu değil.', en: 'Market momentum is not aligned.'},
  'why.MTF_NOT_CONFIRMED': {tr: 'Çoklu zaman dilimi onayı sağlanmadı.', en: 'Multi-timeframe confirmation is not satisfied.'},
  'why.SHORT_FILTER_REJECTED': {tr: 'Yön filtresi kurulumu uygun bulmadı.', en: 'The direction filter did not accept the setup.'},
  'why.TRAP_REJECTED': {tr: 'Kurulum tuzak riski nedeniyle elendi.', en: 'The setup was filtered out due to trap risk.'},
  'why.BREAKOUT_QUALITY_REJECTED': {tr: 'Kırılım kalitesi yeterli değil.', en: 'Breakout quality is not sufficient.'},
  'why.COST_INFEASIBLE': {tr: 'Tahmini maliyet hedefe göre fazla yüksek.', en: 'Estimated costs are too high relative to the target.'},
  'why.ATR_INFEASIBLE': {tr: 'Hedef, güncel piyasa hareketine göre gerçekçi değil.', en: 'The target is not realistic for current market movement.'},
  'why.CAPACITY_FULL': {tr: 'Risk kapasitesi şu an dolu.', en: 'Risk capacity is currently full.'},
  'why.CAPACITY_ZERO': {tr: 'Mevcut ayarlarla hiç pozisyon açılamıyor. Marjin veya tavanı gözden geçir.', en: 'With the current settings no position can be opened. Review the margin or the cap.'},
  'why.COOLDOWN': {tr: 'Bekleme süresi aktif: bu mumda bu sembol için deneme zaten yapıldı.', en: 'Cooldown is active: this symbol was already attempted on this candle.'},
  'why.WAITING_CONFIRMATION': {tr: 'Adaylar onay bekliyor.', en: 'Candidates are waiting for confirmation.'},
  'why.QUALIFIED_ONLY': {tr: 'Nitelikli kurulumların hepsi zaten pozisyonda.', en: 'All qualified setups are already in positions.'},
  'why.ARM_EXPIRED': {tr: 'ARM süresi doldu.', en: 'ARM is expired.'},
  'why.SAFETY_GATE': {tr: 'Bir güvenlik kapısı aktif.', en: 'A safety gate is active.'},
  'why.DAILY_LOSS': {tr: 'Günlük kayıp koruması aktif.', en: 'Daily loss protection is active.'},
  'why.STOP_BOT': {tr: 'STOP BOT aktif: yeni pozisyon açılmıyor.', en: 'STOP BOT is active: no new positions are opened.'},
  'why.INTERVENTION': {tr: 'Dikkatin gerekiyor; yeni pozisyon açılmıyor.', en: 'Your attention is needed; no new position is being opened.'},
  'why.UNKNOWN': {tr: 'Şu an belirgin tek bir neden yok.', en: 'There is no single clear reason right now.'},
  'why.OTHER': {tr: 'Başka bir koşul sağlanmadı.', en: 'Another condition is not met.'},
  // aday durumları
  'cand.QUALIFIED': {tr: 'Nitelikli', en: 'Qualified'},
  'cand.WAITING_CONFIRMATION': {tr: 'Onay bekliyor', en: 'Waiting confirmation'},
  'cand.REJECTED': {tr: 'Reddedildi', en: 'Rejected'},
  'cand.COOLING_DOWN': {tr: 'Bekleme', en: 'Cooling down'},
  // PnL doğrulama
  'pnl.VERIFIED': {tr: 'Net sonuç doğrulandı', en: 'Net result verified'},
  'pnl.PENDING': {tr: 'Nihai sonuç doğrulanıyor', en: 'Final result is being verified'},
  'pnl.UNVERIFIED': {tr: 'Nihai sonuç henüz doğrulanamadı', en: 'Final result could not be verified yet'},
  // risk önizleme
  'verdict.GOOD': {tr: 'UYGUN', en: 'GOOD'},
  'verdict.TIGHT': {tr: 'SIKI', en: 'TIGHT'},
  'verdict.NOT_RECOMMENDED': {tr: 'ÖNERİLMEZ', en: 'NOT RECOMMENDED'},
  'warn.TARGET_TOO_SMALL': {tr: 'Hedefin tahmini işlem maliyetine çok yakın olabilir.', en: 'Your target may be too close to estimated trading costs.'},
  'warn.STOP_TOO_TIGHT': {tr: 'Seçilen risk sınırı normal piyasa hareketinde korunmakta zorlanabilir.', en: 'The selected risk limit may be difficult to maintain under normal market movement.'},
  'warn.HIGH_LEVERAGE': {tr: 'Yüksek kaldıraç maruziyeti ve tasfiye hassasiyetini artırır.', en: 'Higher leverage increases exposure and liquidation sensitivity.'},
  // kapalı/engelli
  'disabled.title': {tr: 'KAIS FARMER henüz hesabında etkin değil', en: 'KAIS FARMER is not enabled for your account yet'},
  'disabled.body': {tr: 'Canlı işlem şu an açık değil. Aşağıdan nasıl çalıştığını ve risk önizlemeni inceleyebilirsin; hiçbir işlem açılmaz.',
    en: 'Live trading is not turned on right now. You can review how it works and your risk preview below; nothing will be traded.'},
} as const satisfies Record<string, Pair>
export type CopyKey = keyof typeof COPY

export function tr_(lang: Lang, key: CopyKey): string {
  return COPY[key][lang]
}
export function whyKey(code: string): CopyKey {
  const key = `why.${code}` as CopyKey
  return key in COPY ? key : 'why.OTHER'
}

export type Tone = 'ok' | 'warn' | 'stop' | 'mute'
export function badgeOf(state: FarmerState): {key: CopyKey; tone: Tone; icon: 'power' | 'play' | 'pause' | 'square' | 'alert'} {
  switch (state) {
    case 'RUNNING': return {key: 'badge.RUNNING', tone: 'ok', icon: 'play'}
    case 'ENTRY_PAUSED_ARM': return {key: 'badge.ENTRY_PAUSED_ARM', tone: 'warn', icon: 'pause'}
    case 'ENTRY_PAUSED_SAFETY': return {key: 'badge.ENTRY_PAUSED_SAFETY', tone: 'warn', icon: 'pause'}
    case 'STOPPING': return {key: 'badge.STOPPING', tone: 'warn', icon: 'square'}
    case 'INTERVENTION_REQUIRED': return {key: 'badge.INTERVENTION_REQUIRED', tone: 'stop', icon: 'alert'}
    default: return {key: 'badge.OFF', tone: 'mute', icon: 'power'}
  }
}

export function statusMessageKey(state: FarmerState, hasPositions: boolean): CopyKey {
  if (state === 'RUNNING') return hasPositions ? 'msg.RUNNING_HOLDING' : 'msg.RUNNING'
  return `msg.${state}` as CopyKey
}

/** Slot görseli: GERÇEK kapasiteden (marjin tavanı dahil). Teorik üst sınır ayrı verilir; 2/5 yanıltıcı gösterilmez. */
export function slotView(status: Pick<FarmerStatus, 'slots' | 'effective_capacity' | 'max_positions'>) {
  const cap = Math.max(0, Number(status.effective_capacity ?? status.slots?.capacity ?? 0))
  const used = status.slots ? status.slots.held + status.slots.open + status.slots.closing : 0
  const dots = Array.from({length: cap}, (_, index) => (index < used ? 'used' : 'free') as 'used' | 'free')
  const configured = Number(status.max_positions ?? cap)
  return {capacity: cap, used, free: Math.max(0, cap - used), dots, limitedByMargin: configured > cap, configuredMax: configured, zero: cap === 0}
}

/** Bilinmeyen PnL asla 0 olarak gösterilmez. */
export function pnlView(pnlState: PnlState | string | undefined, value: number | null | undefined): {text: string; kind: 'verified' | 'pending' | 'unverified'; sign: 'up' | 'down' | 'flat' | null} {
  if (pnlState === 'VERIFIED' && typeof value === 'number' && Number.isFinite(value)) {
    return {text: money(value, true), kind: 'verified', sign: value > 0 ? 'up' : value < 0 ? 'down' : 'flat'}
  }
  return {text: '—', kind: pnlState === 'UNVERIFIED' ? 'unverified' : 'pending', sign: null}
}

export function money(value: number | null | undefined, signed = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  const abs = Math.abs(value).toFixed(2)
  const sign = value < 0 ? '-' : signed && value > 0 ? '+' : ''
  return `${sign}$${abs}`
}

/** Fiyat gösterimi: büyük fiyatlarda 2, küçüklerde anlamlı hane; veri yoksa — */
export function priceText(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '—'
  const digits = value >= 1000 ? 2 : value >= 1 ? 4 : value >= 0.01 ? 5 : 7
  return value.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: digits})
}

export function ageText(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '—'
  const s = Math.floor(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

export function armRemaining(expiresAt: number | null | undefined, nowSeconds: number): string | null {
  if (typeof expiresAt !== 'number' || !(expiresAt > nowSeconds)) return null
  const total = Math.floor(expiresAt - nowSeconds)
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

/** Koruma durumu: motor durumundan; "korumalı" demek için pozisyon açık ve motor çalışıyor olmalı. */
export function protectionOf(position: Pick<FarmerPosition, 'engine_status' | 'slot_state'>): 'protected' | 'closing' | 'attention' | 'pending' {
  if (position.slot_state === 'CLOSING') return 'closing'
  if (position.slot_state === 'HELD') return 'pending'
  if (['BLOCKED', 'INTERVENTION_REQUIRED', 'EMERGENCY'].includes(position.engine_status)) return 'attention'
  return position.slot_state === 'OPEN' ? 'protected' : 'pending'
}

/** Müşteriye ham hata gösterilmez; yalnız bilinen kodlar çevrilir, geri kalanı genel mesaj. */
export function friendlyError(error: unknown, lang: Lang): string {
  const raw = String((error as any)?.message ?? error ?? '')
  const known: Array<[RegExp, Pair]> = [
    [/FARMER_UNAVAILABLE/, {tr: 'KAIS FARMER bu hesapta henüz kullanılabilir değil.', en: 'KAIS FARMER is not available for this account yet.'}],
    [/FARMER_GATES_CLOSED/, {tr: 'Canlı KAIS FARMER şu an açık değil.', en: 'Live KAIS FARMER is not turned on right now.'}],
    [/FARMER_NOT_STARTED/, {tr: 'KAIS FARMER başlatılamadı. Durum ekranındaki uyarıyı kontrol et.', en: 'KAIS FARMER could not be started. Check the notice on the status screen.'}],
    [/FARMER_NOT_CONFIGURED/, {tr: 'Önce KAIS FARMER ayarlarını kaydet.', en: 'Save your KAIS FARMER settings first.'}],
    [/FARMER_NOT_YOUR_ACCOUNT/, {tr: 'Bu KAIS FARMER başka bir hesaba ait.', en: 'This KAIS FARMER belongs to a different account.'}],
    [/SETTINGS_LOCKED_WHILE_ACTIVE/, {tr: 'Ayarlar KAIS FARMER çalışırken değiştirilemez. Önce durdur ve pozisyonların kapanmasını bekle.', en: 'Settings cannot be changed while KAIS FARMER is active. Stop it first and wait for positions to close.'}],
    [/OUT_OF_RANGE|NOT_ALLOWED|INVALID_NUMBER|NOT_INTEGER|MARGIN_ABOVE_CAP/, {tr: 'Bu ayar izin verilen aralığın dışında.', en: 'This setting is outside the allowed range.'}],
  ]
  for (const [pattern, text] of known) if (pattern.test(raw)) return text[lang]
  return lang === 'tr' ? 'İşlem şu an tamamlanamadı. Biraz sonra tekrar dene.' : 'This could not be completed right now. Please try again shortly.'
}

/** Acil kapatma sonucu: "istek gönderildi" ile "kapandı" karıştırılmaz. */
export function emergencyOutcome(result: {complete?: boolean; closed?: number; failed?: number; unowned?: number} | null | undefined): 'success' | 'partial' | 'failure' {
  if (!result) return 'failure'
  if (result.complete) return 'success'
  return (result.closed ?? 0) > 0 ? 'partial' : 'failure'
}
