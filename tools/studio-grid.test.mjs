import assert from 'node:assert/strict'
import {test} from 'node:test'
import {gridPnl, previewGrid, snapTo, startBody, suggestGrid} from '../studio-grid.ts'

const filters = {tickSize: 0.1, stepSize: 0.001, minNotional: 5, minQty: 0.001}
const base = {mode: 'NEUTRAL', lower: 98, upper: 102, count: 8, stop: 96.5, stopUpper: 103.5, margin: 100, leverage: 3, recycle: true}

test('snapTo adım katına yuvarlar ve kayan nokta artığı bırakmaz', () => {
  assert.equal(snapTo(100.04, 0.1), 100)
  assert.equal(snapTo(100.06, 0.1, 'floor'), 100)
  assert.equal(snapTo(100.01, 0.1, 'ceil'), 100.1)
  assert.equal(snapTo(0.30000000000000004, 0.01), 0.3)
})

test('Nötr önizleme: fiyata en yakın çizgi boş, altı alış, üstü satış', () => {
  const out = previewGrid(base, 100, filters)
  assert.equal(out.ok, true, out.issues.join('|'))
  assert.equal(out.lines.length, 9)
  assert.equal(out.lines.filter(l => l.kind === 'skip').length, 1)
  assert.ok(out.lines.filter(l => l.kind === 'buy').every(l => l.price < 100))
  assert.ok(out.lines.filter(l => l.kind === 'sell').every(l => l.price > 100))
  assert.equal(out.activeLevels, 8)
  assert.ok(out.worstLoss > 0 && out.profitPerCycle > 0)
})

test('Fiyat aralığın dışındaysa nötr grid reddedilir', () => {
  const out = previewGrid(base, 105, filters)
  assert.equal(out.ok, false)
  assert.match(out.issues.join(' '), /arasında/)
})

test('LONG: stop alt sınırın üstündeyse ve kademeler fiyatın üstündeyse engel var', () => {
  const long = {...base, mode: 'LONG', lower: 96, upper: 99.6, stop: 94.5}
  assert.equal(previewGrid(long, 100, filters).ok, true)
  assert.equal(previewGrid({...long, stop: 97}, 100, filters).ok, false)
  assert.equal(previewGrid({...long, upper: 101}, 100, filters).ok, false)
})

test('SHORT: ters taraf kontrolü', () => {
  const short = {...base, mode: 'SHORT', lower: 100.4, upper: 104, stop: 105.5}
  assert.equal(previewGrid(short, 100, filters).ok, true)
  assert.equal(previewGrid({...short, stop: 103}, 100, filters).ok, false)
})

test('Kâr ücreti karşılamıyorsa ve kademe çok küçükse engellenir', () => {
  const tight = {...base, mode: 'LONG', lower: 99.9, upper: 99.95, count: 10, stop: 99.0}
  assert.equal(previewGrid(tight, 100, {...filters, tickSize: 0.01}).ok, false)
  const small = {...base, margin: 12, count: 10}
  assert.match(previewGrid(small, 100, filters).issues.join(' '), /en küçük emir büyüklüğü/)
})

test('Politika zarar sınırı ve kaldıraç tavanı uygulanır', () => {
  const out = previewGrid(base, 100, filters, {maxLoss: 0.5, maxLeverage: 2})
  assert.equal(out.ok, false)
  assert.match(out.issues.join(' '), /tek işlem sınırı/)
  assert.match(out.issues.join(' '), /Kaldıraç en fazla 2x/)
})

test('Borsa kuralları yoksa bile önizleme bozulmaz ama yuvarlama yapılmaz', () => {
  assert.equal(previewGrid(base, 100, null).ok, true)
  assert.equal(previewGrid(base, null, null).ok, false)
})

test('Öneri her modda kendi önizlemesinden geçer', () => {
  for (const mode of ['LONG', 'SHORT', 'NEUTRAL']) for (const preset of ['analysis', 'tight', 'wide']) {
    const s = suggestGrid(mode, 100, null, preset, {tick: 0.1, margin: 100, leverage: 3, minNotional: 5, maxCount: 20})
    assert.ok(s, `${mode}/${preset}`)
    const out = previewGrid({mode, lower: s.lower, upper: s.upper, count: s.count, stop: s.stop, stopUpper: s.stopUpper, margin: 100, leverage: 3, recycle: true}, 100, filters)
    assert.equal(out.ok, true, `${mode}/${preset}: ${out.issues.join('|')}`)
  }
})

