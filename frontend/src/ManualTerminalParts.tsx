// Manuel emir sihirbazının sunum bileşenleri. Yalnız görünüm: ağ, emir ve hesap mantığı yoktur.
// Tüm değerler üst bileşenden gelir; gelmeyen değer uydurulmaz ("Henüz hesaplanmadı" gösterilir).
import {useEffect, useRef, useState} from 'react'
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

/** Bekleme sayacı: işlem sürerken "12 sn" gibi geçen süreyi gösterir ki ekran donmuş sanılmasın. */
export function ElapsedSeconds() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return <span className="mxtElapsed">{seconds} sn</span>
}

/** Kaydırarak onay: tutamacı sona sürükleyince onConfirm çağrılır (yanlışlıkla dokunmaya karşı). Klavye: Enter/Boşluk da onaylar.
 *  onConfirm Promise<boolean> dönerse: bekleme sırasında dönen simge, başarıda animasyonlu tik, başarısızlıkta geri dönüş gösterilir. */
// 'mxtSlideBackdrop' sınıf adı bilinçlidir: Canlı İşlem sekmesindeki genel 'arka planı sil' kuralı adında backdrop geçen öğelere dokunmaz.
export function SlideToConfirm({label, hint, doneLabel = 'Tamam', onConfirm, disabled}: {label: string; hint?: string; doneLabel?: string; onConfirm: () => Promise<boolean | void> | boolean | void; disabled?: boolean}) {
  const track = useRef<HTMLDivElement | null>(null)
  const drag = useRef<{startX: number; max: number} | null>(null)
  const pending = useRef<number | null>(null)
  const frame = useRef<number | null>(null)
  const [offset, setOffset] = useState(0)
  useEffect(() => () => { if (frame.current !== null) window.cancelAnimationFrame(frame.current) }, [])
  const [dragging, setDragging] = useState(false)
  const [nudge, setNudge] = useState(false)
  const [stage, setStage] = useState<'idle' | 'working' | 'done'>('idle')
  const HANDLE = 52
  const maxOffset = () => Math.max(1, (track.current?.clientWidth ?? 0) - HANDLE - 12)
  const reset = () => { setDragging(false); setOffset(0); drag.current = null }
  const fire = async () => {
    setStage('working')
    setOffset(maxOffset())
    let ok: boolean | void = false
    try { ok = await onConfirm() } catch { ok = false }
    if (ok === false) { setStage('idle'); setOffset(0); setNudge(true); window.setTimeout(() => setNudge(false), 500); return }
    setStage('done')
  }
  const finish = () => {
    const state = drag.current
    if (!state) return
    const latest = pending.current ?? offset
    pending.current = null
    const reached = latest >= state.max * 0.85
    drag.current = null; setDragging(false)
    if (reached) void fire()
    else { setOffset(0); setNudge(true); window.setTimeout(() => setNudge(false), 500) }
  }
  const locked = disabled || stage !== 'idle'
  return <div className="mxtSlide mxtSlideBackdrop" data-stage={stage} data-disabled={disabled && stage === 'idle' ? 'yes' : undefined} data-nudge={nudge || undefined} ref={track}
    style={{'--slide-progress': stage === 'idle' ? `${Math.round(offset / maxOffset() * 100)}%` : '100%'} as React.CSSProperties} aria-live="polite">
    <span className="mxtSlideLabel"><b>{stage === 'done' ? doneLabel : stage === 'working' ? 'Onaylanıyor…' : label}</b>{hint && stage === 'idle' && <small>{hint}</small>}</span>
    <button type="button" className="mxtSlideHandle" data-dragging={dragging || undefined} disabled={locked}
      style={{transform: `translateX(${offset}px)`}} aria-label={`${label}. Kaydırarak onayla veya Enter'a bas`}
      onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (!locked) void fire() } }}
      onPointerDown={event => { if (locked) return; event.currentTarget.setPointerCapture(event.pointerId); drag.current = {startX: event.clientX, max: maxOffset()}; setDragging(true) }}
      onPointerMove={event => {
        const state = drag.current
        if (!state) return
        // Her harekette değil, ekran karesinde bir kez güncelle (telefonda takılmayı önler).
        pending.current = Math.min(state.max, Math.max(0, event.clientX - state.startX))
        if (frame.current === null) frame.current = window.requestAnimationFrame(() => { frame.current = null; if (pending.current !== null) setOffset(pending.current) })
      }}
      onPointerUp={finish} onPointerCancel={() => { reset() }}>
      {stage === 'done'
        ? <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path className="mxtSlideTick" d="M5 12.5l4.6 4.6L19 7.5"/></svg>
        : stage === 'working' ? <span className="mxtSlideSpin" aria-hidden="true"/> : <span aria-hidden="true">→</span>}
    </button>
  </div>
}

