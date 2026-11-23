import assert from 'node:assert/strict'
import test from 'node:test'
import {dayLabelOf, groupByDay} from './coinbase-activity-view.ts'

const now = new Date(2026, 10, 23, 22, 0)
const at = (day: number, h: number) => new Date(2026, 10, day, h, 0).toISOString()

test('labels', () => {
  assert.equal(dayLabelOf(at(23, 9), now), 'Bugün')
  assert.equal(dayLabelOf(at(22, 9), now), 'Dün')
  assert.equal(dayLabelOf('bad', now), '')
  assert.match(dayLabelOf(at(20, 9), now), /20/)
})
test('groups by day keeping order and summing known pnl', () => {
  const rows = [{time: at(23, 19), pnl: null}, {time: at(23, 17), pnl: '-0.1'}, {time: at(23, 16), pnl: '3.8'}, {time: at(22, 10), pnl: null}, {time: 'x', pnl: '5'}]
  const groups = groupByDay(rows, now)
  assert.deepEqual(groups.map(g => [g.label, g.items.length]), [['Bugün', 3], ['Dün', 1]])
  assert.ok(Math.abs((groups[0].pnl ?? 0) - 3.7) < 1e-9)
  assert.equal(groups[1].pnl, null)
})
