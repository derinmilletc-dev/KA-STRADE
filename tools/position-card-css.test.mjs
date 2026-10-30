import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'

// Master/Demo sayfasında genel bir kural (arka plan/çerçeve/yuvarlaklık: 0 !important) kutuları düzleştiriyordu.
// Pozisyon kartı görselleri bu yüzden daha özgül !important ile yeniden verilir.
const cockpit = readFileSync(new URL('../live-cockpit.css', import.meta.url), 'utf8')
const strong = 'html body #root .masterTradePage.masterTrade[data-layout-tab="canli"] .lcRoot.lcRoot.lcRoot.lcRoot .pvRoot'

test('pozisyon kartı kutu stilleri genel sıfırlama kuralını yener', () => {
  for (const part of ['.pvCard{', '.pvTile{', '.pvSeg{', '.pvChip{']) {
    const line = cockpit.split('\n').find(row => row.startsWith(strong + ' ' + part))
    assert.ok(line, `${part} için güçlü kural yok`)
    assert.match(line, /background[^;}]*!important/)
  }
})

test('seçili sekme düğmesi okunur (yeşil zemin, koyu yazı)', () => {
  const line = cockpit.split('\n').find(row => row.startsWith(strong + ' .pvSeg button[aria-pressed="true"]'))
  assert.ok(line)
  assert.match(line, /background:var\(--lc-go\)!important/)
  assert.match(line, /color:var\(--lc-go-ink\)!important/)
})
