import assert from 'node:assert/strict'
import {computeDcaSuitability as cs, type DcaSuitInput, type SuitCandle} from './gridSuitability.ts'
const NOW = 1_800_000_000
const make = (n: number, step: number, f: (i: number) => number): SuitCandle[] => Array.from({length: n}, (_, i) => {
  const c = f(i), o = i ? f(i - 1) : c
  return {time: NOW - (n - 1 - i) * step, open: o, close: c, high: Math.max(o, c) * 1.002, low: Math.min(o, c) * 0.998, volume: 1}
})
const noise = (i: number) => 100 + ((i * 7919) % 13 - 6) * 0.1
const up = (i: number) => 100 * Math.pow(1.004, i), down = (i: number) => 150 * Math.pow(0.996, i)
const set = (f: (i: number) => number) => ({c15: make(120, 900, f), c1h: make(150, 3600, f), c4h: make(100, 14400, f)})
const base = {stepPct: 2, safetyOrders: 3, stopGapPct: 2, now: NOW}
const px = (f: (i: number) => number, n = 120) => f(n - 1)
const run = (side: 'LONG' | 'SHORT', f: (i: number) => number, extra: Partial<DcaSuitInput> = {}) => cs({...base, side, price: px(f, 150), ...set(f), ...extra} as DcaSuitInput)

// Güçlü düşüş + LONG DCA = düşük/kırmızı; aynı piyasada SHORT çok daha iyi
const longDown = run('LONG', down), shortDown = run('SHORT', down)
assert.ok(longDown.ok && longDown.score < 45 && longDown.level === 'poor', `longDown ${longDown.score}`)
assert.ok(longDown.warnings.length > 0 && (longDown.trend === 'against' || longDown.trend === 'strongAgainst'))
assert.ok(shortDown.score > longDown.score + 20, `shortDown ${shortDown.score}`)
// Yükselişte LONG, düşüşteki LONG'dan iyi
assert.ok(run('LONG', up).score > longDown.score + 15)
// Yatay piyasa: ters güçlü trend yok, orta/yüksek puan
const flat = run('LONG', noise)
assert.equal(flat.trend, 'flat'); assert.ok(flat.score >= 60, `flat ${flat.score}`)
// Çok sert oynaklık + dar ek alım aralığı: oynaklık puanı düşer
const wild = cs({...base, side: 'LONG', price: 100, stepPct: 0.3, safetyOrders: 2, stopGapPct: 0.3, ...set(i => 100 + ((i * 7919) % 13 - 6) * 1.5)} as DcaSuitInput)
assert.ok(wild.parts.vol < flat.parts.vol && wild.volState === 'wild' || wild.volState === 'fast', `wild vol ${wild.parts.vol}`)
// Geniş aralık oynaklığı daha iyi karşılar
const roomy = cs({...base, side: 'LONG', price: 100, stepPct: 5, safetyOrders: 4, stopGapPct: 5, ...set(i => 100 + ((i * 7919) % 13 - 6) * 0.6)} as DcaSuitInput)
const tight = cs({...base, side: 'LONG', price: 100, stepPct: 0.4, safetyOrders: 2, stopGapPct: 0.4, ...set(i => 100 + ((i * 7919) % 13 - 6) * 0.6)} as DcaSuitInput)
assert.ok(roomy.parts.vol > tight.parts.vol, `roomy ${roomy.parts.vol} tight ${tight.parts.vol}`)
// Aşırı uzamış: pompanın tepesinde LONG, normal fiyattakinden düşük "stretch"
const spike = (i: number) => i < 140 ? noise(i) : 100 + (i - 139) * 4
const spiked = run('LONG', spike)
assert.ok(spiked.parts.stretch < flat.parts.stretch, `stretch ${spiked.parts.stretch}`)
// Veri gecikmiş / eksik: puan YOK
assert.equal(cs({...base, side: 'LONG', price: 100, ...set(noise), now: NOW + 86400}).ok, false)
assert.match(cs({...base, side: 'LONG', price: 100, ...set(noise), now: NOW + 86400}).headline, /gecikmiş/)
assert.equal(cs({...base, side: 'LONG', price: 100, c15: [], c1h: [], c4h: []}).ok, false)
assert.match(cs({...base, side: 'LONG', price: 100, ...set(noise), now: NOW + 86400}, 'en').headline, /stale/)
console.log('dcaSuitability OK', {longDown: longDown.score, shortDown: shortDown.score, flat: flat.score, up: run('LONG', up).score, spiked: spiked.score})
