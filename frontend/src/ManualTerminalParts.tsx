// Manuel emir sihirbazının sunum bileşenleri. Yalnız görünüm: ağ, emir ve hesap mantığı yoktur.
// Tüm değerler üst bileşenden gelir; gelmeyen değer uydurulmaz ("Henüz hesaplanmadı" gösterilir).
import type {ReactNode} from 'react'
import {TrendingDown, TrendingUp} from 'lucide-react'
import type {CheckRow, PriceMapResult} from './manual-order-state'

export const PLACEHOLDER = 'Henüz hesaplanmadı'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'mute'

export function StatusPill({tone, children}: {tone: StatusTone; children: ReactNode}) {
  return <span className="mxtPill" data-tone={tone}><i aria-hidden="true"/>{children}</span>
}

export type SummaryItem = {label: string; value: string | null; tone?: 'up' | 'down' | 'warn'; big?: boolean}

export function SummaryList({rows}: {rows: SummaryItem[]}) {
  return <dl className="mxtSumList">{rows.map(row => <div key={row.label}>
    <dt>{row.label}</dt>
    {row.value === null
      ? <dd data-empty="1">{PLACEHOLDER}</dd>
      : <dd data-tone={row.tone} data-big={row.big ? '1' : undefined}>{row.value}</dd>}
  </div>)}</dl>
}

const GLYPH: Record<CheckRow['state'], string> = {ok: '✓', bad: '!', pending: '…'}

export function CheckList({rows}: {rows: CheckRow[]}) {
  return <ul className="mxtChecks" aria-label="Son kontrol listesi">{rows.map(row => <li key={row.key} data-state={row.state}>
    <i aria-hidden="true">{GLYPH[row.state]}</i>
    <span><b>{row.label}</b><small>{row.detail}</small></span>
  </li>)}</ul>
}

const MAP_LABEL: Record<string, string> = {stop: 'Stop', entry: 'Giriş', tp1: 'TP1', tp2: 'TP2', tp3: 'TP3'}

export function PriceMapView({map, format}: {map: PriceMapResult; format: (value: number) => string}) {
  if (!map.valid) return <p className="mxtMapEmpty">{map.reason}</p>
  return <div className="mxtMap" role="img" aria-label={map.points.map(point => `${MAP_LABEL[point.key]} ${format(point.price)}`).join(', ')}>
    <div className="mxtMapTrack"/>
    {map.points.map((point, index) => <div key={point.key} className="mxtMapPt" data-key={point.key} data-row={index % 2} style={{left: `${point.pos}%`}}>
      <i/><span>{MAP_LABEL[point.key]}<b>{format(point.price)}</b></span>
    </div>)}
  </div>
}

export function DirectionCards({value, onChange}: {value: 'LONG' | 'SHORT'; onChange: (side: 'LONG' | 'SHORT') => void}) {
  return <div className="mxtDir" role="group" aria-label="İşlem yönü">
    <button type="button" data-side="LONG" aria-pressed={value === 'LONG'} onClick={() => onChange('LONG')}>
      <TrendingUp aria-hidden="true"/><strong>LONG</strong><span>Fiyat yükselirse kazanırsın.</span>
    </button>
    <button type="button" data-side="SHORT" aria-pressed={value === 'SHORT'} onClick={() => onChange('SHORT')}>
      <TrendingDown aria-hidden="true"/><strong>SHORT</strong><span>Fiyat düşerse kazanırsın.</span>
    </button>
  </div>
}
