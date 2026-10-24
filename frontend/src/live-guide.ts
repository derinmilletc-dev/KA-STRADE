/** Canlı İşlem rehberi: ekrana yazılacak tek "şimdi ne yapmalıyım" kararını verir.
 *  Saf mantık (React yok, ağ yok) → tüm durumlar node testleriyle kontrol edilir (tools/live-guide.test.mjs).
 *  Kural: rehber yalnız sunucudan gelen DOĞRULANMIŞ duruma dayanır; veri yoksa ya da doğrulanamadıysa ilerleme
 *  iddia etmez, "doğrulanamadı" der. Öncelik: sorunlar → kurulum adımları → çalışma durumu. */

export type GuideTone = 'wait' | 'stop' | 'warn' | 'go' | 'ok'
export type GuidePhase = 'loading' | 'problem' | 'setup' | 'running'
export type GuideAction =
  | 'retry' | 'open-connection' | 'clear-emergency' | 'recovery' | 'review-fill-lock' | 'upgrade'
  | 'choose-risk' | 'approve' | 'start' | 'stop' | null

export type GuideInput = {
  statusLoaded: boolean
  statusVerified: boolean
  statusError?: string | null
  loading: boolean
  freeTrial: boolean
  credentialsConfigured: boolean
  connected: boolean
  /** Doğrulanmış Binance cüzdan bakiyesi; doğrulanamadıysa null. */
  balance: number | null
  emergency: boolean
  emergencyText?: string | null
  recoveryRequired: boolean
  recoveryText?: string | null
  accountRateLimited: boolean
  accountRateLimitText?: string | null
  marketBanSeconds: number
  /** Engel yalnız paylaşımlı sunucu IP'sinde ve canlı tarama sabit IP'den sürüyor. */
  scanningContinues?: boolean
  fillLocked: boolean
  fillLockSymbol?: string | null
  dailyLocked: boolean
  riskApproved: boolean
  authorizationValid: boolean
  policyAcknowledged: boolean
  armed: boolean
  running: boolean
  paused: boolean
  pauseReason?: string | null
  /** Çalışırken ilk geçmeyen giriş kontrolünün açıklaması (yoksa girişler açık). */
  entryBlocker?: string | null
  sessionRemainingSeconds: number
  /** Sunucudaki strateji döngüsünün son çalıştığı andan bu yana geçen süre (sn); bilinmiyorsa null. */
  lastCycleAgeSeconds?: number | null
}

export type GuideNotice = {tone: GuideTone; text: string}
export type Guide = {
  phase: GuidePhase
  tone: GuideTone
  /** Kurulum adımı (1-4); kurulum dışı hallerde de ilerleme çubuğu için son tamamlanan adımı gösterir. */
  step: number
  title: string
  message: string
  why?: string
  done?: string
  action: GuideAction
  actionLabel?: string
  notices: GuideNotice[]
}

export const GUIDE_STEPS = ['Hesap', 'Risk ayarı', 'Onaylar', 'Başlat'] as const
export const GUIDE_TOTAL = GUIDE_STEPS.length
/** Döngü normalde dakikada birkaç kez çalışır; 10 dakikadır çalışmadıysa sunucu uyumuş/takılmış sayılır. */
export const STALL_SECONDS = 600

const usdt = (value: number) => `${value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})} USDT`
export const duration = (seconds: number) => {
  const value = Math.max(0, Math.floor(seconds))
  const hours = Math.floor(value / 3600), minutes = Math.floor(value % 3600 / 60), rest = value % 60
  return hours > 0 ? `${hours} sa ${minutes} dk` : minutes > 0 ? `${minutes} dk ${rest} sn` : `${rest} sn`
}

const WHY: Record<number, string> = {
  1: 'Kais işlem açabilmek için Binance Futures hesabını okuyabilmeli. Anahtarlar şifreli kasada saklanır; para çekme izni hiç kullanılmaz.',
  2: 'Her işlemde bakiyenin ne kadarının riske gireceğini ve günde en fazla ne kadar kaybedilebileceğini sen belirlersin. Sistem bu sınırların dışına çıkamaz.',
  3: 'Gerçek parayla işlem için üç ayrı güvenlik onayı gerekir: 24 saatlik risk izni, risk limitlerinin onayı ve canlı emir izni (ARM). Hiçbiri tek başına emir açmaz.',
  4: 'Başlattığında strateji seçtiğin süre boyunca coinleri tarar; kurallara uyan fırsatta stop ve hedefleriyle birlikte emir açar. İstediğin an durdurabilirsin.',
}

