export type Lang = 'tr' | 'en'
const KEY = 'kt_lang'
type Dict = { e: Record<string, string>; p: [string, string][] }

/** Sitenin çevirisi hazır olan diller. Yeni dil eklemek için buraya satır + çeviri dosyası eklenir. */
export const LANGUAGES: {code: Lang; native: string; en: string}[] = [
  {code: 'tr', native: 'Türkçe', en: 'Turkish'},
  {code: 'en', native: 'English', en: 'English'},
]

function browserLang(): Lang {
  try {
    const list = navigator.languages?.length ? navigator.languages : [navigator.language]
    for (const l of list) {
      const code = String(l || '').toLowerCase().slice(0, 2)
      if (code === 'tr' || code === 'en') return code
    }
  } catch { /* ignore */ }
  return 'en'
}

export function currentLang(): Lang {
  try {
    const saved = window.localStorage.getItem(KEY)
    if (saved === 'en' || saved === 'tr') return saved
  } catch { /* storage may be unavailable */ }
  return browserLang()
}

export function setLang(lang: Lang) {
  try { window.localStorage.setItem(KEY, lang) } catch { /* ignore */ }
  window.location.reload()
}

/** Hesapta kayıtlı dil bu cihazdakinden farklıysa hesaptakini uygular. */
export function applyAccountLang(lang: unknown) {
  if ((lang === 'tr' || lang === 'en') && lang !== currentLang()) setLang(lang)
}

const norm = (s: string) => s.replace(/\s+/g, ' ').trim()
/** Yazım (’) ve düz (') kesme işareti aynı sayılır: sözlük anahtarları ve aranan metin düz kesmeye çevrilir. */
const apo = (s: string) => s.replace(/[\u2018\u2019]/g, "'")
const apoKeys = (o: Record<string, string>) => { const r: Record<string, string> = {}; for (const k of Object.keys(o)) { const n = apo(k); if (!(n in r) || n === k) r[n] = o[k] } return r }
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

let exact: Record<string, string> = {}
let lowerMap: Record<string, string> = {}
const trLower = (s: string) => s.replace(/İ/g, 'i').replace(/I/g, 'ı').toLowerCase()
const trUpper = (s: string) => s.replace(/i/g, 'İ').replace(/ı/g, 'I').toUpperCase()
let patterns: { re: RegExp; en: string }[] = []
let trMode = false
let enToTr: Record<string, string> = {}
let trPatterns: { re: RegExp; tr: string }[] = []
const cache = new Map<string, string>()

function translateToTr(key: string, depth = 0): string | null {
  const v = enToTr[key.toLowerCase()]
  if (v !== undefined) {
    const upper = key === key.toUpperCase() && /[A-Z]/.test(key) && key.length > 2
    return upper ? trUpper(v) : v
  }
  if (depth > 2) return null
  for (const { re, tr } of trPatterns) {
    const m = re.exec(key)
    if (!m) continue
    let i = 0
    return tr.replace(/\{\}/g, () => {
      const part = m[++i] ?? ''
      return translateToTr(norm(part), depth + 1) ?? part
    })
  }
  return null
}

function translateCore(key: string, depth: number): string | null {
  if (trMode) return translateToTr(key)
  const hit = exact[key]
  if (hit !== undefined) return hit
  const low = lowerMap[trLower(key)]
  if (low !== undefined) {
    const upper = key === trUpper(key) && /[a-zçğıöşü]/i.test(key) && key.length > 2
    return upper ? low.toUpperCase() : low
  }
  if (depth > 2) return null
  for (const { re, en } of patterns) {
    const m = re.exec(key)
    if (!m) continue
    let i = 0
    return en.replace(/\{\}/g, () => {
      const part = m[++i] ?? ''
      return translateCore(norm(part), depth + 1) ?? part
    })
  }
  return null
}

const HAS_LETTER = /[A-Za-zÇĞİÖŞÜçğıöşü]/
// Canlı fiyat/skor metinleri ("RSI 65,37", "Skor 58,7") her tikte yeni anahtar üretir; sınırsız önbellek
// uzun açık kalan sekmede belleği şişiriyordu. Sınır aşılınca önbellek sıfırlanır (sözlük aynı kalır).
const CACHE_LIMIT = 4000

