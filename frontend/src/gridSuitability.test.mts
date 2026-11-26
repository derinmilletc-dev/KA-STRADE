import assert from 'node:assert/strict'
import {computeSuitability, adx, type SuitCandle} from './gridSuitability.ts'

const make = (n: number, f: (i: number) => number, step = 3600): SuitCandle[] => Array.from({length: n}, (_, i) => {
  const c = f(i), o = i ? f(i - 1) : c
  return {time: i * step, open: o, close: c, high: Math.max(o, c) * 1.002, low: Math.min(o, c) * 0.998, volume: 1}
})
const range = (n: number, step = 3600) => make(n, i => 100 + ((i * 7919) % 13 - 6) * 0.1, step)
const trend = (n: number, step = 3600) => make(n, i => 100 * Math.pow(1.006, i), step)
const downtrend = (n: number, step = 3600) => make(n, i => 150 * Math.pow(0.994, i), step)

// Yatay piyasa: yüksek puan, nötr yön
const r = computeSuitability({price: 100, lower: 96, upper: 104, side: 'LONG', c15: range(120, 900), c1h: range(120), c4h: range(80, 14400)})
assert.ok(r.ok); assert.equal(r.regime, 'range'); assert.ok(r.score >= 70, `range score ${r.score}`); assert.equal(r.direction, 'NEUTRAL')

// Güçlü yükseliş: düşük puan, yön LONG, SHORT seçildiyse uyarı
const up = trend(120), px = up[up.length - 1].close
const t = computeSuitability({price: px, lower: px * 0.97, upper: px * 1.03, side: 'SHORT', c15: trend(120, 900), c1h: up, c4h: trend(80, 14400)})
assert.ok(t.score < r.score && t.score < 60, `trend score ${t.score}`); assert.equal(t.direction, 'LONG'); assert.ok(t.warnings.length > 0)

// Düşüş: yön SHORT
const dn = downtrend(120), dpx = dn[dn.length - 1].close
assert.equal(computeSuitability({price: dpx, lower: dpx * 0.97, upper: dpx * 1.03, side: 'LONG', c15: downtrend(120, 900), c1h: dn, c4h: downtrend(80, 14400)}).direction, 'SHORT')

// Fiyat aralığın dışında / kenarında puan düşer
const base = {side: 'LONG' as const, c15: range(120, 900), c1h: range(120), c4h: range(80, 14400)}
const center = computeSuitability({...base, price: 100, lower: 96, upper: 104}).score
const edge = computeSuitability({...base, price: 103.8, lower: 96, upper: 104}).score
const out = computeSuitability({...base, price: 110, lower: 96, upper: 104})
assert.ok(center > edge && edge > out.score); assert.equal(out.pos, 'outside')

// Aralık değişince puan değişir (çok dar aralık = daha düşük)
const narrow = computeSuitability({...base, price: 100, lower: 99.7, upper: 100.3}).score
assert.ok(narrow < center, `narrow ${narrow} vs ${center}`)

// Eksik veri: hesaplanmaz, çökmez
assert.equal(computeSuitability({price: 100, lower: 96, upper: 104, side: 'LONG', c15: [], c1h: [], c4h: []}).ok, false)
assert.equal(adx(range(10)), null)
// EN metni
assert.match(computeSuitability({price: 100, lower: 96, upper: 104, side: 'LONG', c15: range(120, 900), c1h: range(120), c4h: range(80, 14400)}, 'en').headline, /grid/i)
console.log('gridSuitability OK', {range: r.score, trend: t.score, center, edge, narrow, adx: r.adx1h?.toFixed(1), tadx: t.adx1h?.toFixed(1)})
