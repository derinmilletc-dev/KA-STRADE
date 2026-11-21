// Çalıştırma: node frontend/src/coinbase-levels.test.mts
import assert from 'node:assert/strict'
import {expiryInfo, levelsFromAnalysis, levelsFromVolatility, minOrder} from './coinbase-levels.ts'

const close = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} ≈ ${b}`)

// Analiz yüzdesi Coinbase fiyatına taşınır (Binance 100 → Coinbase 102)
{
  const s = levelsFromAnalysis({direction: 'LONG', entry: 100, stop_loss: 98, tp1: 101, tp2: 104}, 102, 5)!
  assert.equal(s.direction, 'LONG')
  close(s.stopPct, 2)
  close(s.stop, 102 * 0.98)
  close(s.target!, 102 * 1.04)
  assert.equal(s.clamped, false)
}
// SHORT ve tp2 yoksa tp1
{
  const s = levelsFromAnalysis({direction: 'SHORT', entry: 50, stop_loss: 51, tp1: 48}, 50, 3)!
  close(s.stop, 51)
  close(s.target!, 48)
}
// Kaldıraçta tasfiyeye takılacak stop güvenli sınıra çekilir
{
  const s = levelsFromAnalysis({direction: 'LONG', entry: 100, stop_loss: 80}, 100, 10)!
  assert.equal(s.clamped, true)
  assert.ok(s.stopPct < 10)
}
// Net yön yok / bozuk veri → öneri yok
assert.equal(levelsFromAnalysis({direction: 'BEKLE', entry: 100, stop_loss: 98}, 100, 5), null)
assert.equal(levelsFromAnalysis({direction: 'LONG', entry: 100, stop_loss: 102}, 100, 5), null)
assert.equal(levelsFromAnalysis(null, 100, 5), null)

// Dalga stopu: hedef 2R
{
  const s = levelsFromVolatility('SHORT', 200, 1.5, 3)!
  close(s.stop, 203)
  close(s.target!, 194)
}
assert.equal(levelsFromVolatility('LONG', 0, 1, 3), null)

// Vade bilgisi
{
  const now = Date.parse('2026-11-21T00:00:00Z')
  const far = expiryInfo('2026-11-27T16:00:00Z', now)!
  assert.equal(far.days, 6)
  assert.equal(far.tone, 'info')
  assert.equal(expiryInfo('2026-11-23T16:00:00Z', now)!.tone, 'warn')
  assert.equal(expiryInfo('2026-11-21T10:00:00Z', now)!.tone, 'bad')
  assert.equal(expiryInfo('2026-11-20T10:00:00Z', now)!.tone, 'bad')
  assert.equal(expiryInfo('2030-12-20T16:00:00Z', now)!.tone, 'info') // perp ayrımı çağıranda (isPerp) yapılır
  assert.equal(expiryInfo('2050-12-20T16:00:00Z', now), null)
  assert.equal(expiryInfo(null, now), null)
}

// En küçük emir
{
  const m = minOrder(0.1, 3000, 5)!
  close(m.notional, 300)
  close(m.margin, 60)
  assert.equal(minOrder(0, 3000, 5), null)
}
console.log('coinbase-levels testleri geçti')