export function translateText(text: string): string {
  if (!text || !HAS_LETTER.test(text)) return text
  const key = apo(norm(text))
  if (!key) return text
  let out = cache.get(key)
  if (out === undefined) {
    out = translateCore(key, 0) ?? key
    if (cache.size >= CACHE_LIMIT) cache.clear()
    cache.set(key, out)
  }
  if (out === key) return text
  const lead = /^\s*/.exec(text)?.[0] ?? ''
  const trail = /\s*$/.exec(text)?.[0] ?? ''
  return lead + out + trail
}

const ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-mobile-copy', 'data-label']
const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'NOSCRIPT', 'CODE', 'PRE'])

/** Site adı (KaisTrade) hiçbir dilde çevrilmez: "kais<b>trade</b>" gibi bölünmüş yazımda da parçalar korunur. */
function brandPiece(t: Text): boolean {
  const value = (t.nodeValue ?? '').trim().toLowerCase()
  if (!value || !/^(kais|trade|kaistrade|kais trade)$/.test(value)) return false
  for (let el: Node | null = t.parentNode, depth = 0; el && depth < 3; el = el.parentNode, depth++) {
    if ((el.textContent ?? '').replace(/\s+/g, '').toLowerCase().includes('kaistrade')) return true
  }
  return false
}

const SKIP_SELECTOR = 'script,style,textarea,noscript,code,pre,[data-i18n-skip],[translate="no"],[contenteditable="true"]'

/** Düğüm veya atalarından biri çeviri dışıysa true. Tarayıcının yerel closest() sorgusu, ataları JS döngüsüyle gezmekten çok daha hızlıdır. */
const ATTR_SKIP_SELECTOR = 'script,style,noscript,code,pre,[data-i18n-skip],[translate="no"],[contenteditable="true"]'
/** Metin kutularının (textarea) içeriği çevrilmez ama placeholder/title gibi öznitelikleri çevrilir. */
function attrsSkipped(el: Element): boolean { return el.closest(ATTR_SKIP_SELECTOR) !== null }

function skipped(node: Node): boolean {
  const el = node.nodeType === 1 ? node as Element : node.parentElement
  return el ? el.closest(SKIP_SELECTOR) !== null : false
}

function fixAttrs(el: Element) {
  for (const a of ATTRS) {
    const v = el.getAttribute(a)
    if (v) { const t = translateText(v); if (t !== v) el.setAttribute(a, t) }
  }
  if (el.tagName === 'INPUT') {
    const input = el as HTMLInputElement
    if (input.type === 'button' || input.type === 'submit') {
      const t = translateText(input.value)
      if (t !== input.value) input.value = t
    }
  }
}


// React, "%{x} işlem riski" gibi ifadeleri art arda birkaç metin düğümüne böler ("%", "10", " işlem riski").
// Tek tek çevrilemeyen bu parçalar bitişik düğümler birleştirilip bir bütün olarak çevrilir; çeviri
// ilk düğüme yazılır, diğerleri boşaltılır. Özgün değer WeakMap'te tutulur: React bir düğümü
// güncellediğinde (nodeValue bizim yazdığımızdan farklıysa) yeni değer özgün kabul edilir.
const written = new WeakMap<Text, {out: string; src: string}>()
function rawOf(t: Text): string {
  const w = written.get(t)
  const v = t.nodeValue ?? ''
  return w && v === w.out ? w.src : v
}
function setTranslated(t: Text, src: string, out: string) {
  if (t.nodeValue !== out) t.nodeValue = out
  written.set(t, {out, src})
}
const isText = (n: Node | null): n is Text => !!n && n.nodeType === 3
function textRunHead(t: Text): Text {
  let head = t
  while (isText(head.previousSibling)) head = head.previousSibling
  return head
}
function translateRun(head: Text) {
  const run: Text[] = []
  for (let n: Node | null = head; isText(n); n = n.nextSibling) run.push(n)
  if (run.length < 2) { translateSingle(head); return }
  const raws = run.map(rawOf)
  const joined = raws.join('')
  const out = translateText(joined)
  if (out !== joined) {
    setTranslated(run[0], raws[0], out)
    for (let i = 1; i < run.length; i++) setTranslated(run[i], raws[i], '')
    return
  }
  run.forEach(translateSingle)
}
function translateSingle(t: Text) {
  const raw = rawOf(t)
  const v = brandPiece(t) ? raw : translateText(raw)
  if (v !== t.nodeValue) t.nodeValue = v
  if (v !== raw) written.set(t, {out: v, src: raw}); else written.delete(t)
}
function handleText(t: Text) {
  if (skipped(t)) return
  translateRun(textRunHead(t))
}

