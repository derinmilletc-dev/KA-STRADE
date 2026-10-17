import {API_BASE} from './api'
import type {DecisionAnalysis, MtfAnalysis} from './masterTradeDecision'
import {fetchPublicMarketData} from './master-trade-request'
import {MASTER_MTF_INTERVALS} from './master-trade-presentation'
import {MarketDataFailure, marketDataFailure, marketDataResponseError} from './master-trade-data-error'

// Bir zaman dilimi geçici olarak (zaman aşımı, sunucu hatası) gelmezse: bir kez daha denenir;
// yine gelmezse o zaman diliminin son iyi sonucu 2 dakikaya kadar kullanılır. Kalıcı hatalar (yetersiz veri vb.) denenmez.
export const MTF_STALE_MS = 120_000
const lastGood = new Map<string, {analysis: DecisionAnalysis; at: number}>()
const transient = (error: unknown) => {
  const kind = marketDataFailure(error).kind
  return kind === 'backend' || kind === 'frontend'
}
const pause = (ms: number, signal?: AbortSignal) => new Promise<void>(resolve => {
  const timer = setTimeout(resolve, ms)
  signal?.addEventListener('abort', () => { clearTimeout(timer); resolve() }, {once: true})
})
const loadOnce = async (symbol: string, timeframe: string, signal: AbortSignal | undefined, timeoutMs: number): Promise<DecisionAnalysis> => {
  const response = await fetchPublicMarketData(`${API_BASE}/analysis/${symbol}?interval=${timeframe}`, {signal}, timeoutMs)
  if (!response.ok) throw await marketDataResponseError(response)
  const analysis = await response.json() as DecisionAnalysis
  if (!analysis || typeof analysis.direction !== 'string' || !Number.isFinite(analysis.confidence)) throw new MarketDataFailure('backend', 'Backend geçerli analiz verisi döndürmedi.')
  return analysis
}

export async function fetchMtfAnalyses(symbol: string, signal?: AbortSignal) {
  const results = await Promise.all(MASTER_MTF_INTERVALS.map(async timeframe => {
    const key = `${symbol}|${timeframe}`
    try {
      let analysis: DecisionAnalysis
      try {
        analysis = await loadOnce(symbol, timeframe, signal, 15000)
      } catch (first) {
        if (signal?.aborted || !transient(first)) throw first
        await pause(1000, signal)
        analysis = await loadOnce(symbol, timeframe, signal, 10000)
      }
      lastGood.set(key, {analysis, at: Date.now()})
      if (lastGood.size > 200) lastGood.delete(lastGood.keys().next().value as string)
      return {analysis: {...analysis, timeframe} as MtfAnalysis, error: ''}
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError' && signal?.aborted) throw error
      const kept = lastGood.get(key)
      if (kept && transient(error) && Date.now() - kept.at < MTF_STALE_MS) return {analysis: {...kept.analysis, timeframe} as MtfAnalysis, error: ''}
      return {analysis: null, error: `MTF ${timeframe}: ${marketDataFailure(error).message}`}
    }
  }))
  return {analyses: results.map(result => result.analysis).filter((item): item is MtfAnalysis => item !== null), error: results.map(result => result.error).filter(Boolean).join(' · ')}
}
