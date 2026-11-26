import {useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent} from 'react'
import {createPortal} from 'react-dom'
import {ShieldCheck, TriangleAlert, X} from 'lucide-react'
import './studio-hold-confirm.css'
import {tx} from './studio-quick-trade'

/** Basılı tutma: dokunma, fare ve klavye (Boşluk/Enter) ile aynı davranır; bırakınca ya da imleç çıkınca sıfırlanır. */
export function useHold(ms: number, onDone: () => void, disabled: boolean) {
  const [holding, setHolding] = useState(false)
  const timer = useRef<number | null>(null)
  const startedAt = useRef(0)
  const done = useRef(onDone)
  done.current = onDone
  const finish = useCallback(() => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
    setHolding(false); done.current()
  }, [])
  const cancel = useCallback(() => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
    setHolding(false)
  }, [])
  // Bırakma: süre gerçekten dolduysa (sayfa meşgulken zamanlayıcı geç kalsa bile) işlem tamamlanır; dolmadıysa vazgeçilir.
  const release = useCallback(() => {
    if (timer.current !== null && performance.now() - startedAt.current >= ms) finish()
    else cancel()
  }, [cancel, finish, ms])
  const start = useCallback(() => {
    if (disabled || timer.current !== null) return
    setHolding(true)
    startedAt.current = performance.now()
    timer.current = window.setTimeout(finish, ms)
  }, [disabled, ms, finish])
  useEffect(() => cancel, [cancel])
  useEffect(() => { if (disabled) cancel() }, [disabled, cancel])
  const bind = {
    // İşaretçi yakalanır: basılı tutarken yazı/yerleşim değişse ya da fiyat güncellense de tuş "terk edildi" sayılmaz.
    onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return
      try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* desteklenmiyorsa sorun değil */ }
      start()
    },
    onPointerMove: (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (timer.current === null) return
      const box = event.currentTarget.getBoundingClientRect()
      const away = event.clientX < box.left - 16 || event.clientX > box.right + 16 || event.clientY < box.top - 16 || event.clientY > box.bottom + 16
      if (away) cancel() // parmağı tuştan uzağa kaydırmak vazgeçmektir
    },
    onPointerUp: release, onLostPointerCapture: release, onPointerCancel: cancel, onBlur: cancel,
    onContextMenu: (event: {preventDefault: () => void}) => event.preventDefault(),
    onKeyDown: (event: {key: string; repeat: boolean; preventDefault: () => void}) => { if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) { event.preventDefault(); start() } },
    onKeyUp: (event: {key: string}) => { if (event.key === ' ' || event.key === 'Enter') release() },
  }
  return {holding, bind}
}

/** Bilgisayar (fare + hover) mı? Dokunmatikte basılı tutma kalır; bilgisayarda tek tık + özet/onay penceresi kullanılır. */
export function useDesktopConfirm(): boolean {
  const query = '(hover: hover) and (pointer: fine)'
  const [fine, setFine] = useState(() => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(query)
    const on = () => setFine(media.matches)
    on(); media.addEventListener('change', on)
    return () => media.removeEventListener('change', on)
  }, [])
  return fine
}

export type ConfirmRow = {label: string; value: string; tone?: 'up' | 'down' | 'warn'}
export type ConfirmInfo = {title: string; sub: string; rows: ConfirmRow[]; note?: string; warn?: boolean; demo?: boolean; confirmLabel: string}

/** "Emin misin?" penceresi: işlemin özetini gösterir; Onayla / Vazgeç. Esc = vazgeç, Enter = onayla. */
export function ConfirmDialog({info, tone, anchor, onConfirm, onCancel}: {info: ConfirmInfo; tone: 'buy' | 'sell' | 'danger'; anchor: HTMLElement | null; onConfirm: () => void; onCancel: () => void}) {
  const okRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    okRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); onCancel() } }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [onCancel])
  const host = (anchor?.closest('.masterReferenceAnalysis') as HTMLElement | null) ?? document.body
  return createPortal(
    <div className="sqcOverlay" data-tone={tone} onMouseDown={event => { if (event.target === event.currentTarget) onCancel() }}>
      <div className="sqcCard" role="alertdialog" aria-modal="true" aria-label={info.title}>
        <header>
          <span className="sqcIcon" aria-hidden="true">{info.warn ? <TriangleAlert/> : <ShieldCheck/>}</span>
          <div><h3>{info.title}</h3><p>{info.sub}</p></div>
          {info.demo !== undefined && <em data-demo={info.demo || undefined}>{info.demo ? 'DEMO' : tx('CANLI', 'LIVE')}</em>}
          <button type="button" className="sqcX" aria-label={tx('Vazgeç', 'Cancel')} onClick={onCancel}><X aria-hidden="true"/></button>
        </header>
        <dl>{info.rows.map(row => <div key={row.label} data-tone={row.tone}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
        {info.note && <p className="sqcNote" data-warn={info.warn || undefined}>{info.note}</p>}
        <footer>
          <button type="button" className="sqcCancel" onClick={onCancel}>{tx('Vazgeç', 'Cancel')}</button>
          <button type="button" ref={okRef} className="sqcOk" data-tone={tone} onClick={onConfirm}>{info.confirmLabel}</button>
        </footer>
      </div>
    </div>, host)
}

export function HoldButton({ms, label, tone, disabled, onDone, hint, confirm, clickLabel}: {ms: number; label: string; tone: 'buy' | 'sell' | 'danger'; disabled: boolean; onDone: () => void; hint: string; confirm?: ConfirmInfo; clickLabel?: string}) {
  const {holding, bind} = useHold(ms, onDone, disabled)
  const desktop = useDesktopConfirm()
  const [asking, setAsking] = useState(false)
  const ref = useRef<HTMLButtonElement>(null)
  useEffect(() => { if (disabled) setAsking(false) }, [disabled])
  if (confirm && desktop) {
    return <>
      <button type="button" ref={ref} className="sqtHold sqtHoldClick" data-tone={tone} disabled={disabled} aria-label={clickLabel ?? label} aria-haspopup="dialog" onClick={() => setAsking(true)}>
        <span className="sqtHoldLabel">{clickLabel ?? label}</span>
      </button>
      {asking && <ConfirmDialog info={confirm} tone={tone} anchor={ref.current} onCancel={() => setAsking(false)} onConfirm={() => { setAsking(false); onDone() }}/>}
    </>
  }
  return <button type="button" className="sqtHold" data-tone={tone} data-holding={holding} disabled={disabled} aria-label={hint} style={{'--sqt-hold': `${ms}ms`} as CSSProperties} {...bind}>
    <span className="sqtHoldFill" aria-hidden="true"/><svg className="sqtHoldRing" viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15" pathLength="100"/></svg><span className="sqtHoldLabel">{holding ? tx('Bırakma…', 'Keep holding…') : label}</span>
  </button>
}
