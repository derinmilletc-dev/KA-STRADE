// Kais ajanı — aşama 3, adım 1: çok adımlı sesli/yazılı komutu bir PLANA çevirir.
// Bu dosya yalnızca metni çözer: veri okumaz, ağa istek atmaz, emir vermez, hiçbir adımı çalıştırmaz.
// Plan her zaman "ekranda onay ister" ve "çalıştırmaz" olarak işaretlenir; sesle onay yoktur.
export type PlanStepKind = 'analysis' | 'scan' | 'prepare' | 'target_profit' | 'follow'
export type PlanAccount = 'demo' | 'live' | ''

export type PlanStep = {kind: PlanStepKind; params: Record<string, number | string>}

export type CommandPlan = {
  steps: PlanStep[]
  account: PlanAccount
  /** Kais'in eksik bilgi olarak sorması gerekenler (kod). Eksik varken plan çalıştırılamaz. */
  missing: Array<'account' | 'count' | 'confidence' | 'target_profit'>
  /** Anlaşılamayan ya da geçersiz değerler (ör. %250, 0 dolar); varsayılan uydurulmaz. */
  invalid: string[]
  /** Sabit güvenlik işaretleri: bu aşama hiçbir şeyi çalıştırmaz ve her emir ekranda onay ister. */
  executes: false
  requiresScreenConfirmation: true
}

const fold = (value: string) => value.toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/,/g, '.')

const WORD_NUMBERS: Record<string, number> = {bir: 1, iki: 2, uc: 3, dort: 4, bes: 5}
export const MAX_PLAN_TRADES = 5
export const MAX_TARGET_PROFIT_USDT = 1000

function confidence(text: string): {value: number | null; invalid: boolean} {
  const hit = text.match(/%\s?(\d{1,3}(?:\.\d+)?)|yuzde\s?(\d{1,3}(?:\.\d+)?)|(\d{1,3}(?:\.\d+)?)\s?(?:%|yuzde)/)
  if (!hit) return {value: null, invalid: false}
  const value = Number(hit[1] ?? hit[2] ?? hit[3])
  return value >= 50 && value <= 100 ? {value, invalid: false} : {value: null, invalid: true}
}

function count(text: string): {value: number | null; invalid: boolean} {
  const hit = text.match(/\b(\d+|bir|iki|uc|dort|bes)\s+(?:adet\s+)?(?:islem|firsat|coin|sinyal|pozisyon)/)
  if (!hit) return {value: null, invalid: false}
  const value = /^\d+$/.test(hit[1]) ? Number(hit[1]) : WORD_NUMBERS[hit[1]]
  return Number.isInteger(value) && value >= 1 && value <= MAX_PLAN_TRADES ? {value, invalid: false} : {value: null, invalid: true}
}

function targetProfit(text: string): {value: number | null; invalid: boolean; present: boolean} {
  const hit = text.match(/\bkar\w*\s+(\d+(?:\.\d+)?)\s*(?:dolar|usdt|\$)/) ?? text.match(/\b(\d+(?:\.\d+)?)\s*(?:dolar|usdt|\$)\s+(?:kar|hedef)/)
  const wanted = /\bkar\w*\b/.test(text) && /\b(olsun|ayarla|hedef\w*|koy|yap)\b/.test(text)
  if (!hit) return {value: null, invalid: false, present: wanted}
  const value = Number(hit[1])
  return value > 0 && value <= MAX_TARGET_PROFIT_USDT ? {value, invalid: false, present: true} : {value: null, invalid: true, present: true}
}

/** Zincirleme komut → plan. Tek adımlık ya da plan olmayan cümle için null (mevcut yerel komutlar çalışmaya devam eder). */
export function parseCommandPlan(message: string): CommandPlan | null {
  const text = fold(message)
  if (!text.trim() || text.split(/\s+/).length > 60) return null
  const steps: PlanStep[] = []
  const invalid: string[] = []

  const analysis = /\banaliz\w*\s+(?:ekran\w*\s+)?(?:gir|ac|baslat|yap)\w*|\banaliz\w*\s+gir/.test(text)
  const conf = confidence(text)
  const cnt = count(text)
  const wantsScan = /\b(bul|sec|tara|listele|getir)\w*/.test(text) && (conf.value !== null || cnt.value !== null || conf.invalid || cnt.invalid)
  const wantsPrepare = /\b(hazirla\w*|hazir et\w*|taslak\w*)\b/.test(text)
  const target = targetProfit(text)
  const wantsFollow = /\b(takip et\w*|izle\w*|haber ver\w*|bildir\w*)\b/.test(text)

  if (analysis) steps.push({kind: 'analysis', params: {}})
  if (wantsScan) {
    steps.push({kind: 'scan', params: {...(conf.value !== null ? {min_confidence: conf.value} : {}), ...(cnt.value !== null ? {count: cnt.value} : {})}})
    if (conf.invalid) invalid.push('confidence')
    if (cnt.invalid) invalid.push('count')
  }
  if (wantsPrepare) steps.push({kind: 'prepare', params: {}})
  if (target.present) {
    steps.push({kind: 'target_profit', params: target.value !== null ? {usdt: target.value} : {}})
    if (target.invalid) invalid.push('target_profit')
  }
  if (wantsFollow) steps.push({kind: 'follow', params: {}})

  // Plan olması için en az iki farklı adım gerekir; tek komutlar mevcut yerel çözümleyicilere kalır.
  if (steps.length < 2) return null

  const account: PlanAccount = /\b(canli|gercek)\b/.test(text) ? 'live' : /\bdemo\b/.test(text) ? 'demo' : ''
  const missing: CommandPlan['missing'] = []
  const has = (kind: PlanStepKind) => steps.some(step => step.kind === kind)
  if (account === '' && (has('prepare') || has('scan'))) missing.push('account')
  if (has('prepare') && !has('scan')) missing.push('count')
  if (has('scan') && cnt.value === null && !cnt.invalid) missing.push('count')
  if (has('scan') && conf.value === null && !conf.invalid) missing.push('confidence')
  if (has('target_profit') && target.value === null && !target.invalid) missing.push('target_profit')
  return {steps, account, missing, invalid, executes: false, requiresScreenConfirmation: true}
}

