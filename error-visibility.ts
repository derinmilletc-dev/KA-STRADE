// Who may see technical error details, and how to recover from a stale site version.
let staffView = false

export function setStaffView(value: boolean): void { staffView = value }
export function isStaffView(): boolean { return staffView }

const STALE_BUILD = /Unable to preload|Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading (CSS )?chunk [\w-]+ failed/i

// After a new deploy the open tab still asks for old file names that no longer exist.
export function isStaleBuildError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error ?? '')
  return STALE_BUILD.test(text)
}

// One automatic reload per minute: enough to pick up the new version, never a loop.
export function reloadForNewVersion(): boolean {
  try {
    const key = 'kais-stale-reload-at'
    const last = Number(window.sessionStorage.getItem(key) || 0)
    if (Date.now() - last < 60_000) return false
    window.sessionStorage.setItem(key, String(Date.now()))
  } catch {
    return false
  }
  window.location.reload()
  return true
}
