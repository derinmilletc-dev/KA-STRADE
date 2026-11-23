// Coinbase akıllı uyarılar için SAF hesaplar (ağ yok): işlem öncesi değerlendirme, günlük zarar sayacı, kısmi kâr önerisi.
// Hiçbiri emir göndermez; yalnızca ekranda uyarı/öneri üretir. Asıl kuralları sunucu (ön kontrol) uygular; bunlar aynı kuralların erken uyarısıdır.

export const UNLIMITED = 1_000_000
const fin = (value: unknown): number | null => { if (value === null || value === undefined || (typeof value === 'string' && value.trim() === '')) return null; const n = Number(value); return Number.isFinite(n) ? n : null }

export type AdviceTone = 'bad' | 'warn' | 'info' | 'good'
export type AdviceItem = {id: string; tone: AdviceTone; title: string; text: string}
export type TradeAdviceInput = {
  side: 'BUY' | 'SELL'; entry: number; stop: number; target: number | null; contracts: number; contractSize: number
  feeRate?: number; leverage?: number | null
  availableMargin?: number | null; maxLossUsd?: number | null; dailyLimitUsd?: number | null; dailyLossUsd?: number | null
  openOrders?: number | null; maxOrders?: number | null
}
export type TradeAdvice = {verdict: 'ok' | 'warn' | 'block'; headline: string; items: AdviceItem[]; lossUsd: number | null; gainUsd: number | null; feeUsd: number; rr: number | null}

const limitOf = (value: number | null | undefined): number | null => value != null && Number.isFinite(value) && value > 0 && value < UNLIMITED ? value : null
const usd = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})

/** İşlem öncesi değerlendirme: komisyon dahil kayıp/kazanç, limitlere göre "bu emir geçer mi", ve nedenli uyarılar. */
export function tradeAdvice(input: TradeAdviceInput): TradeAdvice | null {
  const {side, entry, stop, target, contracts, contractSize} = input
  if (![entry, stop, contracts, contractSize].every(v => Number.isFinite(v) && v > 0)) return null
  const feeRate = input.feeRate ?? 0.001
  const units = contracts * contractSize
  const fee = units * entry * feeRate * 2
  const risk = side === 'BUY' ? entry - stop : stop - entry
  const items: AdviceItem[] = []
  if (!(risk > 0)) {
    return {verdict: 'block', headline: 'Stop yanlış tarafta', lossUsd: null, gainUsd: null, feeUsd: fee, rr: null,
      items: [{id: 'side', tone: 'bad', title: 'Stop yanlış tarafta', text: side === 'BUY' ? 'LONG için stop girişin altında olmalı.' : 'SHORT için stop girişin üstünde olmalı.'}]}
  }
  const loss = units * risk + fee
  const stopPct = risk / entry * 100
  let gain: number | null = null, rr: number | null = null
  if (target !== null && Number.isFinite(target) && target > 0) {
    const reward = side === 'BUY' ? target - entry : entry - target
    if (reward > 0) { gain = Math.max(0, units * reward - fee); rr = reward / risk }
    else items.push({id: 'targetSide', tone: 'warn', title: 'Hedef yanlış tarafta', text: 'Hedef, kâr yönünde olmalı; bu hedef yok sayılır.'})
  }
  const maxLoss = limitOf(input.maxLossUsd), daily = limitOf(input.dailyLimitUsd), todayLoss = Math.max(0, input.dailyLossUsd ?? 0)
  if (maxLoss !== null && loss > maxLoss) items.push({id: 'maxLoss', tone: 'bad', title: 'İşlem başı zarar limitini aşıyor', text: `Stopta kaybın yaklaşık ${usd(loss)} USD; limitin ${usd(maxLoss)} USD. Teminatı ya da stop mesafesini küçült.`})
  if (daily !== null && todayLoss + loss > daily) {
    const left = Math.max(0, daily - todayLoss)
    items.push({id: 'daily', tone: 'bad', title: 'Günlük zarar limitini aşar', text: `Bugün ${usd(todayLoss)} USD zarardasın; bu işlemin stopu ${usd(loss)} USD daha ekler. Günlük limitin ${usd(daily)} USD (kalan ${usd(left)} USD).`})
  }
  if (input.openOrders != null && input.maxOrders != null && input.openOrders >= input.maxOrders) items.push({id: 'capacity', tone: 'bad', title: 'Açık emir kapasitesi dolu', text: `${input.openOrders}/${input.maxOrders} emir açık. Birini kapat ya da iptal et.`})
  const avail = input.availableMargin
  if (avail != null && avail > 0 && loss / avail > 0.25) items.push({id: 'big', tone: 'warn', title: 'Alım gücünün büyük kısmı risk altında', text: `Stopta kaybın, kullanılabilir bakiyenin %${Math.round(loss / avail * 100)}'i. Genelde %5-10 aralığı daha güvenli kabul edilir.`})
  if (rr !== null && rr < 1) items.push({id: 'rr', tone: 'warn', title: 'Kâr/zarar oranı düşük', text: `Hedef stoptan yakın (1:${rr.toFixed(1)}). Kazanabileceğin, kaybedebileceğinden az.`})
  if (stopPct < 0.3) items.push({id: 'tight', tone: 'warn', title: 'Stop çok yakın', text: `Stop girişe %${stopPct.toFixed(2)} uzakta; normal fiyat oynaması bile stopu tetikleyebilir.`})
  if (gain !== null && gain > 0 && fee / (gain + fee) > 0.3) items.push({id: 'fee', tone: 'warn', title: 'Komisyon kârın büyük kısmını yer', text: `Komisyon ${usd(fee)} USD; brüt kârın %${Math.round(fee / (gain + fee) * 100)}'i. Daha uzak hedef ya da daha büyük hareket gerekir.`})
  if (input.leverage != null && input.leverage > 10) items.push({id: 'lev', tone: 'warn', title: 'Yüksek kaldıraç', text: `Gerçek kaldıraç yaklaşık ${input.leverage.toFixed(1)}x. Küçük bir ters hareket teminatın büyük kısmını eritir.`})
  if (gain === null) items.push({id: 'noTarget', tone: 'info', title: 'Hedef yok', text: 'Yalnızca stop kurulur; kârı elle almalısın.'})
  const bad = items.some(item => item.tone === 'bad'), warn = items.some(item => item.tone === 'warn')
  if (!bad && !warn) items.push({id: 'good', tone: 'good', title: rr !== null ? `Dengeli plan · 1:${rr.toFixed(1)}` : 'Plan uygun', text: `Stopta en çok ${usd(loss)} USD${gain !== null ? `, hedefte ${usd(gain)} USD` : ''} (komisyon dahil).`})
  const order: Record<AdviceTone, number> = {bad: 0, warn: 1, info: 2, good: 3}
  items.sort((a, b) => order[a.tone] - order[b.tone])
  return {verdict: bad ? 'block' : warn ? 'warn' : 'ok', headline: bad ? 'Bu emir reddedilir' : warn ? 'Dikkat edilecekler var' : 'Plan uygun', items, lossUsd: loss, gainUsd: gain, feeUsd: fee, rr}
}