/** Yerel saatle bir sonraki UTC gece yarısı (günlük kayıp sayacı UTC gününe göre sıfırlanır). */
function nextUtcMidnight(now = new Date()): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1))
}

/** Günlük zarar limiti kartı: %90'da sarı uyarı, dolunca kırmızı kilit. Manuel emir, otomatik işlem ve canlı kokpitte ortak.
 *  Sınıf adındaki "Popup", Canlı İşlem sekmesindeki genel "arka planı sil" kuralından muaf tutar. */
export function DayLimitCard({used, limit, locked = false, suggested, onSuggest, onEdit, note}: {used: number; limit: number; locked?: boolean; suggested?: number | null; onSuggest?: () => void; onEdit?: () => void; note?: string}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 30_000); return () => window.clearInterval(timer) }, [])
  if (!(limit > 0)) return null
  const pct = used / limit * 100
  if (pct < 90 && !locked) return null
  const full = locked || pct >= 100
  const reset = nextUtcMidnight(new Date(now))
  const leftMin = Math.max(0, Math.round((reset.getTime() - now) / 60_000))
  const resetText = `${reset.toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'})} · ${Math.floor(leftMin / 60)} sa ${leftMin % 60} dk sonra`
  const fmt = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})
  return <section className="ktDayPopup" data-full={full || undefined} role={full ? 'alert' : 'status'}>
    <header>
      <span className="ktDayIcon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>{full ? <path d="M9.5 12h5"/> : <path d="M12 8.5v4M12 15.5h.01"/>}</svg></span>
      <div><small>GÜNLÜK ZARAR LİMİTİ</small><b>{full ? 'Bugünlük limit doldu' : 'Limit dolmak üzere'}</b></div>
      <em>%{Math.min(999, Math.floor(pct))}</em>
    </header>
    <div className="ktDayMeter" aria-hidden="true"><i style={{width: `${Math.min(100, pct)}%`}}/></div>
    <dl>
      <div><dt>Zarar</dt><dd>{fmt(used)}</dd></div>
      <div><dt>Limit</dt><dd>{fmt(limit)}</dd></div>
      <div><dt>Kalan</dt><dd>{fmt(Math.max(0, limit - used))}</dd></div>
    </dl>
    <p>{full ? 'Yeni işlem açılmaz. Açık pozisyonların stop ve hedefleri çalışmaya devam eder.' : 'Bir sonraki zararlı işlem limiti doldurabilir.'} Tutarlar USDT.{note ? ` ${note}` : ''}</p>
    <div className="ktDayReset"><span aria-hidden="true">↻</span>Sayaç sıfırlanır: <b>{resetText}</b></div>
    {(onSuggest || onEdit) && <div className="ktDayBtns">
      {onSuggest && suggested && suggested > limit && <button type="button" onClick={onSuggest}>Limiti {suggested} USDT yap</button>}
      {onEdit && <button type="button" data-k="alt" onClick={onEdit}>Düzenle</button>}
    </div>}
  </section>
}
