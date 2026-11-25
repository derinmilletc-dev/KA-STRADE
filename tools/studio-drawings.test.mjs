import assert from 'node:assert/strict'
import test from 'node:test'
import {advanceDraft, channelLines, channelOffset, draftPreview, durationText, fibLevels, measure, priceAt, rectBounds} from '../studio-draw-geometry.ts'
import {mergeDrawings, parseRemote} from '../studio-drawing-sync.ts'

globalThis.localStorage = {getItem: () => null, setItem: () => {}}
const {sanitizeDrawings, TEXT_LIMIT, DRAWING_LIMIT} = await import('../chart-drawings.ts')

const P = (time, price) => ({time, price})

test('fib: %0 = B, %100 = A, 0.5 ortada, uzantı A\'yı aşar', () => {
  const levels = fibLevels(P(0, 100), P(10, 200))
  const at = ratio => levels.find(level => level.ratio === ratio).price
  assert.equal(at(0), 200); assert.equal(at(1), 100); assert.equal(at(0.5), 150)
  assert.ok(Math.abs(at(0.618) - 138.2) < 1e-9)
  assert.ok(at(1.618) < 100)
  const down = fibLevels(P(0, 200), P(10, 100))
  assert.equal(down.find(level => level.ratio === 0.5).price, 150)
  assert.ok(down.find(level => level.ratio === 1.618).price > 200)
})

test('rect: köşe sırası fark etmez', () => {
  assert.deepEqual(rectBounds(P(10, 5), P(2, 9)), {t1: 2, t2: 10, high: 9, low: 5})
})

test('priceAt/channel: eğim korunur, paralel doğru C\'den geçer', () => {
  const a = P(0, 100), b = P(10, 110)
  assert.equal(priceAt(a, b, 5), 105)
  assert.equal(priceAt(P(3, 7), P(3, 9), 3), 7) // dikey doğru bölme hatası vermez
  const c = P(5, 112)
  assert.equal(channelOffset(a, b, c), 7)
  const lines = channelLines(a, b, c)
  assert.equal(lines.parallel.a.price, 107); assert.equal(lines.parallel.b.price, 117)
  assert.equal(lines.middle.a.price, 103.5)
  assert.equal(priceAt(lines.parallel.a, lines.parallel.b, 5), c.price)
})

test('measure: fark, yüzde, mum sayısı, süre (sn ve ms zaman)', () => {
  const m = measure(P(1000, 100), P(1000 + 3600, 110), 900)
  assert.equal(m.delta, 10); assert.equal(m.pct, 10); assert.equal(m.bars, 4); assert.equal(m.up, true)
  const ms = measure(P(1_700_000_000_000, 50), P(1_700_000_000_000 + 60_000, 45), 60)
  assert.equal(ms.bars, 1); assert.equal(ms.up, false); assert.equal(ms.pct, -10)
  assert.equal(measure(P(0, 0), P(60, 5), 60).pct, null)
  assert.equal(durationText(90), '1dk'); assert.equal(durationText(20), '1dk'); assert.equal(durationText(3 * 3600 + 120), '3sa 2dk'); assert.equal(durationText(90000), '1g 1sa')
})

test('sanitizeDrawings: eski türler korunur, bozuklar ve tekrarlar atılır, metin kırpılır, sınır uygulanır', () => {
  const ok = [
    {id: 'a', type: 'hline', price: 10}, {id: 'b', type: 'trend', a: P(1, 1), b: P(2, 2)},
    {id: 'c', type: 'fib', a: P(1, 1), b: P(2, 2)}, {id: 'd', type: 'channel', a: P(1, 1), b: P(2, 2), c: P(1, 5)},
    {id: 'e', type: 'text', p: P(1, 1), text: `  ${'x'.repeat(200)}  `},
  ]
  const bad = [{id: 'f', type: 'hline', price: NaN}, {id: 'g', type: 'trend', a: P(1, 1)}, {id: 'h', type: 'text', p: P(1, 1), text: '  '}, {id: 'a', type: 'hline', price: 99}, {type: 'hline', price: 1}, null, 'x', {id: 'i', type: 'bilinmeyen'}]
  const out = sanitizeDrawings([...ok, ...bad])
  assert.deepEqual(out.map(item => item.id), ['a', 'b', 'c', 'd', 'e'])
  assert.equal(out[4].text.length, TEXT_LIMIT)
  assert.equal(out[0].price, 10)
  assert.deepEqual(sanitizeDrawings('x'), [])
  const many = Array.from({length: 80}, (_, i) => ({id: `id${i}`, type: 'hline', price: i + 1}))
  const limited = sanitizeDrawings(many)
  assert.equal(limited.length, DRAWING_LIMIT); assert.equal(limited[limited.length - 1].id, 'id79')
  assert.equal(sanitizeDrawings([{id: 'z', type: 'hline', price: 1, alarm: {side: 'sideways'}}]).length, 0)
})

