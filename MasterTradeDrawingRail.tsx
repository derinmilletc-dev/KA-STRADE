import {useEffect, useRef, useState, type ReactNode} from 'react'
import {Bell, ChevronLeft, ChevronRight, Crosshair, MoreHorizontal, Eraser, Eye, EyeOff, Minus, Redo2, Trash2, TrendingUp, Undo2} from 'lucide-react'
import type {DrawMode} from './chart-drawings'

type Props = {
  drawMode: DrawMode
  onPick: (mode: Exclude<DrawMode, 'none'>) => void
  crosshair: boolean
  onCrosshair: () => void
  hasDrawings: boolean
  hidden: boolean
  onToggleHidden: () => void
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  onClear: () => void
}

type Tip = {label: string; top: number}
const TOUCH_TIP_MS = 1800

/**
 * Yatay tam ekranda grafiğin solunda duran, yalnız simgeli dikey çizim araç çubuğu.
 * Yalnız mevcut çizim araçlarını (artı imleç, trend, yatay çizgi, alarm, silgi, tümünü sil) ve durum tabanlı
 * geri al / yinele / gizle işlevlerini sürer; emir, TP/SL veya veri akışına dokunmaz.
 * İpucu: fare üzerine gelince ve klavye odağında anında, dokunmatikte dokunduktan sonra kısa süre görünür.
 */
export function DrawingRail(props: Props) {
  const [open, setOpen] = useState(true)
  const [menu, setMenu] = useState(false)
  const [tip, setTip] = useState<Tip | null>(null)
  const rail = useRef<HTMLDivElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])
  useEffect(() => {
    if (!menu) return
    // Menü açıkken Esc yalnız menüyü kapatır; tam ekranı kapatmaması için üst bileşendeki escapeGuard true döner.
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenu(false) }
    const onPointer = (event: PointerEvent) => { if (!rail.current?.contains(event.target as Node)) setMenu(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointer)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('pointerdown', onPointer) }
  }, [menu])

  const showTip = (target: HTMLElement, label: string, autoHide: boolean) => {
    clearTimeout(timer.current)
    const railTop = rail.current?.getBoundingClientRect().top ?? 0
    setTip({label, top: target.getBoundingClientRect().top - railTop + target.offsetHeight / 2})
    if (autoHide) timer.current = setTimeout(() => setTip(null), TOUCH_TIP_MS)
  }
  const hideTip = () => { clearTimeout(timer.current); setTip(null) }

  // Her düğme: aria-label (ekran okuyucu) + özel ipucu (fare/klavye/dokunmatik). `title` kullanılmaz; çift ipucu olmasın.
  const tool = (label: string, icon: ReactNode, onClick: () => void, extra: {pressed?: boolean; disabled?: boolean; key?: string; menuItem?: boolean} = {}) => (
    <button type="button" key={extra.key ?? label} className="kfsTool" aria-label={label} aria-pressed={extra.pressed} disabled={extra.disabled}
      data-menu-item={extra.menuItem ? 'true' : undefined}
      onPointerEnter={event => { if (event.pointerType === 'mouse') showTip(event.currentTarget, label, false) }}
      onPointerLeave={event => { if (event.pointerType === 'mouse') hideTip() }}
      onFocus={event => { if (event.currentTarget.matches(':focus-visible')) showTip(event.currentTarget, label, false) }}
      onBlur={hideTip}
      onClick={event => {
        // Dokunma/kalem: hover yok; kısa süre ipucu göster. Fare ve klavye ipucunu zaten hover/odakla alır.
        const pointerType = (event.nativeEvent as PointerEvent).pointerType
        if (pointerType && pointerType !== 'mouse') showTip(event.currentTarget, label, true)
        onClick()
      }}>{icon}</button>
  )

  const pick = (mode: Exclude<DrawMode, 'none'>) => () => { setMenu(false); props.onPick(mode) }
  const collapseLabel = open ? 'Collapse drawing tools' : 'Expand drawing tools'

  return <div ref={rail} className="kfsRail" role="toolbar" aria-orientation="vertical" aria-label="Drawing tools" data-open={open} data-menu-open={menu}>
    <button type="button" className="kfsRailToggle" aria-label={collapseLabel} aria-expanded={open} aria-controls="kfsRailTools"
      onClick={() => { setOpen(value => !value); setMenu(false); hideTip() }}>{open ? <ChevronLeft aria-hidden="true"/> : <ChevronRight aria-hidden="true"/>}</button>
    <div id="kfsRailTools" className="kfsRailTools" hidden={!open}>
      {tool('Crosshair', <Crosshair aria-hidden="true"/>, props.onCrosshair, {pressed: props.crosshair})}
      <span className="kfsRailDivider" aria-hidden="true"/>
      {tool('Trend line', <TrendingUp aria-hidden="true"/>, pick('trend'), {pressed: props.drawMode === 'trend'})}
      {tool('Horizontal line', <Minus aria-hidden="true"/>, pick('hline'), {pressed: props.drawMode === 'hline'})}
      <span className="kfsRailDivider" aria-hidden="true"/>
      {tool('Eraser', <Eraser aria-hidden="true"/>, pick('erase'), {pressed: props.drawMode === 'erase', disabled: !props.hasDrawings})}
      {tool('Undo', <Undo2 aria-hidden="true"/>, props.onUndo, {disabled: !props.canUndo})}
      {tool('Redo', <Redo2 aria-hidden="true"/>, props.onRedo, {disabled: !props.canRedo})}
      <span className="kfsRailDivider" aria-hidden="true"/>
      <div className="kfsMore">
        <button type="button" className="kfsTool" aria-label="More tools" aria-haspopup="menu" aria-expanded={menu}
          onPointerEnter={event => { if (event.pointerType === 'mouse' && !menu) showTip(event.currentTarget, 'More tools', false) }}
          onPointerLeave={event => { if (event.pointerType === 'mouse') hideTip() }}
          onClick={() => { hideTip(); setMenu(value => !value) }}><MoreHorizontal aria-hidden="true"/></button>
        {menu && <div className="kfsMoreMenu" role="menu" aria-label="More tools">
          {tool('Price alert', <Bell aria-hidden="true"/>, pick('alarm'), {pressed: props.drawMode === 'alarm', menuItem: true})}
          {tool(props.hidden ? 'Show drawings' : 'Hide drawings', props.hidden ? <EyeOff aria-hidden="true"/> : <Eye aria-hidden="true"/>, () => { setMenu(false); props.onToggleHidden() }, {disabled: !props.hasDrawings && !props.hidden, menuItem: true, key: 'visibility'})}
          {tool('Clear all drawings', <Trash2 aria-hidden="true"/>, () => { setMenu(false); props.onClear() }, {disabled: !props.hasDrawings, menuItem: true})}
        </div>}
      </div>
    </div>
    {tip && open && <span className="kfsTip" role="status" style={{top: tip.top}}>{tip.label}</span>}
  </div>
}
