import {useEffect, useMemo, useState} from 'react'
import {API_BASE} from './api'
import {fetchWithTimeout} from '../../master-trade-request'
import {smartGridPlan, recentMove, type SmartCandle, type SmartPlan} from './gridDecision'

// "Akıllı plan": coinin son ~24 saatlik hareketine göre aralık + kademe önerir, limitlere sığdırır. Yalnız öneri; uygulamak kullanıcının tıklamasıdır.
// Mum verisi arka plandaki /klines yolundan gelir (yalnız okuma).

type Props = {
  symbol: string; side: 'LONG' | 'SHORT'; price: number; margin: number; leverage: number; active: boolean
  evaluate: (p: {lower: number; upper: number; count: number; margin: number; stop: number}) => {allOk: boolean}
  onApply: (plan: SmartPlan) => void
}

const REFRESH_MS = 120_000

export default function GridSmartPlan({symbol, side, price, margin, leverage, active, evaluate, onApply}: Props) {
  const [candles, setCandles] = useState<{symbol: string; rows: SmartCandle[]} | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!active || !symbol) { setCandles(null); setFailed(false); return }
    let cancelled = false
    const controller = new AbortController()
    const load = async () => {
      try {
        const response = await fetchWithTimeout(`${API_BASE}/klines/${encodeURIComponent(symbol)}?interval=1h&limit=30`, {cache: 'no-store', signal: controller.signal})
        if (!response.ok) throw new Error('klines')
        const body = await response.json()
        const rows = (Array.isArray(body) ? body : []).map((row: Record<string, unknown>) => ({high: Number(row.high), low: Number(row.low), close: Number(row.close)})).filter(c => [c.high, c.low, c.close].every(Number.isFinite))
        if (!cancelled) { setCandles({symbol, rows}); setFailed(false) }
      } catch (error) {
        if (!cancelled && (error as {name?: string})?.name !== 'AbortError') setFailed(true)
      }
    }
    setCandles(null); setFailed(false)
    void load()
    const timer = window.setInterval(() => { void load() }, REFRESH_MS)
    return () => { cancelled = true; controller.abort(); window.clearInterval(timer) }
  }, [symbol, active])

  const plan = useMemo(() => candles && candles.symbol === symbol && price > 0 ? smartGridPlan({side, price, candles: candles.rows, margin, leverage}, evaluate) : null,
    // evaluate her çizimde yeni fonksiyon; girdileri izlemek yeterli
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [candles, symbol, side, price, margin, leverage])
  const move = candles ? recentMove(candles.rows, price) : null

  if (!symbol) return null
  if (!(price > 0)) return null
  return <div className="gsuSmart" data-state={plan ? (plan.ok ? 'ok' : 'no') : failed ? 'no' : 'load'}>
    <div className="gsuSmartHead"><span aria-hidden="true">✦</span><b>Akıllı plan</b>{move && <small>Son 24 saat hareketi %{move.dayPct.toFixed(1)}</small>}</div>
    {!plan && !failed && <p>Piyasa okunuyor…</p>}
    {!plan && failed && <p>Piyasa verisi okunamadı; hazır planlardan birini seç.</p>}
    {plan && plan.ok && <>
      <p>Aralık <b>{plan.lower} – {plan.upper}</b> · <b>{plan.count}</b> kademe · <b>{plan.margin}</b> USDT marjin · stop <b>{plan.stop}</b>{plan.fitted ? ' (limitlere sığdırıldı)' : ''}</p>
      <button type="button" onClick={() => onApply(plan)}>Planı uygula</button>
    </>}
    {plan && !plan.ok && <p>Bu marjinle limitlere sığan plan bulunamadı. Marjini düşür ya da açık pozisyonları kontrol et.</p>}
  </div>
}
