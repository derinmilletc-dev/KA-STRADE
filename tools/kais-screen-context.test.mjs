import assert from 'node:assert/strict'
import {test} from 'node:test'
import {preferencesLine, readKaisScreenContext, setKaisScreenContext} from '../kais-screen-context.ts'
test('ekran bağlamı ve tercih satırı',()=>{
  assert.equal(preferencesLine(null),null)
  assert.equal(preferencesLine({symbols:['BTCUSDT','bad'],timeframe:'15m',risk_per_trade:0.5,trading_mode:'MANUAL'}),'Kayıtlı tercihleri: sevdiği coinler BTCUSDT · zaman dilimi 15m · işlem başı risk 0.5 · işlem bölümü MANUAL')
  setKaisScreenContext('a','Demo grafiği: BTCUSDT 15m'); setKaisScreenContext('b','x'.repeat(500))
  assert.ok(readKaisScreenContext().startsWith('Demo grafiği'))
  setKaisScreenContext('a',null); assert.equal(readKaisScreenContext().length,120)
})