function notices(input: GuideInput): GuideNotice[] {
  const list: GuideNotice[] = []
  if (input.marketBanSeconds > 0) list.push(input.scanningContinues
    ? {tone: 'ok', text: `Binance paylaşımlı sunucu IP'sini ${duration(input.marketBanSeconds)} sınırladı; canlı tarama ve emirler sabit IP'den devam ediyor. Piyasa ekranları bu sürede gecikebilir.`}
    : {tone: 'warn', text: `Binance piyasa verisini ${duration(input.marketBanSeconds)} sınırladı; bu sürede tarama yapılamaz. Hesabın ve açık pozisyonların korumaları etkilenmez.`})
  if (input.dailyLocked) list.push({tone: 'warn', text: 'Bugünkü kayıp sınırına ulaşıldı; yarına kadar yeni işlem açılmaz. Açık pozisyonların korumaları devam eder.'})
  if (input.running && input.sessionRemainingSeconds > 0 && input.sessionRemainingSeconds <= 3600) list.push({tone: 'warn', text: `Oturumun bitmesine ${duration(input.sessionRemainingSeconds)} kaldı. Süre bitince yeni işlem açılmaz.`})
  return list
}

export function liveGuide(input: GuideInput): Guide {
  const base = {notices: notices(input)}
  const problem = (title: string, message: string, action: GuideAction, actionLabel?: string, tone: GuideTone = 'stop', step = 1): Guide =>
    ({...base, phase: 'problem', tone, step, title, message, action, actionLabel})

  if (!input.statusLoaded && input.loading) return {...base, phase: 'loading', tone: 'wait', step: 1, title: 'Hesap durumu alınıyor', message: 'Binance hesabın ve stratejinin güncel durumu okunuyor.', action: null}
  if (!input.statusVerified) {
    const reason = input.statusError?.trim()
    return problem('Hesap durumu doğrulanamadı', `${reason ? `${reason} ` : ''}Güncel durum okunana kadar yeni işlem açılmaz; açık pozisyonların Binance'teki stop ve hedefleri yerinde kalır.`, 'retry', 'Tekrar dene', 'warn')
  }
  if (input.freeTrial) return problem('Canlı işlem Master Mode ile açılır', 'Deneme üyeliğinde canlı işlem kapalı. Master Mode üyeliğe geçince bu ekrandan devam edebilirsin.', 'upgrade', 'Paketleri gör', 'warn')
  if (!input.credentialsConfigured) return {...base, phase: 'setup', tone: 'go', step: 1, title: 'Binance hesabını bağla', message: 'İlk adım: Bağlantı sekmesinden Binance API anahtarını kaydet. Bu adım emir açmaz, sadece hesabını okur.', why: WHY[1], action: 'open-connection', actionLabel: "Bağlantı'ya git"}
  if (!input.connected) return problem('Hesap bağlantısı doğrulanamadı', 'Anahtarın kayıtlı ama Binance hesabı şu an okunamıyor. Kendiliğinden tekrar deneniyor; sorun sürerse Bağlantı sekmesinden anahtarı kontrol et.', 'retry', 'Tekrar dene', 'warn')
  if (input.emergency) return problem('Acil durdurma açık', `${input.emergencyText?.trim() || 'Yeni işlemler kapalı.'} Kaldırmak için 2FA kodu gerekir; açık pozisyonların korumaları değişmez.`, 'clear-emergency', 'Acil durdurmayı kaldır')
  if (input.recoveryRequired) return problem('Önce kurtarma kontrolü gerekiyor', `${input.recoveryText?.trim() || 'Hesaptaki emir ve pozisyonlar sistem kaydıyla eşleştirilmeli.'} Bu sırada yeni işlem açılmaz.`, 'recovery', 'Kurtarma kontrolünü yap')
  if (input.fillLocked) return problem('Dolum güvenlik kilidi', `${input.fillLockSymbol ? `${input.fillLockSymbol} işleminde ` : ''}beklenmeyen bir dolum oldu; yeni işlemler senin incelemene kadar kapalı. Açık pozisyon değiştirilmedi.`, 'review-fill-lock', 'İncele')
  if (input.accountRateLimited) return problem('Binance hesap doğrulaması bekleniyor', `${input.accountRateLimitText?.trim() || 'Binance kısa süreli hız sınırı uyguladı.'} Süre bitince kendiliğinden devam eder.`, null, undefined, 'warn')

  if (input.running && (input.lastCycleAgeSeconds ?? 0) > STALL_SECONDS) {
    return {...base, phase: 'running', tone: 'stop', step: GUIDE_TOTAL, title: 'Strateji bir süredir çalışmıyor',
      message: `Sunucudaki tarama döngüsü ${duration(input.lastCycleAgeSeconds ?? 0)} önce son kez çalıştı. Sunucu uyumuş, yeniden başlıyor ya da takılmış olabilir. Birkaç dakika içinde düzelmezse Render panelinde servisin çalıştığını kontrol et. Açık pozisyonların stop ve hedefleri Binance'te durur.`,
      action: 'retry', actionLabel: 'Durumu yenile'}
  }
  if (input.running) {
    const blocked = input.entryBlocker?.trim()
    return {...base, phase: 'running', tone: blocked || input.dailyLocked ? 'warn' : 'ok', step: GUIDE_TOTAL,
      title: blocked || input.dailyLocked ? 'Çalışıyor · yeni girişler beklemede' : 'Otomatik işlem çalışıyor',
      message: blocked || (input.dailyLocked ? 'Günlük kayıp sınırı doldu.' : 'Strateji coinleri her 15 dakikalık mum kapanışında tarıyor; kurallara uyan fırsatta stop ve hedefleriyle emir açar. Senin bir şey yapmana gerek yok.'),
      action: 'stop', actionLabel: 'Durdur'}
  }

  const stoppedNote = input.paused && input.pauseReason?.trim() ? `Son oturum durdu: ${input.pauseReason.trim()} ` : ''
  if (input.balance === null) return problem('Bakiye doğrulanamadı', `${stoppedNote}Binance bakiyen okunamadı; risk tutarları bakiyeye göre hesaplandığı için devam edilemez. Kendiliğinden tekrar deneniyor.`, 'retry', 'Tekrar dene', 'warn', 2)
  const balanceText = `Binance hesabın doğrulandı · bakiye ${usdt(input.balance)}`
  if (!input.riskApproved) return {...base, phase: 'setup', tone: 'go', step: 2, done: balanceText,
    title: 'Risk ayarını seç', message: `${stoppedNote}Temkinli, Dengeli veya Agresif seçeneklerinden birini seç ya da kendin ayarla. Her işlemde ve günde en fazla ne kadar riske girileceğini rakamla göreceksin.`,
    why: WHY[2], action: 'choose-risk', actionLabel: 'Risk ayarını seç'}
  // ARM tek başına eksikse (ör. durdurmadan sonra) ayrı adım açılmaz: başlatırken yeniden açılır ve özet ekranında yazar.
  const approvalsDone = input.authorizationValid && input.policyAcknowledged
  if (!approvalsDone) {
    const left = [!input.authorizationValid && '24 saatlik risk izni', !input.policyAcknowledged && 'risk limitleri onayı', !input.armed && 'canlı emir izni (ARM)'].filter(Boolean) as string[]
    return {...base, phase: 'setup', tone: 'go', step: 3, done: 'Risk ayarın kaydedildi',
      title: 'Güvenlik onaylarını ver', message: `${stoppedNote}Kalan onay${left.length > 1 ? 'lar' : ''}: ${left.join(', ')}. Her birinin neye izin verdiğini ayrıntısıyla görerek onaylayacaksın.`,
      why: WHY[3], action: 'approve', actionLabel: 'Onayları incele ve ver'}
  }
  return {...base, phase: 'setup', tone: 'go', step: 4, done: 'Güvenlik onayları tamam',
    title: 'Otomatik işlemi başlat', message: `${stoppedNote}Her şey hazır. Başlatmadan önce bu ayarlarla en kötü durumda ne kadar kaybedebileceğini göreceksin.${input.armed ? '' : ' Canlı emir izni (ARM) başlatırken 24 saatliğine yeniden açılır.'}`,
    why: WHY[4], action: 'start', actionLabel: 'Son kontrol ve başlat'}
}

