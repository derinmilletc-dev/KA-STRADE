// Sekme arka plandayken sorgu atmaz (pil ve işlemci yükünü azaltır, uykudan dönünce yığılmayı önler).
// Sekme tekrar görününce ya da internet geri gelince sorguyu hemen, birbirinden hafif aralıklı yeniler.
type Entry = {run: () => unknown; last: number}
const entries = new Map<number, Entry>()
let bound = false

function resumeAll(): void {
  if (typeof document === 'undefined' || document.hidden) return
  const now = Date.now()
  let order = 0
  entries.forEach((entry, id) => {
    if (now - entry.last < 2000) return
    entry.last = now
    window.setTimeout(() => {
      if (!entries.has(id) || document.hidden) return
      try { void entry.run() } catch { /* sorgu kendi hatasını gösterir */ }
    }, 150 * order++)
  })
}

export function visibleInterval(run: () => unknown, ms: number): number {
  if (!bound && typeof document !== 'undefined') {
    bound = true
    document.addEventListener('visibilitychange', resumeAll)
    window.addEventListener('online', resumeAll)
  }
  const entry: Entry = {run, last: Date.now()}
  const id = window.setInterval(() => {
    if (document.hidden) return
    entry.last = Date.now()
    run()
  }, ms)
  entries.set(id, entry)
  return id
}

export function clearVisibleInterval(id: number | undefined): void {
  if (id === undefined) return
  window.clearInterval(id)
  entries.delete(id)
}
