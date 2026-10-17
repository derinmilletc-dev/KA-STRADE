// Dil kontrolü: node i18n-check.mjs
// Kaynakta çevirisi eksik Türkçe (→EN) ve İngilizce (→TR) metinleri listeler.
import fs from 'node:fs'
import path from 'node:path'
const root = path.dirname(new URL(import.meta.url).pathname)
const en = JSON.parse(fs.readFileSync(path.join(root, 'i18n-en.json'), 'utf8')).e
const tr = JSON.parse(fs.readFileSync(path.join(root, 'i18n-tr.json'), 'utf8')).e
const enLow = new Set(Object.keys(en).map(k => k.toLocaleLowerCase('tr')))
const files = []
const walk = d => { for (const f of fs.readdirSync(d, { withFileTypes: true })) {
  if (['node_modules', 'dist', '.git'].includes(f.name)) continue
  const p = path.join(d, f.name)
  if (f.isDirectory()) walk(p); else if (/\.tsx$/.test(f.name)) files.push(p) } }
walk(root)
const missEn = new Set(), missTr = new Set()
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8')
  const lits = [...src.matchAll(/>([^<>{}\n]{3,100})</g)].map(m => m[1]).concat([...src.matchAll(/(?:placeholder|title|aria-label)="([^"{}]{3,100})"/g)].map(m => m[1]))
  for (const raw of lits) {
    const t = raw.replace(/\s+/g, ' ').trim()
    if (!/[A-Za-zçğıöşüÇĞİÖŞÜ]{3}/.test(t) || /[=;()\\_#@$]|https?:/.test(t)) continue
    if (/[çğıöşüÇĞİÖŞÜ]/.test(t)) { if (!enLow.has(t.toLocaleLowerCase('tr'))) missEn.add(t) }
    else if (t.includes(' ') || /^[A-Z]/.test(t)) { if (!(t.toLowerCase() in tr) && !enLow.has(t.toLocaleLowerCase('tr'))) missTr.add(t) }
  }
}
console.log('EN çevirisi eksik (Türkçe metin):', missEn.size); [...missEn].slice(0, 400).forEach(s => console.log('  ' + s))
console.log('TR çevirisi eksik (İngilizce metin):', missTr.size); [...missTr].slice(0, 400).forEach(s => console.log('  ' + s))