function walk(root: Node) {
  if (root.nodeType === 3) {
    handleText(root as Text)
    return
  }
  if (root.nodeType !== 1) return
  if (skipped(root)) { if ((root as Element).tagName === 'TEXTAREA' && !attrsSkipped(root as Element)) fixAttrs(root as Element); return }
  fixAttrs(root as Element)
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  let n = tw.nextNode()
  while (n) {
    if (n.nodeType === 1) {
      const e = n as Element
      if (SKIP.has(e.tagName) || e.hasAttribute('data-i18n-skip') || e.getAttribute('translate') === 'no') {
        if (e.tagName === 'TEXTAREA' && !attrsSkipped(e)) fixAttrs(e)
        n = nextAfter(tw, e)
        continue
      }
      fixAttrs(e)
    } else {
      const t = n as Text
      if (!isText(t.previousSibling)) translateRun(t)
    }
    n = tw.nextNode()
  }
}

function nextAfter(tw: TreeWalker, el: Element): Node | null {
  let cur: Node | null = el
  while (cur && !cur.nextSibling) {
    cur = cur.parentNode
    if (!cur || cur === tw.root) return null
  }
  if (!cur) return null
  tw.currentNode = cur.nextSibling as Node
  return cur.nextSibling
}

/** MutationObserver kayıtlarını tekilleştirerek işler: aynı tikte gelen yinelenen düğümler ve
 *  zaten eklenmiş bir alt ağacın içinde kalan değişiklikler bir kez taranır. */
function processMutations(records: MutationRecord[]) {
  const added = new Set<Node>()
  const texts = new Set<Node>()
  const attrs = new Set<Element>()
  for (const r of records) {
    if (r.type === 'characterData') texts.add(r.target)
    else if (r.type === 'attributes') attrs.add(r.target as Element)
    else r.addedNodes.forEach(n => { if (n.isConnected) added.add(n) })
  }
  const roots = [...added].filter(n => { for (let p = n.parentNode; p; p = p.parentNode) if (added.has(p)) return false; return true })
  const inRoots = (n: Node) => roots.some(root => root !== n && root.contains(n))
  for (const n of roots) walk(n)
  for (const t of texts) if (t.isConnected && !inRoots(t)) walk(t)
  for (const el of attrs) if (el.isConnected && !inRoots(el) && (!skipped(el) || (el.tagName === 'TEXTAREA' && !attrsSkipped(el)))) fixAttrs(el)
}

// Number.prototype.toLocaleString her çağrıda yeni bir biçimlendirici kurar; canlı fiyatlar saniyede
// yüzlerce kez biçimlendirildiği için bu, profilde en pahalı JS kalemiydi. Aynı dil+seçenek için
// Intl.NumberFormat bir kez kurulup yeniden kullanılır; çıktı birebir aynıdır.
const NativeNumberFormat = Intl.NumberFormat
const nativeNumberToLocale = Number.prototype.toLocaleString
const numberFormats = new Map<string, Intl.NumberFormat>()
let numberLocale = (l: unknown) => l

function installNumberFormatCache() {
  Number.prototype.toLocaleString = function (this: number, l?: Intl.LocalesArgument, o?: Intl.NumberFormatOptions) {
    const locale = numberLocale(l) as Intl.LocalesArgument
    let key: string
    try { key = `${locale === undefined ? '~' : JSON.stringify(locale)}|${o === undefined ? '~' : JSON.stringify(o)}` }
    catch { return nativeNumberToLocale.call(this, locale as string, o) }
    let formatter = numberFormats.get(key)
    if (!formatter) {
      if (numberFormats.size >= 200) numberFormats.clear()
      formatter = new NativeNumberFormat(locale, o)
      numberFormats.set(key, formatter)
    }
    return formatter.format(Number(this))
  }
}

