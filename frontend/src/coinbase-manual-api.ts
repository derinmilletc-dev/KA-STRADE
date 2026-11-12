// Coinbase rotalarına istek: her hata ApiFailure olarak fırlatılır (sessiz hata yok).
import {fetchWithTimeout} from '../../master-trade-request'
import {API_BASE} from './api'
import {mutatingPath, networkFailure, parseFailure, type ApiFailure} from './coinbase-manual'

export class CoinbaseApiError extends Error {
  failure: ApiFailure
  constructor(failure: ApiFailure) {
    super(failure.message)
    this.name = 'CoinbaseApiError'
    this.failure = failure
  }
}

const READ_TIMEOUT_MS = 20000
const ORDER_TIMEOUT_MS = 60000 // emir akışı birkaç doğrulama turu yapar

export async function coinbaseCall<T>(path: string, options: RequestInit = {}, secrets: readonly string[] = []): Promise<T> {
  const mutating = mutatingPath(path) || (options.method !== undefined && options.method !== 'GET')
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  let response: Response
  try {
    response = await fetchWithTimeout(`${API_BASE}/coinbase${path}`, {...options, headers}, mutating ? ORDER_TIMEOUT_MS : READ_TIMEOUT_MS)
  } catch (error) {
    throw new CoinbaseApiError(networkFailure(error, mutating))
  }
  let body: unknown = null
  try { body = await response.json() } catch { body = null }
  if (!response.ok) {
    const failure = parseFailure(response.status, body, secrets)
    const retry = Number(response.headers.get('Retry-After'))
    if (failure.retryAfterSeconds === null && Number.isFinite(retry) && retry > 0) failure.retryAfterSeconds = Math.ceil(retry)
    throw new CoinbaseApiError(failure)
  }
  if (body === null) {
    throw new CoinbaseApiError(parseFailure(502, null))
  }
  return body as T
}

export const postJson = <T,>(path: string, payload: unknown, secrets: readonly string[] = []) =>
  coinbaseCall<T>(path, {method: 'POST', body: JSON.stringify(payload)}, secrets)
