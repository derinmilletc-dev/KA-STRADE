import assert from 'node:assert/strict'
import {test} from 'node:test'
import './register-ts-resolve.mjs'

const {buildInsights, candleRead, publishEngineNote, readEngineNote, withEngineNote, MIN_INSIGHT_CANDLES, structureRead, swings, tfBias, mtfRead, aiPreview, publishAiNote, readAiNote, higherTimeframes} = await import('../studio-insights.ts')

const make = (n, fn) => Array.from({length: n}, (_, i) => { const c = fn(i); return {time: 1_700_000_000 + i * 900, open: c.open ?? c.close, high: c.high ?? c.close + 1, low: c.low ?? c.close - 1, close: c.close, volume: c.volume ?? 100} })

test('az mumda yorum hazır değil', () => {
  assert.equal(buildInsights({candles: make(MIN_INSIGHT_CANDLES - 1, i => ({close: 100 + i}))}).ready, false)
})

test('istikrarlı yükselişte trend yükseliş, genel görünüm yükseliş yönlü', () => {
  const candles = make(120, i => ({close: 100 + i * 0.8, open: 100 + i * 0.8 - 0.3, high: 100 + i * 0.8 + 0.5, low: 100 + i * 0.8 - 0.6}))
  const result = buildInsights({candles, analysis: {direction: 'LONG', support: 150, resistance: 220}, decision: {direction: 'LONG', confidenceScore: 70, mtfConfirmed: 4, mtfTotal: 5, riskReward: 1.5}})
  assert.equal(result.ready, true)
  assert.equal(result.items.find(i => i.id === 'trend').tone, 'up')
  assert.ok(/yükseliş/i.test(result.verdict.text))
  const plan = result.items.find(i => i.id === 'plan')
  assert.ok(plan.text.includes('MTF 4/5') && plan.text.includes('1:1,5'))
  assert.ok(result.summary.startsWith('Motor yorumu') && result.summary.length <= 560)
})

test('düşüşte trend düşüş ve plan LONG ise çelişki uyarısı', () => {
  const candles = make(120, i => ({close: 200 - i * 0.8}))
  const result = buildInsights({candles, decision: {direction: 'LONG', confidenceScore: 60}})
  assert.equal(result.items.find(i => i.id === 'trend').tone, 'down')
  assert.ok(result.items.find(i => i.id === 'plan').text.includes('çelişiyor'))
})

test('mum formları: yutan mum ve uzun fitil', () => {
  const base = make(40, i => ({close: 100}))
  const bull = [...base.slice(0, -2), {time: 1, open: 101, high: 101.2, low: 99.4, close: 99.6, volume: 100}, {time: 2, open: 99.4, high: 102.2, low: 99.3, close: 102, volume: 100}]
  assert.equal(candleRead(bull).tone, 'up')
  const wick = [...base.slice(0, -1), {time: 3, open: 100, high: 100.3, low: 97, close: 100.1, volume: 100}]
  assert.equal(candleRead(wick).tone, 'up')
})

test('motor notu: bayat ve yanlış anahtarda boş; soru sınırı aşılmaz', () => {
  publishEngineNote('BTCUSDT:15m', 'özet metni')
  assert.equal(readEngineNote('BTCUSDT:15m'), 'özet metni')
  assert.equal(readEngineNote('ETHUSDT:15m'), '')
  assert.equal(readEngineNote('BTCUSDT:15m', -1), '')
  const q = 'Bu grafiği yorumla'
  const note = 'x'.repeat(900)
  const out = withEngineNote(q, note)
  assert.ok(out.length <= 800 && out.startsWith(q))
  assert.equal(withEngineNote(q, ''), q)
  assert.equal(withEngineNote('y'.repeat(790), note), 'y'.repeat(790))
})

const wave = (n, base, amp, drift = 0) => make(n, i => { const c = base + drift * i + Math.sin(i / 3) * amp; return {close: c, open: c - Math.cos(i / 3) * amp * .3, high: c + amp * .35, low: c - amp * .35} })

test('yapı: yükselen tepe/dipler HH/HL, alçalanlar LH/LL', () => {
  const upS = structureRead(wave(140, 100, 3, .5))
  assert.equal(upS.kind, 'up')
  const downS = structureRead(wave(140, 200, 3, -.5))
  assert.equal(downS.kind, 'down')
  assert.equal(structureRead(make(20, i => ({close: 100 + i}))), null)
  assert.ok(swings(wave(140, 100, 3)).highs.length >= 3)
})

