import assert from 'node:assert/strict'
import {test} from 'node:test'
import {entryScript, isNewVersion} from '../update-check.ts'

test('entry script is read from built index.html', () => {
  assert.equal(entryScript('<head><script type="module" crossorigin src="/assets/index-AbC123.js"></script></head>'), '/assets/index-AbC123.js')
  assert.equal(entryScript('<script src="/assets/index-Z9.js" type="module"></script>'), '/assets/index-Z9.js')
  assert.equal(entryScript('<script type="module" src="/main.tsx"></script>'), null)
})

test('new version only when both sides are known and differ', () => {
  assert.equal(isNewVersion('/assets/index-a.js', '/assets/index-b.js'), true)
  assert.equal(isNewVersion('/assets/index-a.js', '/assets/index-a.js'), false)
  assert.equal(isNewVersion(null, '/assets/index-b.js'), false)
  assert.equal(isNewVersion('/assets/index-a.js', null), false)
})