/** Hazır risk seçenekleri (Original V2 sınırları: işlem riski %0,5–10, günlük %1–25, 10–100 coin, 1/4/12/24 saat).
 *  Hiçbiri %2'yi aşmaz: daha yüksek risk ayrıca açık onay ister ve yalnız "Kendim ayarlayayım" ile seçilir. */
export const RISK_PRESETS = [
  {id: 'temkinli', label: 'Temkinli', risk_percent: 0.5, daily_loss_percent: 2, symbol_count: 20, session_hours: 4, note: 'Az risk, az coin, kısa oturum'},
  {id: 'dengeli', label: 'Dengeli', risk_percent: 1, daily_loss_percent: 3, symbol_count: 40, session_hours: 12, note: 'Önerilen başlangıç'},
  {id: 'agresif', label: 'Agresif', risk_percent: 2, daily_loss_percent: 6, symbol_count: 60, session_hours: 24, note: 'Daha çok fırsat, daha çok risk'},
] as const
export type RiskPreset = typeof RISK_PRESETS[number]

export function matchPreset(values: {risk_percent: number; daily_loss_percent: number; symbol_count: number; session_hours: number}): RiskPreset | null {
  return RISK_PRESETS.find(preset => preset.risk_percent === values.risk_percent && preset.daily_loss_percent === values.daily_loss_percent
    && preset.symbol_count === values.symbol_count && preset.session_hours === values.session_hours) ?? null
}

