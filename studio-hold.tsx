import {useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent} from 'react'
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

export function HoldButton({ms, label, tone, disabled, onDone, hint}: {ms: number; label: string; tone: 'buy' | 'sell' | 'danger'; disabled: boolean; onDone: () => void; hint: string}) {
  const {holding, bind} = useHold(ms, onDone, disabled)
  return <button type="button" className="sqtHold" data-tone={tone} data-holding={holding} disabled={disabled} aria-label={hint} style={{'--sqt-hold': `${ms}ms`} as CSSProperties} {...bind}>
    <span className="sqtHoldFill" aria-hidden="true"/><span className="sqtHoldLabel">{holding ? tx('Bırakma…', 'Keep holding…') : label}</span>
  </button>
}
