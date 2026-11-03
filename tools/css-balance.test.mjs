import assert from 'node:assert/strict'
import {test} from 'node:test'
import {readdirSync, readFileSync} from 'node:fs'
import {join} from 'node:path'

// Vite tüm CSS dosyalarını tek pakette birleştirir: bir dosyada eksik "}" kalırsa ardından gelen
// bütün dosyaların kuralları o blokun içine düşer (giriş sayfası stilsiz açılır). Her dosya dengeli olmalı.
const root = new URL('..', import.meta.url).pathname
const files = [...readdirSync(root).filter(name => name.endsWith('.css')).map(name => join(root, name)),
  ...readdirSync(join(root, 'frontend/src')).filter(name => name.endsWith('.css')).map(name => join(root, 'frontend/src', name))]

function balance(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '').replace(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g, '""')
  let depth = 0, line = 1
  for (const ch of text) {
    if (ch === '\n') line += 1
    else if (ch === '{') depth += 1
    else if (ch === '}') { depth -= 1; if (depth < 0) return {depth, line} }
  }
  return {depth, line}
}

test('Her CSS dosyasında süslü parantezler dengeli', () => {
  assert.ok(files.length > 10)
  for (const file of files) {
    const {depth, line} = balance(readFileSync(file, 'utf8'))
    assert.equal(depth, 0, `${file.replace(root, '')}: ${depth > 0 ? `${depth} kapanmamış "{"` : `fazla "}" (satır ${line})`}`)
  }
})