export type RiskSummary = {perTrade: number; perDay: number; dayShare: number; perTradeCapped: boolean; perDayCapped: boolean}
/** Başlatmadan önce gösterilen rakamlar: seçilen yüzde × bakiye, sunucunun sabit USDT tavanlarıyla sınırlanır. */
export function riskSummary(balance: number, riskPercent: number, dailyPercent: number, caps?: {max_loss_per_trade_usdt?: number; daily_loss_limit_usdt?: number} | null): RiskSummary | null {
  if (!(balance > 0) || !(riskPercent > 0) || !(dailyPercent > 0)) return null
  const rawTrade = balance * riskPercent / 100, rawDay = balance * dailyPercent / 100
  const tradeCap = caps?.max_loss_per_trade_usdt, dayCap = caps?.daily_loss_limit_usdt
  const perTrade = typeof tradeCap === 'number' && tradeCap > 0 ? Math.min(rawTrade, tradeCap) : rawTrade
  const perDay = typeof dayCap === 'number' && dayCap > 0 ? Math.min(rawDay, dayCap) : rawDay
  return {perTrade, perDay, dayShare: perDay / balance * 100, perTradeCapped: perTrade < rawTrade, perDayCapped: perDay < rawDay}
}

export type RiskCapsLike = {max_margin_usdt?: number; leverage?: number; estimated_cost_pct?: number; max_stop_distance_pct?: number} | null | undefined
export type CapacityRow = {stopPct: number; loss: number; percent: number}
/** Aday yokken gösterilen teorik üst sınır: boş hesapta, işlem başı teminat sınırı ve kaldıraçla bir işlemde ulaşılabilecek en büyük stop kaybı.
 *  Açık pozisyonlar, anlık kullanılabilir bakiye ve Binance sınırları işlem anında bunu ayrıca düşürebilir. Hesaplanamazsa null. */
export function capacityPreview(wallet: number, caps: RiskCapsLike): {maxNotional: number; rows: CapacityRow[]; atMaxStop: CapacityRow} | null {
  if (!(wallet > 0) || !caps) return null
  const margin = caps.max_margin_usdt, leverage = caps.leverage, cost = caps.estimated_cost_pct
  if (typeof margin !== 'number' || typeof leverage !== 'number' || typeof cost !== 'number') return null
  if (![margin, leverage, cost].every(Number.isFinite) || margin <= 0 || leverage < 1 || cost < 0) return null
  const maxStop = typeof caps.max_stop_distance_pct === 'number' && caps.max_stop_distance_pct > 0 ? caps.max_stop_distance_pct : 6
  const maxNotional = margin * leverage
  const row = (stopPct: number): CapacityRow => { const loss = maxNotional * (stopPct + cost) / 100; return {stopPct, loss, percent: loss / wallet * 100} }
  const rows = [1, 2, 3, maxStop].filter((value, index, all) => value <= maxStop && all.indexOf(value) === index).map(row)
  return {maxNotional, rows, atMaxStop: row(maxStop)}
}

