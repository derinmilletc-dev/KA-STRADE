import assert from 'node:assert/strict'
import test from 'node:test'
import {bandPaths, segmentPaths, symmetricExtent, valueText} from '../studio-overlay-paths.ts'

const xOf = i => i * 10
const yOf = v => 100 - v

test('segmentPaths: pencere kesilir, null çizgiyi koparır', () => {
  const series = [null, 1, 2, null, 4, 5, 6]
  assert.deepEqual(segmentPaths(series, 0, 7, xOf, yOf), ['M10 99L20 98', 'M40 96L50 95L60 94'])
  assert.deepEqual(segmentPaths(series, 4, 7, xOf, yOf), ['M0 96L10 95L20 94'])
  assert.deepEqual(segmentPaths(undefined, 0, 3, xOf, yOf), [])
  assert.deepEqual(segmentPaths([1, 2], 5, 9, xOf, yOf), [])
})

test('segmentPaths: sonsuz y atlanır, tek nokta yol verir', () => {
  assert.deepEqual(segmentPaths([1, 2, 3], 0, 3, xOf, v => v === 2 ? Infinity : v), ['M0 1L20 3'])
  assert.deepEqual(segmentPaths([null, 7, null], 0, 3, xOf, yOf), ['M10 93'])
})

test('bandPaths: yalnız iki seri de doluyken kapalı bant, tek nokta bant sayılmaz', () => {
  const upper = [null, 10, 12, 14], lower = [null, 4, 5, 6]
  const [path] = bandPaths(upper, lower, 0, 4, xOf, yOf)
  assert.ok(path.startsWith('M10 90L20 88L30 86L30 94L20 95L10 96Z'), path)
  assert.deepEqual(bandPaths([5], [3], 0, 1, xOf, yOf), [])
  assert.deepEqual(bandPaths(upper, undefined, 0, 4, xOf, yOf), [])
})

test('symmetricExtent ve valueText', () => {
  assert.equal(symmetricExtent([[1, -4, null], undefined], 0, 3), 4 * 1.15)
  assert.equal(symmetricExtent([[null]], 0, 1), 1)
  assert.equal(valueText(1234.5678), '1,234.57'); assert.equal(valueText(0.123456), '0.12'); assert.equal(valueText(null), '—')
})
