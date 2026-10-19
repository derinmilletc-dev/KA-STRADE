/** Müşteri sitesi ile yönetim (admin/moderatör) yüzeyi ayrımı.
 *  Yüzey, giriş noktasıyla belirlenir: index.html → 'customer', admin.html → 'admin'.
 *  Alan adları derleme zamanında verilir; ayarlı değilse hiçbir yönlendirme yapılmaz (mevcut davranış korunur). */
export type Surface = 'customer' | 'admin'

function cleanOrigin(value: unknown, fallback = ''): string {
  if (typeof value !== 'string' || !value.trim()) return fallback
  try {
    const url = new URL(value.trim())
    const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1'
    if (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) return fallback
    return url.origin
  } catch { return fallback }
}

/** Örn. https://admin.kaistrade.com — boşsa ayrı yönetim alan adı henüz yok demektir. */
export const ADMIN_ORIGIN = cleanOrigin(import.meta.env.VITE_ADMIN_ORIGIN)
/** Yönetim yüzeyinden müşteri sitesine dönüş adresi. */
export const CUSTOMER_ORIGIN = cleanOrigin(import.meta.env.VITE_CUSTOMER_ORIGIN, 'https://kaistrade.com')

/** Yönetim yolu için gidilecek adres: ayrı alan adı ayarlıysa oraya, değilse aynı site içinde. */
export function staffUrl(path: string, currentOrigin: string = window.location.origin): string {
  return ADMIN_ORIGIN && ADMIN_ORIGIN !== currentOrigin ? `${ADMIN_ORIGIN}${path}` : path
}

/** Müşteri sitesinde bir yönetim yolu açıldıysa yönetim alan adına gönderir. true = yönlendirme başladı. */
export function redirectStaffPathToAdminOrigin(path: string, currentOrigin: string = window.location.origin): boolean {
  const target = staffUrl(path, currentOrigin)
  if (target === path) return false
  window.location.replace(target)
  return true
}

const STAFF_PREFIXES = ['/admin', '/moderator', '/settings', '/profile']

/** Yönetim yüzeyinde bir rolün görebileceği yol; müşteri sayfalarına (/dashboard, / ...) asla düşmez. */
export function staffSurfacePath(role: string, path: string): string {
  const landing = role === 'OWNER' ? '/admin' : '/moderator'
  const allowed = STAFF_PREFIXES.some(prefix => path === prefix || path.startsWith(`${prefix}/`))
  if (!allowed) return landing
  if (role !== 'OWNER' && (path === '/admin' || path.startsWith('/admin/'))) return landing
  return path
}