test('Analiz aynı yöndeyse stop analizden gelir; ters yöndeyse sabit yüzdeye düşer', () => {
  const analysis = {direction: 'LONG', entry: 100, stop_loss: 97, tp1: 103, tp2: 106, tp3: 110}
  const a = suggestGrid('LONG', 100, analysis, 'analysis', {tick: 0.1, margin: 100, leverage: 3})
  assert.equal(a.source, 'analysis')
  assert.ok(a.stop < a.lower && a.upper < 100)
  const b = suggestGrid('SHORT', 100, analysis, 'analysis', {tick: 0.1, margin: 100, leverage: 3})
  assert.ok(b.stop > b.upper && b.lower > 100)
})

test('Küçük teminatta kademe sayısı azalır, hep alt sınırın üstünde kalır', () => {
  const s = suggestGrid('NEUTRAL', 100, null, 'wide', {tick: 0.1, margin: 20, leverage: 1, minNotional: 5})
  assert.ok(s.count >= 2 && s.count <= 3)
})

test('Geçersiz fiyat öneri üretmez', () => {
  assert.equal(suggestGrid('LONG', 0, null, 'tight', {margin: 10, leverage: 1}), null)
  assert.equal(suggestGrid('LONG', NaN, null, 'tight', {margin: 10, leverage: 1}), null)
})

test('startBody: nötr üst stop ve onay metni; tek yönlü üst stop göndermez', () => {
  const neutral = startBody('BTCUSDT', base, true)
  assert.equal(neutral.stop_upper, 103.5)
  assert.equal(neutral.confirmation, 'DEMO GRID BAŞLAT')
  assert.equal(neutral.recycle, true)
  const long = startBody('BTCUSDT', {...base, mode: 'LONG', recycle: false}, false)
  assert.equal('stop_upper' in long, false)
  assert.equal(long.confirmation, 'CANLI GRID BAŞLAT')
  assert.equal(long.recycle, false)
})

test('gridPnl: gerçekleşen + açık kademelerin anlık değeri', () => {
  const row = {side: 'LONG', stats: {cycles: 2, realized_usdt: 1.5, open_levels: [{level: 1, cycle: 0, entry: 100, qty: 2}]}}
  assert.deepEqual(gridPnl(row, 101), {realized: 1.5, unrealized: 2, total: 3.5, cycles: 2})
  assert.equal(gridPnl(row, null).total, null)
  const neutral = {side: 'NEUTRAL', stats: {cycles: 0, realized_usdt: 0, open_levels: [{level: 5, cycle: 0, entry: 101, qty: 1, side: 'SHORT'}]}}
  assert.equal(gridPnl(neutral, 100).unrealized, 1)
})

test('Pahalı coinde miktar adımı kademe boyutunu belirler: öneri kademeyi azaltır, önizleme gereken teminatı söyler', () => {
  const btc = {tickSize: 0.1, stepSize: 0.001, minNotional: 5, minQty: 0.001}
  const s = suggestGrid('NEUTRAL', 85000, null, 'tight', {tick: 0.1, step: 0.001, margin: 300, leverage: 5, minNotional: 5})
  assert.ok(s.count <= 18)
  const ok = previewGrid({mode: 'NEUTRAL', lower: s.lower, upper: s.upper, count: s.count, stop: s.stop, stopUpper: s.stopUpper, margin: 300, leverage: 5, recycle: true}, 85000, btc)
  assert.equal(ok.ok, true, ok.issues.join('|'))
  const low = previewGrid({mode: 'NEUTRAL', lower: s.lower, upper: s.upper, count: 8, stop: s.stop, stopUpper: s.stopUpper, margin: 50, leverage: 2, recycle: true}, 85000, btc)
  assert.equal(low.ok, false)
  assert.match(low.issues.join(' '), /teminatı en az ~\d+ USDT/)
})

import {atrPct, backtestGrid, barSeconds, gridRangeStatus, gridSummary} from '../studio-grid.ts'

const bars = (prices, start = 1_700_000_000, step = 900) => prices.map((p, i) => { const open = i === 0 ? p : prices[i - 1]; return {time: start + i * step, open, high: Math.max(open, p) + 0.05, low: Math.min(open, p) - 0.05, close: p} })