function patchLocales() {
  const map = (l: unknown) => (typeof l === 'string' && /^tr(-|$)/i.test(l) ? 'en-US' : l)
  numberLocale = map
  for (const name of ['toLocaleString', 'toLocaleDateString', 'toLocaleTimeString'] as const) {
    const orig = Date.prototype[name]
    Date.prototype[name] = function (l?: Intl.LocalesArgument, o?: Intl.DateTimeFormatOptions) { return orig.call(this, map(l) as string, o) }
  }
  for (const name of ['NumberFormat', 'DateTimeFormat'] as const) {
    const Orig = Intl[name] as unknown as new (...a: unknown[]) => unknown
    const handler: ProxyHandler<typeof Orig> = {
      construct: (t, args) => Reflect.construct(t, [map(args[0]), args[1]]) as object,
      apply: (t, _this, args) => (t as unknown as (...a: unknown[]) => unknown)(map(args[0]), args[1]),
    }
    ;(Intl as unknown as Record<string, unknown>)[name] = new Proxy(Orig, handler)
  }
}

function patchDialogs() {
  const a = window.alert.bind(window)
  const c = window.confirm.bind(window)
  const p = window.prompt.bind(window)
  window.alert = (m?: unknown) => a(translateText(String(m ?? '')))
  window.confirm = (m?: string) => c(translateText(String(m ?? '')))
  window.prompt = (m?: string, d?: string) => p(translateText(String(m ?? '')), d)
}

const META_TITLE = 'KaisTrade | Crypto Analysis and Trading Platform'
const META_DESC = 'Follow crypto markets with KaisTrade, review AI analyses and evaluate your strategies with demo trades.'

function fixHead() {
  document.title = translateText(document.title) === document.title && /Kripto Analiz/.test(document.title) ? META_TITLE : translateText(document.title)
  document.querySelectorAll('meta[name="description"],meta[property="og:description"],meta[name="twitter:description"]').forEach(m => m.setAttribute('content', META_DESC))
  document.querySelectorAll('meta[property="og:title"],meta[name="twitter:title"]').forEach(m => m.setAttribute('content', META_TITLE))
  document.querySelector('meta[property="og:locale"]')?.setAttribute('content', 'en_US')
}

export async function initI18n(): Promise<void> {
  installNumberFormatCache()
  const lang = currentLang()
  document.documentElement.lang = lang
  if (lang === 'tr') {
    try {
      const mod = await import('./i18n-tr.json')
      const trDict = (mod.default ?? mod) as unknown as { e: Record<string, string>; p?: [string, string][] }
      enToTr = apoKeys(trDict.e)
      trPatterns = (trDict.p ?? []).map(([en, tr]) => ({ re: new RegExp('^' + esc(apo(en)).replace(/\\\{#\\\}/g, '(\\d[\\d.,]*)').replace(/\\\{\\\}/g, '(.+?)').replace(/\{\}/g, '(.+?)') + '$', 'i'), tr }))
      trMode = true
    } catch { return }
    const startTr = () => {
      walk(document.body)
      const obs = new MutationObserver(processMutations)
      obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS })
    }
    if (document.body) startTr(); else document.addEventListener('DOMContentLoaded', startTr)
    return
  }
  try {
    const mod = await import('./i18n-en.json')
    const dict = (mod.default ?? mod) as unknown as Dict
    exact = apoKeys(dict.e)
    lowerMap = {}
    for (const k of Object.keys(exact)) { const l = trLower(k); if (!(l in lowerMap)) lowerMap[l] = exact[k] }
    patterns = dict.p.map(([tr, en]) => ({
      re: new RegExp('^' + esc(apo(tr)).replace(/\\\{#\\\}/g, '(\\d[\\d.,]*)').replace(/\\\{\\\}/g, '(.+?)').replace(/\{\}/g, '(.+?)') + '$'),
      en,
    }))
  } catch {
    document.documentElement.lang = 'tr'
    return
  }
  patchLocales()
  patchDialogs()
  fixHead()
  const start = () => {
    walk(document.body)
    const obs = new MutationObserver(records => {
      processMutations(records)
      if (/Kripto Analiz|[çğıöşü]/i.test(document.title)) fixHead()
    })
    obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS })
    obs.observe(document.head, { subtree: true, childList: true, characterData: true })
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start)
}
