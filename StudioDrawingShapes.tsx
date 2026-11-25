import {useId, useRef, type FormEvent, type KeyboardEvent, type MouseEvent as ReactMouseEvent, type ReactNode} from 'react'
import {TEXT_LIMIT, type Drawing} from './chart-drawings'
import {channelLines, durationText, fibLabel, fibLevels, measure, rectBounds} from './studio-draw-geometry'

// Yeni çizim araçlarının şekilleri (fibonacci, dikdörtgen, kanal, ölçüm, metin). Yalnız görünüm; emir göndermez.
export type ProDrawing = Exclude<Drawing, {type: 'hline'} | {type: 'trend'}>
export const isProDrawing = (drawing: Drawing): drawing is ProDrawing => drawing.type !== 'hline' && drawing.type !== 'trend'

export type ShapeCtx = {
  plotWidth: number; priceHeight: number; y: (price: number) => number; tx: (time: number) => number
  price: (value: number) => string; step: number; erasing: boolean; onErase: (id: string) => void
}

const FIB_TONE: Record<string, string> = {'0': 'edge', '1': 'edge', '0.5': 'mid', '0.618': 'gold', '0.382': 'gold', '0.786': 'soft', '0.236': 'soft', '1.272': 'ext', '1.618': 'ext'}

export function DrawingShape({drawing, ctx, draft = false}: {drawing: ProDrawing; ctx: ShapeCtx; draft?: boolean}) {
  const {y, tx, price, plotWidth} = ctx
  const erase = draft ? {} : {
    onContextMenu: (event: ReactMouseEvent) => { event.preventDefault(); event.stopPropagation(); if ((event.nativeEvent as PointerEvent).pointerType !== 'touch') ctx.onErase(drawing.id) },
    onClick: (event: ReactMouseEvent) => { if (!ctx.erasing) return; event.stopPropagation(); ctx.onErase(drawing.id) },
  }
  const cls = `refDrawing refDrawing--${drawing.type}${draft ? ' refDrawing--draft' : ''}`
  const hitLine = (x1: number, y1: number, x2: number, y2: number, key?: string) =>
    draft ? null : <line key={key} className="refDrawingHit" x1={x1} y1={y1} x2={x2} y2={y2} {...erase}><title>Silmek için sağ tıkla</title></line>
  const hitArea = (x0: number, y0: number, w: number, h: number): ReactNode =>
    draft ? null : <rect className="refDrawingHit refDrawingHitArea" x={x0} y={y0} width={w} height={h} {...erase} style={{pointerEvents: ctx.erasing ? 'all' : 'none'}}/>

  switch (drawing.type) {
    case 'fib': {
      const levels = fibLevels(drawing.a, drawing.b)
      const ax = tx(drawing.a.time), bx = tx(drawing.b.time)
      const left = Math.min(ax, bx), right = plotWidth
      const labelX = Math.min(left + 4, plotWidth - 150)
      // Seviyeler birbirine çok yakınsa (küçük hareket) yazılar üst üste biniyordu: 13px'ten yakın olanın yazısı gizlenir.
      const shown = new Set<number>()
      let lastY = -Infinity
      for (const level of [...levels].sort((p, q) => y(p.price) - y(q.price))) {
        const py = y(level.price)
        if (py - lastY >= 13) { shown.add(level.ratio); lastY = py }
      }
      const find = (ratio: number) => levels.find(level => level.ratio === ratio)!
      const zoneTop = y(find(0.5).price), zoneBottom = y(find(0.618).price)
      return <g className={cls} data-up={drawing.b.price >= drawing.a.price}>
        <rect className="refFibZone" x={left} y={Math.min(zoneTop, zoneBottom)} width={Math.max(0, right - left)} height={Math.abs(zoneBottom - zoneTop)}/>
        <line className="refFibDiag" x1={ax} y1={y(drawing.a.price)} x2={bx} y2={y(drawing.b.price)}/>
        {levels.map(level => <g key={level.ratio} className="refFibLevel" data-tone={FIB_TONE[String(level.ratio)] ?? 'soft'}>
          <line x1={left} x2={right} y1={y(level.price)} y2={y(level.price)}/>
          {shown.has(level.ratio) && <text x={labelX} y={y(level.price) - 3}>{fibLabel(level.ratio)} · {price(level.price)}</text>}
        </g>)}
        {hitLine(ax, y(drawing.a.price), bx, y(drawing.b.price))}
        {levels.map(level => hitLine(left, y(level.price), right, y(level.price), `h${level.ratio}`))}
      </g>
    }
    case 'rect': {
      const box = rectBounds(drawing.a, drawing.b)
      const x1 = tx(box.t1), x2 = tx(box.t2), top = y(box.high), bottom = y(box.low)
      const pct = drawing.a.price !== 0 ? (drawing.b.price - drawing.a.price) / drawing.a.price * 100 : null
      return <g className={cls} data-up={drawing.b.price >= drawing.a.price}>
        <rect className="refRectBody" x={x1} y={top} width={Math.max(1, x2 - x1)} height={Math.max(1, bottom - top)}/>
        <text x={x1 + 4} y={top + 12}>{price(box.high)}</text>
        <text x={x1 + 4} y={bottom - 4}>{price(box.low)}{pct !== null ? ` · ${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : ''}</text>
        {hitArea(x1, top, Math.max(1, x2 - x1), Math.max(1, bottom - top))}
        {hitLine(x1, top, x2, top, 't')}{hitLine(x1, bottom, x2, bottom, 'b')}{hitLine(x1, top, x1, bottom, 'l')}{hitLine(x2, top, x2, bottom, 'r')}
      </g>
    }
    case 'channel': {
      const lines = channelLines(drawing.a, drawing.b, drawing.c)
      const pts = (l: {a: {time: number; price: number}; b: {time: number; price: number}}) => ({x1: tx(l.a.time), y1: y(l.a.price), x2: tx(l.b.time), y2: y(l.b.price)})
      const main = pts(lines.main), par = pts(lines.parallel), mid = pts(lines.middle)
      return <g className={cls}>
        <polygon className="refChannelFill" points={`${main.x1},${main.y1} ${main.x2},${main.y2} ${par.x2},${par.y2} ${par.x1},${par.y1}`}/>
        <line className="refChannelMain" {...main}/><line className="refChannelPar" {...par}/><line className="refChannelMid" {...mid}/>
        {hitLine(main.x1, main.y1, main.x2, main.y2, 'm')}{hitLine(par.x1, par.y1, par.x2, par.y2, 'p')}
      </g>
    }
    case 'measure': {
      const m = measure(drawing.a, drawing.b, ctx.step)
      const ax = tx(drawing.a.time), bx = tx(drawing.b.time), ay = y(drawing.a.price), by = y(drawing.b.price)
      const label = `${m.delta >= 0 ? '+' : ''}${price(m.delta)}${m.pct !== null ? ` (${m.pct >= 0 ? '+' : ''}${m.pct.toFixed(2)}%)` : ''} · ${m.bars} mum · ${durationText(m.seconds)}`
      const width = Math.min(plotWidth - 8, label.length * 6.1 + 16)
      const cx = Math.max(4, Math.min(plotWidth - width - 4, (ax + bx) / 2 - width / 2))
      const cy = Math.max(4, Math.min(ctx.priceHeight - 24, Math.min(ay, by) - 30))
      return <g className={cls} data-up={m.up}>
        <rect className="refMeasureBox" x={Math.min(ax, bx)} y={Math.min(ay, by)} width={Math.abs(bx - ax)} height={Math.abs(by - ay)}/>
        <line className="refMeasureLine" x1={ax} y1={ay} x2={bx} y2={by}/>
        <circle cx={ax} cy={ay} r="3"/><circle cx={bx} cy={by} r="3"/>
        <rect className="refMeasureTag" x={cx} y={cy} width={width} height="20" rx="5"/>
        <text className="refMeasureText" x={cx + width / 2} y={cy + 10} textAnchor="middle" dominantBaseline="middle">{label}</text>
        {hitLine(ax, ay, bx, by)}{hitArea(Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay))}
      </g>
    }
    case 'text': {
      const px = tx(drawing.p.time), py = y(drawing.p.price)
      const width = Math.min(plotWidth - 8, drawing.text.length * 6.6 + 14)
      const x0 = Math.max(4, Math.min(plotWidth - width - 4, px - width / 2)), y0 = Math.max(4, Math.min(ctx.priceHeight - 22, py - 11))
      return <g className={cls}>
        <circle cx={px} cy={py} r="3"/>
        <rect className="refTextTag" x={x0} y={y0} width={width} height="22" rx="6"/>
        <text className="refTextLabel" x={x0 + width / 2} y={y0 + 11} textAnchor="middle" dominantBaseline="middle">{drawing.text}</text>
        {!draft && <rect className="refDrawingHit refDrawingHitArea" x={x0} y={y0} width={width} height="22" {...erase} style={{pointerEvents: 'all', fill: 'transparent'}}/>}
      </g>
    }
  }
}

/** Yeni şekiller tek katmanda, fiyat bölmesine kırpılarak çizilir. */
export function ProShapes({drawings, ctx, draft}: {drawings: readonly ProDrawing[]; ctx: ShapeCtx; draft?: ProDrawing | null}) {
  const clip = useId().replace(/:/g, '')
  if (!drawings.length && !draft) return null
  return <g className="refDrawings refDrawings--pro" data-erasing={ctx.erasing} clipPath={`url(#${clip})`}>
    <defs><clipPath id={clip}><rect x="0" y="0" width={ctx.plotWidth} height={ctx.priceHeight}/></clipPath></defs>
    {drawings.map(drawing => <DrawingShape key={drawing.id} drawing={drawing} ctx={ctx}/>)}
    {draft && <DrawingShape drawing={draft} ctx={ctx} draft/>}
  </g>
}

/** Metin aracı: tıklanan noktada küçük yazı kutusu. Enter kaydeder, Esc ya da boş gönderim iptal eder. */
export function TextPrompt({left, top, onSubmit, onCancel}: {left: number; top: number; onSubmit: (text: string) => void; onCancel: () => void}) {
  const input = useRef<HTMLInputElement>(null)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = (input.current?.value ?? '').trim().slice(0, TEXT_LIMIT)
    if (value) onSubmit(value); else onCancel()
  }
  const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); onCancel() } }
  return <form className="refTextPrompt" style={{left, top}} onSubmit={submit} onKeyDown={key} onPointerDown={event => event.stopPropagation()}>
    <input ref={input} autoFocus maxLength={TEXT_LIMIT} placeholder="Notunu yaz…" aria-label="Grafik notu" enterKeyHint="done"/>
    <button type="submit" aria-label="Notu ekle">Ekle</button>
    <button type="button" className="refTextCancel" aria-label="Vazgeç" onClick={onCancel}>×</button>
  </form>
}

const icon = (children: ReactNode, label: string) => <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" data-icon={label}>{children}</svg>
export const FibIcon = () => icon(<><path d="M3 5h18M3 10h18M3 15h18M3 20h18" opacity=".55"/><path d="M5 19 19 6"/></>, 'fib')
export const RectIcon = () => icon(<rect x="4" y="6" width="16" height="12" rx="1.5"/>, 'rect')
export const ChannelIcon = () => icon(<><path d="M3 17 21 8"/><path d="M3 11 21 2" opacity=".0"/><path d="M3 22 21 13"/><path d="M3 19.5 21 10.5" strokeDasharray="2 3" opacity=".6"/></>, 'channel')
export const MeasureIcon = () => icon(<><path d="M4 20 20 4"/><path d="M4 14v6h6M14 4h6v6"/></>, 'measure')
export const TextIcon = () => icon(<><path d="M5 6h14M12 6v13"/><path d="M9 19h6"/></>, 'text')
