import assert from 'node:assert/strict'
import fs from 'node:fs'
import test from 'node:test'

const read = name => JSON.parse(fs.readFileSync(new URL(`../${name}`, import.meta.url), 'utf8'))
const en = read('i18n-en.json')
const tr = read('i18n-tr.json')
const count = (text, token) => text.split(token).length - 1

test('EN sözlüğünde çeviri, anahtardan fazla yer tutucu içermez', () => {
  for (const [key, value] of en.p) {
    assert.ok(count(value, '{}') <= count(key, '{}') + count(key, '{#}'), `EN kalıbı: ${key}`)
  }
})

test('TR sözlüğünde çeviri, anahtardan fazla yer tutucu içermez', () => {
  for (const [key, value] of tr.p) {
    assert.ok(count(value, '{}') <= count(key, '{}') + count(key, '{#}'), `TR kalıbı: ${key}`)
  }
})

test('Stüdyo grafik, desen ve gösterge etiketleri İngilizce çevirisine sahiptir', () => {
  for (const key of ['Çekiç', 'Yutan boğa', 'Sabah yıldızı', 'Çizgi', 'Kapanış çizgisi ve altı dolgulu', 'Oynaklık bandı', 'Trend ve momentum kesişimi', 'Strateji adı', 'Sermaye eğrisi', 'Replay hızı']) {
    assert.ok(typeof en.e[key] === 'string' && en.e[key].length > 0, `EN çevirisi yok: ${key}`)
  }
})

test('Yüzde ifadesi İngilizcede sayıdan sonra gelir', () => {
  const pair = en.p.find(([key]) => key === '%{#} hesap')
  assert.equal(pair[1], '{}% of account')
})