export type DailyMeter = {lossUsd: number; limitUsd: number | null; pct: number | null; leftUsd: number | null; tone: 'ok' | 'warn' | 'bad'; text: string}

/** Günlük zarar sayacı. Günün K/Z'si (Coinbase'in daily_realized_pnl'i) eksiyse zarardır; limit yoksa yalnız bugünkü zarar gösterilir. */
export function dailyLossMeter(dailyPnl: unknown, limit: unknown): DailyMeter | null {
  const pnl = fin(dailyPnl)
  if (pnl === null) return null
  const loss = Math.max(0, -pnl)
  const cap = limitOf(fin(limit))
  if (cap === null) return {lossUsd: loss, limitUsd: null, pct: null, leftUsd: null, tone: 'ok', text: loss > 0 ? `Bugün ${usd(loss)} USD zarar · günlük limit yok` : 'Bugün zarar yok'}
  const pct = Math.min(100, loss / cap * 100)
  const tone = pct >= 80 ? 'bad' : pct >= 50 ? 'warn' : 'ok'
  const left = Math.max(0, cap - loss)
  return {lossUsd: loss, limitUsd: cap, pct, leftUsd: left, tone,
    text: pct >= 100 ? `Günlük limit doldu · yeni emir verilemez` : `Bugün ${usd(loss)} / ${usd(cap)} USD · kalan ${usd(left)} USD`}
}

export type ProfitHint = {level: 'watch' | 'strong'; progressPct: number; title: string; text: string; action: 'half' | 'breakeven' | null}

/** Hedefe ilerleme: fiyat girişten hedefe giden yolun yarısını geçtiyse kârı korumayı önerir. Kapatma/taşıma için ekranda ayrı onay istenir. */
export function profitHint(input: {dir: 'LONG' | 'SHORT' | null; contracts: number; entry: number | null; current: number | null; stop: number | null; target: number | null}): ProfitHint | null {
  const {dir, contracts, entry, current, stop, target} = input
  if (!dir || entry === null || current === null || target === null || !(entry > 0) || !(current > 0)) return null
  const sign = dir === 'LONG' ? 1 : -1
  const full = (target - entry) * sign
  if (!(full > 0)) return null
  const progress = (current - entry) * sign / full
  if (!(progress >= 0.5) || progress >= 1) return null
  const pct = Math.round(progress * 100)
  const stopAtRisk = stop === null || (stop - entry) * sign < 0
  const strong = progress >= 0.8
  if (contracts >= 2) {
    return {level: strong ? 'strong' : 'watch', progressPct: pct, action: 'half',
      title: `Hedefin %${pct}'indesin`,
      text: strong ? 'Hedefe çok yakınsın. Yarısını kapatıp kârı kilitlemeyi düşün; kalanı hedefe bırakırsın.' : 'Yarısını kapatıp kârın bir kısmını kilitleyebilirsin; kalan pozisyon hedefe devam eder.'}
  }
  if (stopAtRisk) {
    return {level: strong ? 'strong' : 'watch', progressPct: pct, action: 'breakeven',
      title: `Hedefin %${pct}'indesin`,
      text: 'Tek sözleşmen var, yarısı kapatılamaz. Stopu girişe çekersen bu işlem zarara dönmez.'}
  }
  return null
}
