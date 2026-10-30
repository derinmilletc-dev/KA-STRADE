import {API_BASE} from './api'
import {withRequestDeadline} from './browser-request'
import {ModeratorRequestError} from './moderator-model'

export async function moderatorRequest<T>(path: string, parse: (value: unknown) => T, signal: AbortSignal): Promise<T> {
  return moderatorApiRequest(path, parse, signal, {method: 'GET'})
}

export async function moderatorApiRequest<T>(path: string, parse: (value: unknown) => T, signal: AbortSignal, options: RequestInit): Promise<T> {
  return withRequestDeadline(async transportSignal => {
    const headers = new Headers(options.headers)
    headers.set('X-Requested-With', 'XMLHttpRequest')
    if (options.body) headers.set('Content-Type', 'application/json')
    const response = await fetch(`${API_BASE}/mod${path}`, {
      ...options, credentials: 'same-origin', headers, signal: transportSignal,
    })
    if (!response.ok) {
      const payload: unknown = await response.json()
      const detail = payload && typeof payload === 'object' && 'detail' in payload ? payload.detail : null
      const code = detail && typeof detail === 'object' && 'code' in detail && detail.code === 'mfa_required' ? 'mfa_required' : ''
      throw new ModeratorRequestError(response.status, code)
    }
    return parse(await response.json())
  }, {signal, timeoutMs: 20000})
}

/** Moderatör paneli dosya indirme (ör. destek talebi eki): ham içerik, JSON değil. */
export async function moderatorBlob(path: string, signal: AbortSignal): Promise<Blob> {
  return withRequestDeadline(async transportSignal => {
    const response = await fetch(`${API_BASE}/mod${path}`, {
      method: 'GET', credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}, signal: transportSignal,
    })
    if (!response.ok) throw new ModeratorRequestError(response.status)
    return response.blob()
  }, {signal, timeoutMs: 20000})
}
