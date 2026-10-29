import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'

const source = readFileSync(new URL('../user-scroll.ts', import.meta.url), 'utf8')

test('menü yalnız kullanıcı girdisiyle gizlenir: iki kaydırma dinleyicisi de userIsScrolling ister', () => {
  const master = readFileSync(new URL('../MasterTrade.tsx', import.meta.url), 'utf8')
  const app = readFileSync(new URL('../TestnetFirstApp.tsx', import.meta.url), 'utf8')
  assert.match(master, /if \(!userIsScrolling\(\)\)/)
  assert.match(app, /if \(!userIsScrolling\(\)\) return/)
})

test('kullanıcı girdi olayları dinlenir ve pencere kısadır', () => {
  for (const name of ['touchmove', 'wheel', 'keydown']) assert.ok(source.includes(`'${name}'`))
  assert.match(source, /USER_SCROLL_WINDOW_MS = 700/)
})
