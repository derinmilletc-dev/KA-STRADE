import {useEffect, useState} from 'react'
import {API_BASE} from './api'
import {fetchPublicMarketData} from './master-trade-request'
import {parseKlines, type Kline} from './studio-klines'
import {REPLAY_SPEEDS, canReplay, type ReplayState} from './studio-replay'

// İkinci grafik (yan yana) ve bar replay kontrolleri. Yalnız okunur piyasa verisi; emir yok.
const SAFE = /^[A-Z0-9]{2,30}$/
export function useKlines(symbol: string, interval: string, enabled: boolean, limit = 160, refreshMs = 30000) {
  const [state, setState] = useState<{key: string; candles: Kline[]; error: string}>({key: '', candles: [], error: ''})
  const key = `${symbol}|${interval}|${limit}`
  useEffect(() => {
    if (!enabled || !SAFE.test(symbol)) return
    const controller = new AbortController()
    let timer: number | undefined
    const load = async () => {
      try {
        const response = await fetchPublicMarketData(`${API_BASE}/klines/${symbol}?interval=${encodeURIComponent(interval)}&limit=${limit}`, {signal: controller.signal}, 20000)
        if (!response.ok) throw new Error('Veri alınamadı')
        const candles = parseKlines(await response.json())
        if (!candles) throw new Error('Geçersiz veri')
        if (!controller.signal.aborted) setState({key, candles, error: ''})
      } catch {
        if (!controller.signal.aborted) setState(current => ({key, candles: current.key === key ? current.candles : [], error: 'İkinci grafik verisi alınamadı.'}))
      }
    }
    void load()
    if (refreshMs > 0) timer = window.setInterval(() => void load(), refreshMs)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [symbol, interval, enabled, key, limit, refreshMs])
  return state.key === key ? state : {key, candles: [] as Kline[], error: ''}
}

export function SplitPicker({on, interval, intervals, onToggle, onInterval}: {on: boolean; interval: string; intervals: readonly string[]; onToggle: () => void; onInterval: (value: string) => void}) {
  return <div className="refSplitPicker" role="group" aria-label="İkinci grafik">
    <small>YAN YANA GRAFİK</small>
    <div>
      <button type="button" aria-pressed={on} onClick={onToggle}>{on ? 'İkinci grafik açık' : 'İkinci grafiği aç'}</button>
      <select aria-label="İkinci grafik zaman dilimi" value={interval} onChange={event => onInterval(event.target.value)}>{intervals.map(item => <option key={item}>{item}</option>)}</select>
    </div>
  </div>
}

export function ReplayBar({state, length, onPlay, onStep, onSeek, onSpeed, onExit}: {state: ReplayState; length: number; onPlay: () => void; onStep: (delta: number) => void; onSeek: (index: number) => void; onSpeed: (speed: number) => void; onExit: () => void}) {
  return <div className="refReplayBar" role="group" aria-label="Bar replay">
    <button type="button" aria-label="Bir mum geri" onClick={() => onStep(-1)}>◀</button>
    <button type="button" className="refReplayPlay" aria-label={state.playing ? 'Duraklat' : 'Oynat'} aria-pressed={state.playing} onClick={onPlay}>{state.playing ? '❚❚' : '▶'}</button>
    <button type="button" aria-label="Bir mum ileri" onClick={() => onStep(1)}>▶|</button>
    <input type="range" aria-label="Replay konumu" min={0} max={Math.max(0, length - 1)} value={state.index} onChange={event => onSeek(Number(event.target.value))}/>
    <select aria-label="Replay hızı" value={state.speed} onChange={event => onSpeed(Number(event.target.value))}>{REPLAY_SPEEDS.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select>
    <button type="button" className="refReplayExit" onClick={onExit}>Çık</button>
  </div>
}

export function ReplayToggle({length, active, onToggle}: {length: number; active: boolean; onToggle: () => void}) {
  return <div className="refReplayToggle"><button type="button" aria-pressed={active} disabled={!canReplay(length)} onClick={onToggle}>{active ? 'Bar replay açık' : 'Bar replay'}</button><small>Geçmiş mumları tek tek açıp nasıl ilerlediğini izle.</small></div>
}
