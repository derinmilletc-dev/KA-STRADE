export type Lang = 'tr' | 'en'
const KEY = 'kt_lang'
type Dict = { e: Record<string, string>; p: [string, string][] }

export function currentLang(): Lang {
  try {
    const saved = window.localStorage.getItem(KEY)
    if (saved === 'en' || saved === 'tr') return saved
  } catch { /* storage may be unavailable */ }
  return 'tr'
}

export function setLang(lang: Lang) {
  try { window.localStorage.setItem(KEY, lang) } catch { /* ignore */ }
  window.location.reload()
}

const norm = (s: string) => s.replace(/\s+/g, ' ').trim()
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

let exact: Record<string, string> = {}
let lowerMap: Record<string, string> = {}
const trLower = (s: string) => s.replace(/İ/g, 'i').replace(/I/g, 'ı').toLowerCase()
const trUpper = (s: string) => s.replace(/i/g, 'İ').replace(/ı/g, 'I').toUpperCase()
let patterns: { re: RegExp; en: string }[] = []
let trMode = false
let enToTr: Record<string, string> = {}
const cache = new Map<string, string>()

function translateToTr(key: string): string | null {
  const v = enToTr[key.toLowerCase()]
  if (v === undefined) return null
  const upper = key === key.toUpperCase() && /[A-Z]/.test(key) && key.length > 2
  return upper ? trUpper(v) : v
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

export function translateText(text: string): string {
  if (!text) return text
  const key = norm(text)
  if (!key || !/[A-Za-zÇĞİÖŞÜçğıöşü]/.test(key)) return text
  let out = cache.get(key)
  if (out === undefined) {
    out = translateCore(key, 0) ?? key
    cache.set(key, out)
  }
  if (out === key) return text
  const lead = /^\s*/.exec(text)?.[0] ?? ''
  const trail = /\s*$/.exec(text)?.[0] ?? ''
  return lead + out + trail
}

const ATTRS = ['placeholder', 'title', 'aria-label', 'alt']
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

function skipped(node: Node): boolean {
  for (let el: Node | null = node; el; el = el.parentNode) {
    if (el.nodeType === 1) {
      const e = el as Element
      if (SKIP.has(e.tagName) || e.hasAttribute('data-i18n-skip') || e.getAttribute('translate') === 'no' || e.getAttribute('contenteditable') === 'true') return true
    }
  }
  return false
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

function walk(root: Node) {
  if (root.nodeType === 3) {
    const t = root as Text
    if (!skipped(t) && !brandPiece(t)) { const v = translateText(t.nodeValue ?? ''); if (v !== t.nodeValue) t.nodeValue = v }
    return
  }
  if (root.nodeType !== 1 || skipped(root)) return
  fixAttrs(root as Element)
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
  let n = tw.nextNode()
  while (n) {
    if (n.nodeType === 1) {
      const e = n as Element
      if (SKIP.has(e.tagName) || e.hasAttribute('data-i18n-skip') || e.getAttribute('translate') === 'no') {
        n = nextAfter(tw, e)
        continue
      }
      fixAttrs(e)
    } else {
      const t = n as Text
      const v = brandPiece(t) ? t.nodeValue : translateText(t.nodeValue ?? '')
      if (v !== t.nodeValue) t.nodeValue = v
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

function patchLocales() {
  const map = (l: unknown) => (typeof l === 'string' && /^tr(-|$)/i.test(l) ? 'en-US' : l)
  const num = Number.prototype.toLocaleString
  Number.prototype.toLocaleString = function (l?: Intl.LocalesArgument, o?: Intl.NumberFormatOptions) { return num.call(this, map(l) as string, o) }
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
  const lang = currentLang()
  document.documentElement.lang = lang
  if (lang === 'tr') {
    try {
      const mod = await import('./i18n-tr.json')
      enToTr = ((mod.default ?? mod) as unknown as { e: Record<string, string> }).e
      trMode = true
    } catch { return }
    const startTr = () => {
      walk(document.body)
      const obs = new MutationObserver(records => {
        for (const r of records) {
          if (r.type === 'characterData') walk(r.target)
          else if (r.type === 'attributes') { if (!skipped(r.target)) fixAttrs(r.target as Element) }
          else r.addedNodes.forEach(n => walk(n))
        }
      })
      obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS })
    }
    if (document.body) startTr(); else document.addEventListener('DOMContentLoaded', startTr)
    return
  }
  try {
    const mod = await import('./i18n-en.json')
    const dict = (mod.default ?? mod) as unknown as Dict
    exact = dict.e
    lowerMap = {}
    for (const k of Object.keys(exact)) { const l = trLower(k); if (!(l in lowerMap)) lowerMap[l] = exact[k] }
    patterns = dict.p.map(([tr, en]) => ({
      re: new RegExp('^' + esc(tr).replace(/\\\{\\\}/g, '(.+?)').replace(/\{\}/g, '(.+?)') + '$'),
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
      for (const r of records) {
        if (r.type === 'characterData') walk(r.target)
        else if (r.type === 'attributes') { if (!skipped(r.target)) fixAttrs(r.target as Element) }
        else r.addedNodes.forEach(n => walk(n))
      }
      if (/Kripto Analiz|[çğıöşü]/i.test(document.title)) fixHead()
    })
    obs.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS })
    obs.observe(document.head, { subtree: true, childList: true, characterData: true })
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start)
}
