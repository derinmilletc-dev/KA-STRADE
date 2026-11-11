// DCA botunun açtığı pozisyonu canlı izleme: bot durumundan "şu an ne yapıyor" özeti çıkarır (saf hesap, ekran yok).
export type WatchTone = 'up' | 'warn' | 'down' | 'info'
export type WatchView = {
  headline: string; detail: string; tone: WatchTone
  long: boolean; done: number; total: number
  next: number | null; nextDistPct: number | null
  tp: number | null; tpDistPct: number | null
  stop: number | null; stopDistPct: number | null
  avg: number | null; mark: number | null
  levels: Array<{price: number; filled: boolean}>
  trail: boolean; cycle: number; completed: number; realized: number
  /** Fiyat çizgisinde 0–100 konumlar (soldan sağa: zarar tarafı → kâr tarafı). */
  track: {stop: number; avg: number | null; mark: number | null; tp: number | null; levels: Array<{pos: number; filled: boolean}>} | null
  note: string
}

const fin = (value: unknown): number | null => { const n = Number(value); return Number.isFinite(n) && n !== 0 ? n : null }
const pct = (from: number, to: number) => Math.abs(to - from) / from * 100

// bot: sunucunun DCA durum kaydı (stats, spec.levels, status, reason, alerts). mark: anlık fiyat (yoksa null).
export function watchDca(bot: any, mark: number | null): WatchView {
  const stats = bot?.stats ?? {}
  const long = bot?.side !== 'SHORT'
  const sign = long ? 1 : -1
  const total = Number(stats.stages_total) || (bot?.spec?.levels?.length ?? 0)
  const done = Number(stats.stages_filled) || 0
  const levelsRaw: any[] = bot?.spec?.levels ?? []
  const levels = levelsRaw.map((level, index) => ({price: Number(level?.entry), filled: index < done})).filter(level => Number.isFinite(level.price) && level.price > 0)
  const avg = fin(stats.avg_entry), tp = fin(stats.tp_price), stop = fin(stats.stop_price)
  const price = mark !== null && mark > 0 ? mark : null
  const nextLevel = done < total ? levels[done] : undefined
  const next = nextLevel ? nextLevel.price : null
  const nextDistPct = next !== null && price !== null ? pct(price, next) : null
  const tpDistPct = tp !== null && price !== null ? Math.max(0, sign * (tp - price) / price * 100) : null
  const stopDistPct = stop !== null && price !== null ? Math.max(0, sign * (price - stop) / price * 100) : null
  const trail = Boolean(stats.trail_active)
  const status = String(bot?.status ?? '')
  const bad = ['BLOCKED', 'INTERVENTION_REQUIRED', 'EMERGENCY'].includes(status) || Boolean(bot?.needs_user)
  const fmt = (n: number) => String(Number(n.toPrecision(6)))

  let headline: string, detail: string, tone: WatchTone
  if (bad) { headline = 'Müdahale gerekiyor'; detail = String(bot?.reason || 'Bot kendi başına devam edemiyor; DCA ekranından bakmalısın.'); tone = 'down' }
  else if (status === 'PAUSED') { headline = 'Bot durduruldu'; detail = 'Yeni emir vermiyor. Pozisyon açık kaldı; devam ettirebilir ya da kapatabilirsin.'; tone = 'warn' }
  else if (status === 'CYCLE_DONE') { headline = 'Tur bitti'; detail = String(bot?.reason || 'Hedefe ulaştı, yeni tura hazırlanıyor.'); tone = 'up' }
  else if (trail) { headline = 'Kârı kovalıyor'; detail = `Stop fiyatın arkasından geliyor${stop !== null ? ` (şu an ${fmt(stop)})` : ''}; geri gitmez. Fiyat dönerse kârla çıkar.`; tone = 'up' }
  else if (next !== null) {
    headline = long ? 'Düşüşü bekliyor' : 'Yükselişi bekliyor'
    detail = `Sıradaki ek alım ${fmt(next)} fiyatında${nextDistPct !== null ? ` (%${nextDistPct.toFixed(2)} uzakta)` : ''}. Fiyat oraya gelmezse alım yapmaz.`; tone = 'info'
  } else if (done < total) {
    headline = long ? 'Düşüşü bekliyor' : 'Yükselişi bekliyor'
    detail = `${total - done} ek alım hakkı var; fiyat ${long ? 'düşerse' : 'yükselirse'} alır, ${long ? 'çıkarsa' : 'inerse'} kârla kapatır.`; tone = 'info'
  } else { headline = 'Tüm ek alımlar doldu'; detail = 'Daha fazla alım yok; kâr hedefini ya da stopu bekliyor.'; tone = done > 0 ? 'warn' : 'info' }

  let track: WatchView['track'] = null
  const points = [stop, avg, tp, price, ...levels.map(level => level.price)].filter((v): v is number => v !== null)
  if (stop !== null && points.length >= 3) {
    const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo
    if (span > 0) {
      // Çizgi hep "zarar → kâr" yönünde: SHORT'ta ters çevrilir.
      const at = (v: number) => { const p = (v - lo) / span * 100; return long ? p : 100 - p }
      track = {stop: at(stop), avg: avg !== null ? at(avg) : null, mark: price !== null ? at(price) : null, tp: tp !== null ? at(tp) : null, levels: levels.map(level => ({pos: at(level.price), filled: level.filled}))}
    }
  }
  const alerts: any[] = bot?.alerts ?? []
  const last = alerts.length ? alerts[alerts.length - 1] : null
  const note = String(last?.text ?? (headline === 'Müdahale gerekiyor' ? '' : bot?.reason ?? ''))
  return {headline, detail, tone, long, done, total, next, nextDistPct, tp, tpDistPct, stop, stopDistPct, avg, mark: price, levels, trail,
    cycle: Number(stats.cycle) || 1, completed: Number(stats.completed_cycles) || 0, realized: Number(stats.realized_usdt) || 0, track, note}
}