test('barSeconds saniye ve milisaniye zamanını tanır; ATR yetersiz mumda null', () => {
  assert.equal(barSeconds(bars([1, 2, 3, 4])), 900)
  assert.equal(barSeconds(bars([1, 2, 3, 4], 1_700_000_000_000, 900_000)), 900)
  assert.equal(barSeconds(bars([1, 2])), null)
  assert.equal(atrPct(bars([100, 101, 100])), null)
  const a = atrPct(bars(Array.from({length: 40}, (_, i) => 100 + (i % 2 ? 1 : -1))))
  assert.ok(a > 1.9 && a < 2.3, String(a))
})

test('ATR ön ayarı: hareketli coinde geniş, sakin coinde dar aralık önerir', () => {
  const calm = suggestGrid('NEUTRAL', 100, null, 'atr', {tick: 0.01, margin: 300, leverage: 5, atr: {pct: 0.05, barSeconds: 900}})
  const wild = suggestGrid('NEUTRAL', 100, null, 'atr', {tick: 0.01, margin: 300, leverage: 5, atr: {pct: 0.4, barSeconds: 900}})
  assert.ok(wild.upper - wild.lower > calm.upper - calm.lower)
  assert.match(wild.note, /ATR/)
  const none = suggestGrid('NEUTRAL', 100, null, 'atr', {tick: 0.01, margin: 300, leverage: 5})
  assert.match(none.note, /yeterli mum yok/)
})

const nParams = {mode: 'NEUTRAL', lower: 98, upper: 102, count: 8, stop: 96.5, stopUpper: 103.5, margin: 200, leverage: 3, recycle: true}
const nPrev = () => previewGrid(nParams, 100, {tickSize: 0.01, stepSize: 0.001, minNotional: 5, minQty: 0.001})

test('Backtest: yatay fiyatta tur ve net kâr oluşur, stop çalışmaz', () => {
  const zig = Array.from({length: 120}, (_, i) => 100 + Math.sin(i / 2) * 1.4)
  const out = backtestGrid(nParams, nPrev(), 100, bars(zig))
  assert.ok(out.cycles >= 3, `cycles ${out.cycles}`)
  assert.ok(out.net > 0 && out.fees > 0)
  assert.equal(out.stopped, false)
  assert.ok(out.inRangePct > 80)
  assert.ok(out.maxDrawdown >= 0)
})

test('Backtest: tek yönlü sert düşüşte stop çalışır, zarar kaydedilir', () => {
  const drop = Array.from({length: 40}, (_, i) => 100 - i * 0.4)
  const out = backtestGrid(nParams, nPrev(), 100, bars(drop))
  assert.equal(out.stopped, true)
  assert.ok(out.stoppedAt > 0 && out.stoppedAt < 40)
  assert.ok(out.net < 0)
  assert.equal(out.openLevels, 0)
})

test('Backtest: pencerenin başındaki fiyata ölçeklenir (fiyat çok farklı olsa da aynı şekil)', () => {
  const zig = Array.from({length: 120}, (_, i) => 50 + Math.sin(i / 2) * 0.7)
  const out = backtestGrid(nParams, nPrev(), 100, bars(zig))
  assert.ok(out.cycles >= 3, `cycles ${out.cycles}`)
})

test('Backtest: geçersiz plan ya da az mum sonuç üretmez', () => {
  assert.equal(backtestGrid(nParams, nPrev(), 100, bars([100, 101])), null)
  assert.equal(backtestGrid({...nParams, lower: 99, upper: 98}, previewGrid({...nParams, lower: 99, upper: 98}, 100, null), 100, bars(Array(30).fill(100))), null)
})

test('gridRangeStatus aralık dışını ve içini ayırt eder; gridSummary süreyi hesaplar', () => {
  const row = {spec: {levels: [{entry: 98, target: 99}, {entry: 102, target: 101}]}}
  assert.equal(gridRangeStatus(row, 100).state, 'inside')
  assert.equal(gridRangeStatus(row, 103).state, 'above')
  assert.equal(gridRangeStatus(row, 96).state, 'below')
  assert.equal(gridRangeStatus(row, null).state, 'unknown')
  assert.equal(gridRangeStatus({}, 100).state, 'unknown')
  assert.equal(gridSummary({created_at: 1000, updated_at: 8200, stats: {cycles: 4, realized_usdt: 1.2, fees_usdt: 0.1}}).hours, 2)
  assert.equal(gridSummary({stats: null}).hours, null)
})
