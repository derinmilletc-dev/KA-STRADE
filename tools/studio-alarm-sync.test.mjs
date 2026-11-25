import assert from 'node:assert/strict'
import test from 'node:test'
import {alarmDiff, applyServerState} from '../studio-alarm-sync.ts'

const line = (id, price, alarm) => ({id, type: 'hline', price, ...(alarm ? {alarm} : {})})

test('yeni alarm sunucuya açılır, bağlı ve çalmış olanlar açılmaz', () => {
  const next = [line('a', 100, {side: 'above'}), line('b', 90, {side: 'below', serverId: 5}), line('c', 80, {side: 'below', fired: true}), line('d', 70)]
  const {create, remove} = alarmDiff([], next)
  assert.deepEqual(create.map(item => item.id), ['a'])
  assert.deepEqual(remove, [])
})
test('silinen veya alarmı kaldırılan çizgi sunucudan da silinir', () => {
  const prev = [line('a', 100, {side: 'above', serverId: 7}), line('b', 90, {side: 'below', serverId: 8})]
  assert.deepEqual(alarmDiff(prev, [prev[0]]).remove, [8])
  assert.deepEqual(alarmDiff(prev, [line('a', 100), prev[1]]).remove, [7])
  assert.deepEqual(alarmDiff(prev, []).remove.sort(), [7, 8])
})
test('çalmış alarmın silinmesi sunucuya istek atmaz', () => {
  const prev = [line('a', 100, {side: 'above', serverId: 7, fired: true})]
  assert.deepEqual(alarmDiff(prev, []).remove, [])
})
test('sunucuda çalan alarm yerelde çaldı olur', () => {
  const list = [line('a', 100, {side: 'above', serverId: 7}), line('b', 90, {side: 'below', serverId: 8})]
  const out = applyServerState(list, {alerts: [{id: 8}], fired: [{id: 7}]})
  assert.equal(out.changed, true)
  assert.deepEqual(out.firedLevels, [100])
  assert.equal(out.list[0].alarm.fired, true)
  assert.equal(out.list[1], list[1])
})
test('sunucuda silinmiş alarmın bağı kopar, değişiklik yoksa aynı liste döner', () => {
  const list = [line('a', 100, {side: 'above', serverId: 7})]
  const out = applyServerState(list, {alerts: [], fired: []})
  assert.equal(out.list[0].alarm.serverId, undefined)
  const same = applyServerState(list, {alerts: [{id: 7}], fired: []})
  assert.equal(same.list, list); assert.equal(same.changed, false)
})
test('yerel alarmlar (bağsız) sunucu durumundan etkilenmez', () => {
  const list = [line('a', 100, {side: 'above'})]
  assert.equal(applyServerState(list, {alerts: [], fired: []}).list, list)
})
