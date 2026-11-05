// E-posta adresi denetimi (sitede, yazarken). Sunucudaki backend/app/email_check.py ile aynı kuralları aynı dosyadan okur;
// ikisi de backend/tests/email_check_cases.json örnekleriyle sınanır. Durumlar için oradaki açıklamaya bakın.
import rules from './backend/app/email_rules.json' with {type: 'json'}

export type EmailStatus = 'ok' | 'empty' | 'invalid' | 'tld' | 'typo' | 'maybe' | 'disposable'
export type EmailResult = {status: EmailStatus; email: string; suggestion: string | null}

const PROVIDERS = rules.providers as Record<string, string[]>
const REGIONAL = new Set<string>(rules.regional)
const FUZZY: readonly string[] = rules.fuzzy
const DISPOSABLE = new Set<string>(rules.disposable)
const TLDS = new Set<string>(rules.tlds)
const LOCAL_RE = /^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$/
const LABEL_RE = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/
const TURKISH: Record<string, string> = {'ı': 'i', 'İ': 'i', 'ş': 's', 'Ş': 's', 'ğ': 'g', 'Ğ': 'g', 'ü': 'u', 'Ü': 'u', 'ö': 'o', 'Ö': 'o', 'ç': 'c', 'Ç': 'c'}
const COMMON_TLDS = ['com', 'net', 'org', 'tr', 'io', 'co', 'de', 'uk', 'info', 'me', 'ai', 'app', 'dev', 'edu', 'gov'] as const
/** Gerçek ama sağlayıcı adreslerinde neredeyse hep yazım hatası olan uzantılar. */
const AMBIGUOUS = new Set(['co', 'cm', 'om', 'cmo', 'co.tr', 'cm.tr'])

/** Damerau-Levenshtein (bitişik harf yer değiştirme 1 sayılır). */
export function distance(a: string, b: string): number {
  const d = Array.from({length: a.length + 1}, (_, i) => Array.from({length: b.length + 1}, (_, j) => (i === 0 ? j : j === 0 ? i : 0)))
  for (let i = 1; i <= a.length; i += 1) {
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1)
    }
  }
  return d[a.length][b.length]
}

const nearest = (word: string, options: readonly string[]) =>
  options.reduce((best, option) => (distance(word, option) < distance(word, best) ? option : best), options[0])

function syntaxOk(email: string): boolean {
  if (email.length > 254 || email.split('@').length !== 2) return false
  const [local, domain] = email.split('@')
  const labels = domain.split('.')
  if (!local || local.length > 64 || !LOCAL_RE.test(local) || labels.length < 2) return false
  if (!labels.every(label => LABEL_RE.test(label))) return false
  const tld = labels[labels.length - 1]
  return /^[a-z]+$/.test(tld) || tld.startsWith('xn--')
}

function clean(raw: string): string {
  let value = raw.replace(/[ıİşŞğĞüÜöÖçÇ]/g, char => TURKISH[char]).toLowerCase()
  value = value.replace(/\s+/g, '').replace(/[,;]/g, '.').replace(/@+/g, '@').replace(/\.{2,}/g, '.').replace(/^\.+|\.+$/g, '')
  return value.replace('.@', '@').replace('@.', '@')
}

function fuzzy(first: string, suffix: string | null): string | null {
  if (first.length < 4 || first in PROVIDERS) return null
  let best: [number, string] | null = null
  for (const name of FUZZY) {
    const gap = distance(first, name)
    if (gap <= (name.length <= 6 ? 1 : 2) && (best === null || gap < best[0])) best = [gap, name]
  }
  if (!best) return null
  const allowed = PROVIDERS[best[1]]
  return `${best[1]}.${suffix !== null && allowed.includes(suffix) ? suffix : allowed[0]}`
}

function checkValid(email: string): EmailResult {
  const [local, domain] = email.split('@')
  const labels = domain.split('.')
  if (DISPOSABLE.has(domain) || [...DISPOSABLE].some(item => domain.endsWith(`.${item}`))) return {status: 'disposable', email, suggestion: null}
  const first = labels[0]
  const suffix = labels.slice(1).join('.')
  // Sağlayıcı adının arkası yalnız kısa uzantı parçalarıysa (com, co.uk, con.tr …) sağlayıcı adresi sayılır.
  const tldLike = labels.length <= 3 && labels.slice(1).every(label => /^[a-z]{1,4}$/.test(label))
  const allowed = Object.prototype.hasOwnProperty.call(PROVIDERS, first) ? PROVIDERS[first] : undefined
  const regionalOk = REGIONAL.has(first) && labels.slice(1).every(label => TLDS.has(label)) && !AMBIGUOUS.has(suffix)
  if (allowed && tldLike && !allowed.includes(suffix) && !regionalOk) return {status: 'typo', email, suggestion: `${local}@${first}.${nearest(suffix, allowed)}`}
  if (!TLDS.has(labels[labels.length - 1])) {
    let fixed = [...labels.slice(0, -1), nearest(labels[labels.length - 1], COMMON_TLDS)].join('.')
    if (!allowed && labels.length === 2) fixed = fuzzy(first, null) ?? fixed
    return {status: 'tld', email, suggestion: `${local}@${fixed}`}
  }
  if (!allowed && tldLike) {
    const match = fuzzy(first, suffix)
    if (match) return {status: 'maybe', email, suggestion: `${local}@${match}`}
  }
  return {status: 'ok', email, suggestion: null}
}

export function checkEmail(raw: string | null | undefined): EmailResult {
  const value = (raw ?? '').trim()
  if (!value) return {status: 'empty', email: '', suggestion: null}
  const email = value.toLowerCase()
  if (syntaxOk(email)) return checkValid(email)
  let candidate = clean(value)
  if (!candidate.includes('@')) {
    // "aligmail.com" → "ali@gmail.com"
    outer: for (const [name, allowedList] of Object.entries(PROVIDERS)) {
      for (const suffix of allowedList) {
        const tail = `${name}.${suffix}`
        if (candidate.endsWith(tail) && candidate.length > tail.length) { candidate = `${candidate.slice(0, -tail.length)}@${tail}`; break outer }
      }
    }
  } else {
    // "ali@gmail" → "ali@gmail.com"
    const at = candidate.indexOf('@')
    const local = candidate.slice(0, at), domain = candidate.slice(at + 1)
    if (!domain.includes('.') && Object.prototype.hasOwnProperty.call(PROVIDERS, domain)) candidate = `${local}@${domain}.${PROVIDERS[domain][0]}`
  }
  let suggestion: string | null = null
  if (candidate !== email && syntaxOk(candidate)) {
    const fixed = checkValid(candidate)
    suggestion = fixed.status === 'typo' || fixed.status === 'tld' || fixed.status === 'maybe' ? fixed.suggestion : fixed.status === 'ok' ? candidate : null
  }
  return {status: 'invalid', email, suggestion}
}

/** Gönderilebilir mi? "maybe" (olası yazım hatası) yalnız kullanıcı "adresim doğru" dediyse kabul edilir. */
export const emailAccepted = (result: EmailResult, confirmedMaybe: boolean) => result.status === 'ok' || (result.status === 'maybe' && confirmedMaybe)
