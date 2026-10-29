import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
const read = f => fs.readFileSync(new URL('../' + f, import.meta.url), 'utf8')

test('Demo ekranları çeviri dışında bırakılmaz (İngilizce modda Türkçe kalmasın)', () => {
  assert.ok(!read('DemoMasterShell.tsx').includes('data-i18n-skip'))
  assert.ok(!/demoMasterConnection[^\n]*data-i18n-skip/.test(read('BinanceDemo.tsx')))
})

test('Çeviri sözlükleri geçerli JSON ve yeni anahtarlar mevcut', () => {
  const en = JSON.parse(read('i18n-en.json'))
  const tr = JSON.parse(read('i18n-tr.json'))
  assert.equal(en.e['Demo İşlem Masası'], 'Demo Trading Desk')
  assert.equal(en.e['Bildirimleri aç'], 'Turn on notifications')
  assert.ok(Array.isArray(tr.p) && tr.p.length > 0)
  assert.ok(tr.e['no setups found'])
})