export type MarginModeCaps = RiskCapsLike & {system_ceilings?: {max_margin_per_trade_usdt?: number} | null}
export type MarginModePreview = {marginUsdt: number; totalPercent: number; atMaxStop: CapacityRow; atThreeStop: CapacityRow | null; capped: boolean}
/** Bir teminat modunda (işlem başı % × bakiye, sistem tavanıyla sınırlı) boş hesapta bir işlemde ulaşılabilecek en büyük stop kaybı. Salt gösterim. */
export function marginModePreview(wallet: number, caps: MarginModeCaps, perTradePercent: number): MarginModePreview | null {
  if (!(wallet > 0) || !(perTradePercent > 0) || !caps) return null
  const ceiling = caps.system_ceilings?.max_margin_per_trade_usdt
  const raw = wallet * perTradePercent / 100
  const margin = typeof ceiling === 'number' && ceiling > 0 ? Math.min(raw, ceiling) : raw
  const preview = capacityPreview(wallet, {...caps, max_margin_usdt: margin})
  if (!preview) return null
  return {marginUsdt: margin, totalPercent: perTradePercent, atMaxStop: preview.atMaxStop, atThreeStop: preview.rows.find(row => row.stopPct === 3) ?? null, capped: margin < raw}
}

export type RiskBreakdown = {
  status: 'verified' | 'partial' | 'unavailable'; unverified?: string[]; reason?: string
  target_loss_usdt?: number; target_percent?: number; budget_loss_usdt?: number
  max_applicable_loss_usdt?: number; max_applicable_percent?: number
  effective_loss_usdt?: number; effective_percent?: number
  reachable?: boolean; limiting_factor?: string; unreached_reason?: string | null; stop_distance_pct?: number
}

type NearMiss = {symbol: string; reason_code: string; reason?: string; confidence?: number | null; confidence_threshold?: number | null; failed_checks?: string[]}
type Scanner = {stage?: string; last_scan_at?: string; candidate_count?: number; scanned_symbol_count?: number; near_misses?: NearMiss[]; rejection_counts?: Record<string, number>; last_failure?: {message: string} | null} | null | undefined

const REJECTION_TEXT: Record<string, string> = {
  data_missing: 'mum verisi eksik', invalid_market_data: 'veri hatalı', market_volume: '24 saatlik hacim düşük', market_move: 'fiyat hareketi yetersiz',
  market_filter: 'piyasa filtresine takıldı', market_shortlist: 'aday listesine giremedi', signal_conditions: 'trend/sinyal koşulu oluşmadı',
  confidence_low: 'güven skoru düşük', spread_wide: 'alış-satış farkı geniş', stop_too_wide: 'stop %6 sınırından geniş',
  minimum_margin: 'teminat sınırına takıldı', minimum_trade_size: 'Binance minimum işlem tutarının altında', position_limit: 'pozisyon sınırı dolu',
  daily_limit: 'günlük sınıra ulaşıldı', risk_or_account_limit: 'risk/hesap kontrolüne takıldı',
}
const CHECK_TEXT: Record<string, string> = {direction: 'yön', confidence: 'güven', trap: 'tuzak radarı', breakout: 'kırılım kalitesi', mtf: 'çoklu zaman dilimi'}

