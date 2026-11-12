import {AlertTriangle, CheckCircle2} from 'lucide-react'
import {CoinbaseApiError} from './coinbase-manual-api'
import type {ApiFailure} from './coinbase-manual'

export type Notice = {kind: 'ok' | 'warn' | 'error'; text: string; failure?: ApiFailure}

/** Hata ne olursa olsun açık bir bildirime çevrilir (sessiz hata yok). */
export function noticeFromError(error: unknown): Notice {
  if (error instanceof CoinbaseApiError) return {kind: 'error', text: error.failure.message, failure: error.failure}
  return {kind: 'error', text: error instanceof Error ? error.message : 'Beklenmeyen hata.'}
}

export default function CoinbaseManualNotice({notice}: {notice: Notice}) {
  return <div className={`cbNotice ${notice.kind}${notice.failure?.critical ? ' critical' : ''}`} role={notice.kind === 'error' ? 'alert' : 'status'}>
    {notice.kind === 'ok' ? <CheckCircle2/> : <AlertTriangle/>}
    <div>
      <b>{notice.failure?.critical ? 'ACİL: ' : ''}{notice.text}</b>
      {notice.failure?.reasons.length ? <ul>{notice.failure.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : null}
      {notice.failure?.retryAfterSeconds ? <small>Yaklaşık {notice.failure.retryAfterSeconds} sn sonra tekrar dene.</small> : null}
      {notice.failure?.code ? <small>Kod: {notice.failure.code}</small> : null}
    </div>
  </div>
}
