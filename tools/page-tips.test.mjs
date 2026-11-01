import assert from 'node:assert/strict'
import {test} from 'node:test'
import {PAGE_TIPS, nextTipIndex, pageTipsKey, previousTipIndex, tipPlacement} from '../page-tips.ts'

test('every tips group has a unique id, short copy and real selectors', () => {
  for (const [key, group] of Object.entries(PAGE_TIPS)) {
    assert.equal(group.id, key)
    assert.ok(group.tips.length >= 3 && group.tips.length <= 5)
    assert.equal(new Set(group.tips.map(tip => tip.title)).size, group.tips.length)
    for (const tip of group.tips) {
      assert.ok(tip.targets.length > 0 && tip.targets.every(selector => selector.trim().length > 0))
      assert.ok(tip.title.length <= 32 && tip.text.length <= 190, tip.title)
    }
  }
})

test('tips copy makes no profit promises and does not claim to place orders', () => {
  const text = JSON.stringify(PAGE_TIPS).toLowerCase()
  for (const word of ['kazanırsın', 'kesin kâr', 'risksiz', 'para kazan', 'garanti']) assert.ok(!text.includes(word), word)
})

test('stepping forward ends after the last tip and backward stops at the first', () => {
  assert.equal(nextTipIndex(0, 4), 1)
  assert.equal(nextTipIndex(3, 4), null)
  assert.equal(previousTipIndex(0), 0)
  assert.equal(previousTipIndex(3), 2)
})

test('the card goes below the target when there is room, above when not, else docks', () => {
  assert.equal(tipPlacement({top: 100, height: 60}, 170, 800), 'below')
  assert.equal(tipPlacement({top: 600, height: 60}, 170, 800), 'above')
  assert.equal(tipPlacement({top: 150, height: 600}, 170, 800), 'dock')
  assert.equal(tipPlacement(null, 170, 800), 'dock')
})

test('storage keys are per member and per group', () => {
  assert.notEqual(pageTipsKey('a', 'market'), pageTipsKey('b', 'market'))
  assert.notEqual(pageTipsKey('a', 'market'), pageTipsKey('a', 'risk'))
})
