import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'

// Canlı İşlem ve Demo İşlem aynı kokpiti çizer. Eski live-cockpit.css 100 sütunlu ızgara kuralı taşma yapar;
// 12 sütunlu doğru düzen live-terminal.css'tedir. İki dosyanın yüklenme sırası sayfaya göre değişebildiği için
// (Demo'da live-cockpit.css sonra yükleniyordu, ekran bozuluyordu) doğru kurallar ÖZGÜLLÜKLE kazanmalı, sıraya güvenilmemeli.
const terminal = readFileSync(new URL('../live-terminal.css', import.meta.url), 'utf8')
const cockpit = readFileSync(new URL('../live-cockpit.css', import.meta.url), 'utf8')
const gridSelectors = (css) => css.split('\n').filter(line => /\.lcGrid\b/.test(line) && /^\s*html body #root/.test(line))

test('eski 100 sütunlu kural iki lcRoot ile yazılı (referans)', () => {
  const old = cockpit.split('\n').find(line => line.includes('repeat(100,minmax(0,1fr))'))
  assert.ok(old, 'live-cockpit.css 100 sütun kuralı bulunamadı; bu test güncellenmeli')
  assert.ok(old.includes('.lcRoot.lcRoot .lcGrid'))
  assert.ok(!old.includes('.lcRoot.lcRoot.lcRoot'))
})

test('live-terminal.css ızgara kuralları eski kuraldan daha özgül (yükleme sırasından bağımsız)', () => {
  const rules = gridSelectors(terminal)
  assert.ok(rules.length >= 8, `beklenenden az ızgara kuralı: ${rules.length}`)
  for (const line of rules) assert.ok(line.includes('.lcRoot.lcRoot.lcRoot .lcGrid'), `özgüllük düşük: ${line.slice(0, 120)}`)
  assert.match(terminal, /\.lcGrid\s*\{\s*grid-template-columns:repeat\(12,minmax\(0,1fr\)\)!important/)
})