test('yapı: son tepenin üstünde kapanış yukarı kırılım, dar bant sıkışma', () => {
  const base = wave(120, 100, 4)
  const hiPivot = swings(base).highs.slice(-1)[0].v
  const broke = [...base, {time: 9e9, open: hiPivot, high: hiPivot + 5, low: hiPivot - .2, close: hiPivot + 4, volume: 300}]
  assert.equal(structureRead(broke).bos, 'up')
  const squeeze = [...wave(100, 100, 6), ...make(30, i => ({close: 100 + (i % 2) * .05, open: 100, high: 100.1, low: 99.9}))]
  assert.equal(structureRead(squeeze).squeeze, true)
})

test('zaman dilimi eğilimi ve MTF uyumu', () => {
  assert.equal(tfBias(wave(140, 100, 3, .6)), 'up')
  assert.equal(tfBias(wave(140, 300, 3, -.6)), 'down')
  assert.equal(tfBias(make(30, i => ({close: 100}))), null)
  const conflict = mtfRead('up', [{tf: '1h', bias: 'down'}, {tf: '4h', bias: 'down'}])
  assert.equal(conflict.tone, 'warn'); assert.ok(conflict.text.includes('geri çekilme')); assert.equal(conflict.vote, -1)
  const aligned = mtfRead('down', [{tf: '1h', bias: 'down'}, {tf: '1d', bias: 'down'}])
  assert.equal(aligned.tone, 'down'); assert.ok(aligned.text.includes('1g'))
  assert.equal(mtfRead('up', [{tf: '1h', bias: null}]), null)
  assert.equal(mtfRead('flat', [{tf: '1h', bias: 'up'}]).vote, 0)
})

test('güven sayımı toplamı tutar ve MTF sonuca girer', () => {
  const candles = wave(140, 100, 3, .6)
  const r = buildInsights({candles, mtf: [{tf: '1h', candles: wave(140, 100, 3, .6)}, {tf: '4h', candles: wave(140, 300, 3, -.6)}]})
  assert.equal(r.confidence.up + r.confidence.down + r.confidence.neutral, r.confidence.total)
  assert.ok(r.items.some(i => i.id === 'mtf'))
  assert.equal(r.mtf.length, 2)
})

test('plan uyarıları: dar stop, yakın direnç, düşük R/Ö, plan çelişkisi', () => {
  const candles = wave(140, 300, 3, -.6)
  const price = candles[candles.length - 1].close
  const r = buildInsights({candles, price, analysis: {direction: 'LONG', entry: price, stop_loss: price - 0.05, resistance: price + 0.2, support: price - 8}, decision: {direction: 'LONG', riskReward: 0.7}})
  const ids = r.advice.map(a => a.id)
  assert.ok(ids.includes('adv-stop-tight')); assert.ok(ids.includes('adv-wall')); assert.ok(ids.includes('adv-rr')); assert.ok(ids.includes('adv-conflict'))
  assert.ok(r.summary.includes('Uyarı:'))
})

test('açık pozisyon: R değeri, başa baş ve TP1 önerileri', () => {
  const candles = wave(140, 100, 3, .6)
  const price = candles[candles.length - 1].close
  const r = buildInsights({candles, price, position: {side: 'LONG', entry: price - 4, stop: price - 6, tp1: price + 0.05}})
  const ids = r.advice.map(a => a.id)
  assert.ok(ids.includes('pos-r')); assert.ok(ids.includes('pos-be')); assert.ok(ids.includes('pos-tp'))
  assert.ok(r.advice.find(a => a.id === 'pos-r').text.includes('+2,0R'))
  const down = buildInsights({candles, price, position: {side: 'SHORT', entry: price + 1, stop: price + 3, tp1: price - 9}})
  assert.ok(down.advice.some(a => a.id === 'pos-against'))
})

test('Kais AI notu: anahtara bağlı, bayatsa boş; önizleme başlıksız ve kısa', () => {
  const text = 'Genel görünüm\nGrafik düşüş eğiliminde.\n\nRiskler\nDirenç yakın ve hacim zayıf.\n\nSon\nÜçüncü paragraf'
  publishAiNote('BTCUSDT:15m', text)
  assert.ok(readAiNote('BTCUSDT:15m'))
  assert.equal(readAiNote('ETHUSDT:15m'), null)
  assert.equal(readAiNote('BTCUSDT:15m', -1), null)
  assert.equal(aiPreview(text), 'Grafik düşüş eğiliminde. Direnç yakın ve hacim zayıf.')
  assert.ok(aiPreview('x'.repeat(900)).length <= 380)
})

test('üst zaman dilimleri', () => {
  assert.deepEqual(higherTimeframes('15m'), ['1h', '4h', '1d'])
  assert.deepEqual(higherTimeframes('1m'), ['5m', '15m', '1h'])
  assert.deepEqual(higherTimeframes('4h'), ['1d'])
  assert.deepEqual(higherTimeframes('1d'), [])
  assert.deepEqual(higherTimeframes('bilinmez'), [])
})