const STEP_TEXT = {
  tr: {
    analysis: () => 'analiz ekranına girerim',
    scan: (p: PlanStep['params']) => `${p.min_confidence ? `%${p.min_confidence} ve üstü ` : ''}${p.count ? `${p.count} ` : ''}fırsatı bulurum`,
    prepare: () => 'işlemleri taslak olarak hazırlarım',
    target_profit: (p: PlanStep['params']) => (p.usdt ? `net kâr hedefini ${p.usdt} dolara göre hesaplarım` : 'net kâr hedefini hesaplarım'),
    follow: () => 'açılan işlemi takip edip haber veririm',
  },
  en: {
    analysis: () => 'open the analysis screen',
    scan: (p: PlanStep['params']) => `find ${p.count ? `${p.count} ` : ''}setups${p.min_confidence ? ` at ${p.min_confidence}%+` : ''}`,
    prepare: () => 'prepare them as drafts',
    target_profit: (p: PlanStep['params']) => (p.usdt ? `size the target for ${p.usdt} USD net profit` : 'size the net profit target'),
    follow: () => 'follow the opened trade and tell you about it',
  },
} as const

const ASK = {
  tr: {account: 'Demo mu Canlı mı?', count: 'Kaç işlem hazırlayayım?', confidence: 'Hangi güven oranının üstü?', target_profit: 'Hedef kâr kaç dolar?'},
  en: {account: 'Demo or Live?', count: 'How many trades?', confidence: 'What minimum confidence?', target_profit: 'How many dollars of profit?'},
} as const

const INVALID = {
  tr: {confidence: 'Güven oranı %50–%100 arasında olmalı.', count: `İşlem sayısı 1–${MAX_PLAN_TRADES} arasında olmalı.`, target_profit: `Hedef kâr 0'dan büyük, en fazla ${MAX_TARGET_PROFIT_USDT} dolar olmalı.`},
  en: {confidence: 'Confidence must be 50–100%.', count: `Trade count must be 1–${MAX_PLAN_TRADES}.`, target_profit: `Target profit must be above 0 and at most ${MAX_TARGET_PROFIT_USDT} USD.`},
} as const

/** Kullanıcıya söylenecek/gösterilecek plan metni. Hiçbir şey çalıştırılmadığını ve onayın ekranda olduğunu her zaman söyler. */
export function describePlan(plan: CommandPlan, language: 'tr' | 'en' = 'tr'): string {
  const steps = plan.steps.map((step, index) => `${index + 1}) ${(STEP_TEXT[language][step.kind] as (p: PlanStep['params']) => string)(step.params)}`).join('; ')
  const account = plan.account === 'live' ? (language === 'en' ? 'Live' : 'Canlı') : plan.account === 'demo' ? 'Demo' : ''
  const head = language === 'en' ? `My plan${account ? ` (${account})` : ''}: ${steps}.` : `Planım${account ? ` (${account})` : ''}: ${steps}.`
  const problems = plan.invalid.map(key => INVALID[language][key as keyof typeof INVALID['tr']]).filter(Boolean)
  const questions = plan.missing.map(key => ASK[language][key])
  const safety = language === 'en'
    ? 'I have not done anything yet. Every order needs your confirmation on the screen; voice cannot confirm.'
    : 'Henüz hiçbir şey yapmadım. Her emir için onayı ekranda vereceksin; sesle onay yok.'
  return [head, ...problems, ...(questions.length ? [language === 'en' ? `I need: ${questions.join(' ')}` : `Eksik: ${questions.join(' ')}`] : []), safety].join(' ')
}
