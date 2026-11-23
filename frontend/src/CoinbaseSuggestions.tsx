// Coinbase stop/hedef önerileri: her öneri sunucuda gerçek ön kontrolden geçirilerek gelir.
// Geçen öneri seçilince emir ön kontrolden de geçer; geçmeyen öneri pasif görünür ve NEDEN geçmediği yazılır.
import {useCallback, useEffect, useRef, useState} from 'react'
import {coinbaseCall, CoinbaseApiError} from './coinbase-manual-api'
import type {Ticket} from './coinbase-manual'
import {blockedAdvice, itemReward, itemSelected, itemSub, reasonShort, suggestPayload, type SuggestItem, type SuggestResult} from './coinbase-suggest'

type Load = {status: 'idle' | 'loading' | 'ready' | 'error'; result: SuggestResult | null; forKey: string; error: string}
export type Suggestions = {status: Load['status']; result: SuggestResult | null; error: string; ready: boolean; reload: () => void}

const REFRESH_MS = 20_000
const DEBOUNCE_MS = 450

/** Bilete göre önerileri yükler (debounce + 20 sn'de bir sessiz yenileme). `rev` limit değişince yeniden yüklemek içindir. */
export function useCoinbaseSuggestions(ticket: Ticket, enabled: boolean, rev = 0): Suggestions {
  const payload = enabled ? suggestPayload(ticket) : null
  const key = payload ? `${rev}|${JSON.stringify(payload)}` : ''
  const [load, setLoad] = useState<Load>({status: 'idle', result: null, forKey: '', error: ''})
  const seq = useRef(0)
  const run = useCallback(async (quiet: boolean) => {
    if (!key) return
    const id = ++seq.current
    if (!quiet) setLoad(current => ({...current, status: 'loading', error: ''}))
    try {
      const result = await coinbaseCall<SuggestResult>('/orders/suggest', {method: 'POST', body: key.slice(key.indexOf('|') + 1)})
      if (id === seq.current) setLoad({status: 'ready', result, forKey: key, error: ''})
    } catch (error) {
      if (id !== seq.current) return
      const message = error instanceof CoinbaseApiError ? error.failure.message : 'Öneriler alınamadı.'
      setLoad(current => ({...current, status: 'error', error: message}))
    }
  }, [key])
  useEffect(() => {
    if (!key) { seq.current += 1; setLoad({status: 'idle', result: null, forKey: '', error: ''}); return }
    const first = window.setTimeout(() => void run(false), DEBOUNCE_MS)
    const timer = window.setInterval(() => { if (!document.hidden) void run(true) }, REFRESH_MS)
    return () => { window.clearTimeout(first); window.clearInterval(timer) }
  }, [key, run])
  // Başka bilet için gelmiş eski sonuç gösterilmez (yanlış fiyatlı öneri olmasın).
  const fresh = load.result !== null && load.forKey === key
  return {status: key === '' ? 'idle' : fresh ? (load.status === 'error' ? 'error' : 'ready') : load.status === 'error' ? 'error' : 'loading',
    result: fresh ? load.result : null, error: load.error, ready: key !== '', reload: () => void run(false)}
}

type ButtonProps = {suggestions: Suggestions; ticket: Pick<Ticket, 'stopPrice' | 'takeProfit'>; onApply: (item: SuggestItem) => void; variant: 'card' | 'chip'}

/** Öneri düğmeleri. 'card': telefon seçici (mxtMPk), 'chip': masaüstü satırı. */
export function SuggestionButtons({suggestions, ticket, onApply, variant}: ButtonProps) {
  const {result, status} = suggestions
  if (!suggestions.ready) {
    return variant === 'card'
      ? <button type="button" className="mxtMPk cbSugEmpty" disabled><b>Stop önerileri</b><small>Önce teminatı gir</small></button>
      : <span className="cbSugHint">Önce teminatı gir</span>
  }
  if (!result) {
    if (status === 'error') {
      return variant === 'card'
        ? <button type="button" className="mxtMPk cbSugEmpty" onClick={suggestions.reload}><b>Yeniden dene</b><small>Öneriler alınamadı</small></button>
        : <button type="button" onClick={suggestions.reload}>Yeniden dene</button>
    }
    return <>{[1, 2, 3, 5].map(pct => variant === 'card'
      ? <button type="button" key={pct} className="mxtMPk cbSugSkel" disabled aria-label={`Stop %${pct} hesaplanıyor`}><b>Stop %{pct}</b><small>Sınanıyor…</small></button>
      : <button type="button" key={pct} className="cbSugSkel" disabled>%{pct}</button>)}</>
  }
  return <>{result.items.map(item => {
    const selected = itemSelected(item, ticket)
    const recommended = result.recommended_stop_pct === item.stop_pct
    const reward = itemReward(item)
    return variant === 'card'
      ? <button type="button" key={item.stop_pct} className="mxtMPk cbSug" data-on={selected || undefined} data-rec={recommended ? '1' : undefined} data-bad={!item.ok || undefined}
        aria-pressed={selected} disabled={!item.ok} title={item.ok ? undefined : item.reasons.join(' · ')} data-testid={`cb-sug-${item.stop_pct}`} onClick={() => onApply(item)}>
        <b>Stop %{item.stop_pct}{recommended && <em>Önerilen</em>}</b><small>{itemSub(item)}</small>{reward && <small className="cbSugGain">{reward}</small>}</button>
      : <button type="button" key={item.stop_pct} className="cbSug" data-bad={!item.ok || undefined} aria-pressed={selected} disabled={!item.ok}
        title={item.ok ? `${itemSub(item)}${reward ? ` · ${reward}` : ''}` : item.reasons.join(' · ')} onClick={() => onApply(item)}>%{item.stop_pct}{item.ok ? '' : ' ✕'}</button>
  })}</>
}

/** Önerilerin altındaki durum notu: sınanıyor / hata / hiçbiri geçmiyor (ne yapmalı) / hangileri neden pasif. */
export function SuggestionNote({suggestions}: {suggestions: Suggestions}) {
  const {result, status, error} = suggestions
  if (!suggestions.ready) return null
  if (!result) {
    return status === 'error'
      ? <p className="mxtHint cbSugNote" role="alert" data-tone="warn">{error || 'Öneriler alınamadı.'} <button type="button" className="cbSugRetry" onClick={suggestions.reload}>Yeniden dene</button></p>
      : <p className="mxtHint cbSugNote" role="status" data-tone="info">Öneriler Coinbase kurallarına göre sınanıyor…</p>
  }
  const advice = blockedAdvice(result)
  if (advice) return <p className="mxtHint cbSugNote" role="alert" data-tone="warn">{advice}</p>
  const failing = result.items.filter(item => !item.ok)
  return <p className="mxtHint cbSugNote" role="status" data-tone="ok">
    ✓ Açık öneriler ön kontrolden geçti; sözleşme sayısı teminatına ve limitlerine göre hesaplandı.
    {failing.length > 0 && <span className="cbSugWhy"> Pasif olanlar: {failing.map(item => `%${item.stop_pct} (${reasonShort(item.reasons[0] ?? '')})`).join(', ')}.</span>}
  </p>
}