/** "Neden işlem açılmadı?" — yalnız son taramanın gerçek sonucundan. Tarama yoksa ya da aday bulunduysa null. */
export function whyNoTrade(scanner: Scanner): string | null {
  if (!scanner) return null
  if (scanner.last_failure?.message) return `Son tarama tamamlanamadı: ${scanner.last_failure.message}`
  if (!scanner.last_scan_at || (scanner.candidate_count ?? 0) > 0) return null
  const nearest = scanner.near_misses?.[0]
  const scanned = scanner.scanned_symbol_count ?? 0
  if (nearest) {
    const reason = REJECTION_TEXT[nearest.reason_code] ?? nearest.reason ?? 'kurallara uymadı'
    const confidence = nearest.confidence != null && nearest.confidence_threshold != null ? ` (güven %${nearest.confidence}, gereken %${nearest.confidence_threshold})` : ''
    const checks = nearest.failed_checks?.length ? ` · geçmeyen: ${nearest.failed_checks.map(key => CHECK_TEXT[key] ?? key).join(', ')}` : ''
    return `Son taramada ${scanned} coinin hiçbiri kurallara uymadı. En yakın aday ${nearest.symbol.replace('USDT', '')}: ${reason}${confidence}${checks}.`
  }
  const top = Object.entries(scanner.rejection_counts ?? {}).filter(([, count]) => count > 0).sort((a, b) => b[1] - a[1])[0]
  return top ? `Son taramada ${scanned} coinin hiçbiri kurallara uymadı; en sık sebep: ${REJECTION_TEXT[top[0]] ?? top[0]} (${top[1]} coin).` : `Son taramada ${scanned} coinde uygun fırsat çıkmadı.`
}

export type RejectionSummary = {scans?: number; since_epoch?: number | null; reasons?: Record<string, number>; checks?: Record<string, number>} | null | undefined
export type RejectionRow = {key: string; label: string; count: number; percent: number; bar: number}
export type RejectionBreakdown = {scans: number; hours: number | null; reasons: RejectionRow[]; gates: RejectionRow[]; headline: string; thin: boolean}

const GATE_TEXT: Record<string, string> = {
  direction: 'Net trend yok (yön belirsiz)', confidence: 'Güven eşiğin altında', trap: 'Tuzak skoru yüksek',
  breakout: 'Kırılım kalitesi düşük', mtf: '1h/4h yönü uyuşmuyor',
}
const cap = (text: string) => text.charAt(0).toLocaleUpperCase('tr-TR') + text.slice(1)