test('merge: yeni olan kazanır, sunucu yoksa yerel gönderilir', () => {
  const local = {drawings: [{id: 'a', type: 'hline', price: 1}], updatedAt: 100}
  const remote = {drawings: [], updatedAt: 200}
  assert.deepEqual(mergeDrawings(local, remote), {record: remote, push: false, changedLocal: true})
  assert.deepEqual(mergeDrawings({...local, updatedAt: 300}, remote), {record: {...local, updatedAt: 300}, push: true, changedLocal: false})
  assert.equal(mergeDrawings(local, null).push, true)
  assert.equal(mergeDrawings({drawings: [], updatedAt: 0}, null).push, false)
  assert.equal(mergeDrawings(local, {...local}).push, false)
})

test('merge: eşitleme öncesi yerel çizimler (damga 0) sunucudakiyle birleşir, hiçbiri kaybolmaz', () => {
  const legacy = {drawings: [{id: 'old', type: 'hline', price: 1}, {id: 'same', type: 'hline', price: 2}], updatedAt: 0}
  const remote = {drawings: [{id: 'same', type: 'hline', price: 2}, {id: 'phone', type: 'hline', price: 3}], updatedAt: 500}
  const merged = mergeDrawings(legacy, remote, 1000)
  assert.deepEqual(merged.record.drawings.map(item => item.id), ['same', 'phone', 'old'])
  assert.equal(merged.push, true); assert.equal(merged.changedLocal, true); assert.equal(merged.record.updatedAt, 1000)
  // yerelin hepsi zaten sunucuda: sunucu kaydı alınır, tekrar gönderilmez
  const covered = mergeDrawings({drawings: [{id: 'same', type: 'hline', price: 2}], updatedAt: 0}, remote, 1000)
  assert.equal(covered.push, false); assert.equal(covered.record, remote)
  // yerel boş ve damga 0: normal kural (sunucu yeni)
  assert.equal(mergeDrawings({drawings: [], updatedAt: 0}, remote, 1000).record, remote)
})

test('parseRemote: geçersiz yanıtı reddeder, çizimleri doğrular', () => {
  assert.equal(parseRemote(null, sanitizeDrawings), null); assert.equal(parseRemote({drawings: []}, sanitizeDrawings), null)
  const parsed = parseRemote({drawings: [{id: 'a', type: 'hline', price: 2}, {id: 'x'}], updated_at: 5}, sanitizeDrawings)
  assert.deepEqual(parsed, {drawings: [{id: 'a', type: 'hline', price: 2}], updatedAt: 5})
})

test('advanceDraft: iki tıklamalı araçlar, üç tıklamalı kanal, metin, yinelenen nokta', () => {
  let n = 0
  const id = () => `id${++n}`
  for (const mode of ['trend', 'fib', 'rect', 'measure']) {
    const first = advanceDraft(mode, [], P(1, 10), id)
    assert.deepEqual(first, {anchors: [P(1, 10)], drawing: null, askText: null})
    assert.deepEqual(advanceDraft(mode, first.anchors, P(1, 10), id).anchors, [P(1, 10)]) // aynı nokta sayılmaz
    const done = advanceDraft(mode, first.anchors, P(5, 20), id)
    assert.equal(done.drawing.type, mode); assert.deepEqual([done.drawing.a, done.drawing.b], [P(1, 10), P(5, 20)]); assert.deepEqual(done.anchors, [])
  }
  let step = advanceDraft('channel', [], P(1, 1), id)
  step = advanceDraft('channel', step.anchors, P(2, 2), id)
  assert.equal(step.drawing, null); assert.equal(step.anchors.length, 2)
  step = advanceDraft('channel', step.anchors, P(1, 5), id)
  assert.equal(step.drawing.type, 'channel'); assert.deepEqual(step.drawing.c, P(1, 5))
  assert.deepEqual(advanceDraft('text', [], P(3, 3), id), {anchors: [], drawing: null, askText: P(3, 3)})
  assert.deepEqual(advanceDraft('none', [], P(3, 3), id), {anchors: [], drawing: null, askText: null})
})

test('draftPreview: nokta yoksa/imleç yoksa null; kanal 1 noktada çizgi, 2 noktada kanal', () => {
  assert.equal(draftPreview('fib', [], P(1, 1)), null)
  assert.equal(draftPreview('fib', [P(0, 0)], null), null)
  assert.equal(draftPreview('rect', [P(0, 0)], P(2, 2)).type, 'rect')
  assert.equal(draftPreview('channel', [P(0, 0)], P(2, 2)).type, 'trend')
  assert.equal(draftPreview('channel', [P(0, 0), P(2, 2)], P(1, 5)).type, 'channel')
  assert.equal(draftPreview('text', [P(0, 0)], P(2, 2)), null)
})