function rows(counts: Record<string, number> | undefined, labels: (key: string) => string, limit: number): RejectionRow[] {
  const entries = Object.entries(counts ?? {}).filter(([, count]) => Number.isFinite(count) && count > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  const total = entries.reduce((sum, [, count]) => sum + count, 0)
  const top = entries[0]?.[1] ?? 0
  return entries.slice(0, limit).map(([key, count]) => ({key, label: labels(key), count, percent: total ? Math.round(count / total * 100) : 0, bar: top ? Math.round(count / top * 100) : 0}))
}

/** Son 24 saatte coinlerin hangi kapıda elendiği. Sayılar "coin × tarama"dır; veri yoksa null (uydurma yok). */
export function rejectionBreakdown(summary: RejectionSummary, nowMs: number): RejectionBreakdown | null {
  const scans = Number(summary?.scans)
  if (!summary || !Number.isFinite(scans) || scans < 1) return null
  const reasons = rows(summary.reasons, key => cap(REJECTION_TEXT[key] ?? key), 6)
  const gates = rows(summary.checks, key => GATE_TEXT[key] ?? key, 5)
  if (!reasons.length) return null
  const since = Number(summary.since_epoch)
  const hours = Number.isFinite(since) && since > 0 && nowMs / 1000 >= since ? Math.max(0, (nowMs / 1000 - since) / 3600) : null
  const thin = scans < 4
  const span = hours !== null ? (hours < 1 ? 'son bir saatte' : `son ${Math.round(hours)} saatte`) : 'kayıtlı taramalarda'
  const headline = `${cap(span)} ${scans} taramada en sık eleme: ${REJECTION_TEXT[reasons[0].key] ?? reasons[0].key} (%${reasons[0].percent}).`
  return {scans, hours, reasons, gates, headline, thin}
}

/** Okuma hatalarında kendiliğinden yeniden deneme aralığı (sn): 5, 10, 20, 40, sonra 60'ta sabit.
 *  Yalnız durum OKUMASI tekrarlanır; emir, izin veya başlatma gibi değiştiren istekler asla kendiliğinden tekrarlanmaz. */
export const retryDelay = (attempt: number) => Math.min(60, 5 * 2 ** Math.max(0, Math.min(4, Math.floor(attempt))))

const num = (value: unknown) => {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN
  return Number.isFinite(parsed) ? parsed : null
}
const pct = (value: number) => `%${value.toLocaleString('tr-TR', {maximumFractionDigits: 2, minimumFractionDigits: value < 10 ? 1 : 0})}`

export type PositionNote = {text: string; tone: GuideTone; toTarget: number | null; toStop: number | null; openFor: string | null}
/** Pozisyon yorumu: TP1'e ve stopa kalan mesafe (fiyata göre %) ve açık kalma süresi. Seviye ya da fiyat yoksa uydurmaz. */
export function positionNote(position: {direction?: unknown; mark_price?: unknown; entry_price?: unknown}, plan: {stop_loss?: unknown; targets?: unknown[]; opened_at?: unknown; created_at?: unknown} | undefined, nowMs: number): PositionNote | null {
  const mark = num(position.mark_price), stop = num(plan?.stop_loss), target = num(plan?.targets?.[0])
  const long = String(position.direction || '').toUpperCase() !== 'SHORT'
  if (mark === null || mark <= 0) return null
  const toTarget = target !== null && target > 0 ? (long ? target - mark : mark - target) / mark * 100 : null
  const toStop = stop !== null && stop > 0 ? (long ? mark - stop : stop - mark) / mark * 100 : null
  const opened = Date.parse(String(plan?.opened_at || plan?.created_at || ''))
  const openFor = Number.isFinite(opened) && opened <= nowMs ? duration((nowMs - opened) / 1000) : null
  const parts: string[] = []
  if (toTarget !== null) parts.push(toTarget <= 0 ? 'TP1 seviyesi geçildi' : `TP1'e ${pct(toTarget)}`)
  if (toStop !== null) parts.push(toStop <= 0 ? 'fiyat stop seviyesinde' : `stopa ${pct(toStop)}`)
  if (openFor) parts.push(`${openFor} açık`)
  if (!parts.length) return null
  const tone: GuideTone = toStop !== null && toTarget !== null && toStop < toTarget / 3 ? 'warn' : toTarget !== null && toTarget <= 0 ? 'ok' : 'wait'
  return {text: parts.join(' · '), tone, toTarget, toStop, openFor}
}

export type SmartAlertInput = {
  dailyLoss: number | null; dailyLimit: number | null; dailyLocked: boolean
  availableBalance: number | null; minMargin: number | null
  marketBanSeconds: number; openPositions: number; protectedPositions: number
}
/** Akıllı uyarılar: yalnız doğrulanmış sayılardan; sayı yoksa uyarı da yok. */
export function smartAlerts(input: SmartAlertInput): GuideNotice[] {
  const list: GuideNotice[] = []
  const {dailyLoss, dailyLimit} = input
  if (!input.dailyLocked && dailyLoss !== null && dailyLimit !== null && dailyLimit > 0 && dailyLoss / dailyLimit >= 0.8)
    list.push({tone: 'warn', text: `Günlük kayıp sınırına yaklaşıldı: %${Math.round(Math.min(1, dailyLoss / dailyLimit) * 100)} kullanıldı (${usdt(dailyLoss)} / ${usdt(dailyLimit)}). Sınır dolarsa bugün yeni işlem açılmaz.`})
  if (input.availableBalance !== null && input.minMargin !== null && input.minMargin > 0 && input.availableBalance < input.minMargin)
    list.push({tone: 'warn', text: `Kullanılabilir bakiye (${usdt(input.availableBalance)}) en düşük teminatın (${usdt(input.minMargin)}) altında; yeni işlem açılamaz.`})
  if (input.marketBanSeconds > 0 && input.openPositions > 0)
    list.push(input.protectedPositions >= input.openPositions
      ? {tone: 'ok', text: `Binance engeli sürse de ${input.openPositions} açık pozisyonun stop ve hedefleri Binance'te kayıtlı; korumalar çalışmaya devam eder.`}
      : {tone: 'stop', text: `Binance engeli sırasında ${input.openPositions - input.protectedPositions} pozisyonun korumaları doğrulanamadı. Binance uygulamasından stop emrini kontrol et.`})
  return list
}

export type DailySummary = {pnl: number | null; trades: number | null; scans: number | null; candidates: number | null; lossUsed: number | null; text: string}
/** Gün özeti: bugünkü gerçekleşen K/Z, işlem sayısı ve son 24 saatin tarama sonucu. */
export function dailySummary(input: {realizedPnl?: unknown; entries?: unknown; last24h?: {scans?: unknown; candidates?: unknown; trades?: unknown} | null; dailyLoss?: unknown; dailyLimit?: unknown}): DailySummary | null {
  const pnl = num(input.realizedPnl), trades = num(input.entries)
  const scans = num(input.last24h?.scans), candidates = num(input.last24h?.candidates)
  const loss = num(input.dailyLoss), limit = num(input.dailyLimit)
  const lossUsed = loss !== null && limit !== null && limit > 0 ? Math.min(100, Math.max(0, loss / limit * 100)) : null
  if (pnl === null && trades === null && scans === null) return null
  const parts: string[] = []
  if (trades !== null) parts.push(trades === 0 ? 'Bugün işlem açılmadı' : `Bugün ${trades} işlem`)
  if (pnl !== null && trades !== 0) parts.push(`gerçekleşen ${pnl > 0 ? '+' : ''}${usdt(pnl)}`)
  if (scans !== null) parts.push(`son 24 saatte ${scans} tarama, ${candidates ?? 0} aday`)
  return {pnl, trades, scans, candidates, lossUsed, text: parts.join(' · ') + '.'}
}

/** Durum okunamadığında müşteriye "ne yapmalıyım" cevabı: hata metnine göre somut yol. */
export function statusHelp(error: string | null | undefined): {text: string; href?: string; linkLabel?: string} {
  const message = String(error || '')
  if (/2FA/i.test(message)) return {text: 'Canlı işlem için site girişinde iki adımlı doğrulama (2FA) açık olmalı.', href: '/profile', linkLabel: '2FA ayarına git'}
  if (/başka bir kullanıcı veya oturuma/i.test(message)) return {text: 'Canlı kontrol başka bir oturuma bağlı. Aşağıdaki “Oturumu geri bağla” ile 2FA kodunla devam edebilirsin.'}
  if (/(401|oturum(un)? (süresi|sona))/i.test(message)) return {text: 'Oturumun sona ermiş olabilir. Sayfayı yenileyip tekrar giriş yap.'}
  if (/anahtar|fingerprint|uyuşmuyor/i.test(message)) return {text: 'Kayıtlı Binance anahtarı ile canlı durum eşleşmiyor. Bağlantı sekmesinden anahtarı kontrol et.', href: '/master-trade?tab=baglanti', linkLabel: 'Bağlantı'}
  return {text: 'Sunucu şu an yanıt vermiyor; yeniden başlıyor ya da uykudan uyanıyor olabilir (ücretsiz planda yaklaşık 1 dakika). Kendiliğinden tekrar deneniyor; 2 dakikadan uzun sürerse Render panelinde servisin çalıştığını kontrol et.'}
}

/** Sıradaki tarama: strateji her 15 dakikalık mum kapanışında (UTC hizalı) tarar. Engel bekleniyorsa engelin bitişi. */
export function nextScan(nowMs: number, opts: {waitUntilMs?: number | null} = {}): {at: number; inSeconds: number} {
  const boundary = Math.ceil((nowMs + 1) / 900_000) * 900_000
  const at = opts.waitUntilMs && opts.waitUntilMs > nowMs ? Math.max(boundary, Math.ceil(opts.waitUntilMs / 900_000) * 900_000) : boundary
  return {at, inSeconds: Math.max(0, Math.round((at - nowMs) / 1000))}
}

/** Bağlantı geçici koptuğunda: son DOĞRULANAN durum ve ne kadar önce olduğu; ilerleme sıfırlanmaz. */
export function reconnectingGuide(last: GuideInput, ageSeconds: number, error: string | null | undefined): Guide {
  const before = liveGuide(last)
  const help = statusHelp(error)
  const state = before.phase === 'running' ? 'otomatik işlem çalışıyordu' : `kurulumda ${before.step}. adımdaydın (${GUIDE_STEPS[before.step - 1]})`
  return {phase: 'problem', tone: 'wait', step: before.step, title: 'Bağlantı geçici olarak koptu',
    message: `Son doğrulanan durum (${duration(ageSeconds)} önce): ${state}. ${help.text} Yeni işlem ancak durum yeniden doğrulanınca açılır; açık pozisyonların stop ve hedefleri Binance'te durur.`,
    action: 'retry', actionLabel: 'Şimdi tekrar dene', notices: []}
}
