import {useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties} from 'react'
import {Check, Search, X} from 'lucide-react'
import {CoinIcon} from './src/components/CoinIcon'
import {coinBaseAsset} from './src/components/coin-symbol'
import {filterMarketQuotes, marketPrice, marketWindow, type MarketFilter, type MarketQuote, type MarketScore} from './master-market-data'

/**
 * Analysis Studio coin seçici: grafik başlığındaki sembole bağlı, kompakt yüzen panel.
 * Veri kaynağı mevcut piyasa akışıdır (useMasterMarketQuotes satırları); yeni API veya WebSocket açmaz, fiyat uydurmaz.
 * Seçim yalnız mevcut sembol akışını (onSelect → MasterTrade.selectMarket) çağırır; emir/pozisyona dokunmaz.
 */
type Props = {
  symbol: string
  rows: readonly MarketQuote[]
  scores: readonly MarketScore[]
  userId: string | null
  error?: string
  anchor: HTMLElement | null
  onSelect: (symbol: string) => void
  onClose: () => void
}

const ROW = 60
const FILTERS: MarketFilter[] = ['Tümü', 'Favoriler', 'Skorlu']
const SYMBOL_PATTERN = /^[A-Z0-9]+USDT$/

/** Piyasa listesindeki favoriler (aynı anahtar ve doğrulama); yeni kalıcılık eklenmez, yalnız okunur. */
function readFavorites(userId: string | null): Set<string> {
  if (!userId) return new Set()
  try {
    const raw = localStorage.getItem(`protrebot:master-market-favorites:${userId}`)
    const payload: unknown = raw === null ? [] : JSON.parse(raw)
    return Array.isArray(payload) && payload.every(item => typeof item === 'string' && SYMBOL_PATTERN.test(item)) ? new Set(payload) : new Set()
  } catch { return new Set() }
}

type Placement = {top: number; left: number; width: number; maxHeight: number; sheet: boolean}
function place(anchor: HTMLElement | null): Placement {
  const margin = 8, vw = window.innerWidth, vh = window.innerHeight
  const rect = anchor?.getBoundingClientRect()
  const top = Math.round(Math.min(Math.max((rect?.bottom ?? 48) + 6, margin), vh - 220))
  // Dar ekranda popover güvenle sığmazsa kenardan kenara (8 px boşluklu) kompakt panel.
  if (vw < 480) return {top, left: margin, width: vw - margin * 2, maxHeight: vh - top - margin, sheet: true}
  const width = Math.min(380, vw - margin * 2)
  const left = Math.round(Math.min(Math.max(rect?.left ?? margin, margin), vw - width - margin))
  return {top, left, width, maxHeight: Math.max(200, vh - top - margin), sheet: false}
}

export function MasterTradeCoinPicker({symbol, rows, scores, userId, error, anchor, onSelect, onClose}: Props) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<MarketFilter>('Tümü')
  const [favorites] = useState(() => readFavorites(userId))
  const [placement, setPlacement] = useState<Placement>(() => place(anchor))
  const [scroll, setScroll] = useState({offset: 0, extent: 360})
  const panel = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  const scoreMap = useMemo(() => new Map(scores.filter(row => Number.isFinite(row.score)).map(row => [row.symbol, row])), [scores])
  // Sentetik test satırları (OFFLINE TEST) grafik tarafından yüklenemez; seçicide gösterilmez.
  const usable = useMemo(() => rows.filter(row => !row.test && SYMBOL_PATTERN.test(row.symbol)), [rows])
  const filtered = useMemo(() => filterMarketQuotes(usable, scoreMap, favorites, query, filter, 'Hacim'), [usable, scoreMap, favorites, query, filter])
  const view = marketWindow(scroll.offset, scroll.extent, filtered.length, ROW)

  useLayoutEffect(() => {
    const update = () => setPlacement(place(anchor))
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [anchor])
  useLayoutEffect(() => {
    const element = list.current
    if (!element) return
    const measure = () => setScroll(current => ({...current, extent: element.clientHeight || current.extent}))
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()
    return () => observer.disconnect()
  }, [])
  // Açılışta seçili sembolü görünür alana getir.
  useLayoutEffect(() => {
    const element = list.current
    if (!element) return
    const index = filtered.findIndex(row => row.symbol === symbol)
    const offset = index > 2 ? (index - 2) * ROW : 0
    element.scrollTop = offset
    setScroll(current => ({...current, offset}))
  }, [])
  // Odak: fare/klavyede aramaya; dokunmatikte klavye açılıp yatay ekranı daraltmasın diye panele.
  useEffect(() => {
    if (matchMedia('(pointer: fine)').matches) search.current?.focus({preventScroll: true})
    else panel.current?.focus({preventScroll: true})
    const trigger = anchor
    return () => { if (trigger?.isConnected) trigger.focus({preventScroll: true}) }
  }, [anchor])
  // Kapatma: Esc ve dış tıklama (mobil geri, açan bileşendeki geçmiş kaydıyla yönetilir).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); closeRef.current() } }
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node
      if (panel.current?.contains(target) || anchor?.contains(target)) return
      closeRef.current()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointer, true)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointer, true)
    }
  }, [anchor])

  const style: CSSProperties = {top: placement.top, left: placement.left, width: placement.width, maxHeight: placement.maxHeight}
  return <div ref={panel} className="kfsCoinPicker" data-sheet={placement.sheet || undefined} role="dialog" aria-modal="false" aria-labelledby="kfsCoinPickerTitle" tabIndex={-1} style={style}>
    <div className="kfsCoinPickerHead">
      <div className="kfsCoinPickerTitle">
        <h3 id="kfsCoinPickerTitle">Piyasalar</h3>
        <span>{filtered.length}</span>
        <button type="button" className="kfsCoinPickerClose" aria-label="Kapat" onClick={onClose}><X aria-hidden="true"/></button>
      </div>
      <label className="kfsCoinSearch"><Search aria-hidden="true"/>
        <input ref={search} type="search" inputMode="search" autoComplete="off" spellCheck={false} aria-label="Coin veya sembol ara" placeholder="Coin veya sembol ara..."
          value={query} onChange={event => { setQuery(event.target.value); if (list.current) list.current.scrollTop = 0; setScroll(current => ({...current, offset: 0})) }}/>
      </label>
      <div className="kfsCoinFilters" role="group" aria-label="Piyasa filtreleri">
        {FILTERS.filter(item => item !== 'Favoriler' || userId).map(item => <button type="button" key={item} aria-pressed={filter === item}
          onClick={() => { setFilter(item); if (list.current) list.current.scrollTop = 0; setScroll(current => ({...current, offset: 0})) }}>{item}</button>)}
      </div>
    </div>
    <div ref={list} className="kfsCoinList" aria-label="Piyasa sembolleri" onScroll={event => { const offset = event.currentTarget.scrollTop; setScroll(current => current.offset === offset ? current : {...current, offset}) }}>
      {!filtered.length ? <p className="kfsCoinEmpty" role="status">{!usable.length && error ? error : 'Sonuç bulunamadı'}</p>
        : <div className="kfsCoinCanvas" style={{height: filtered.length * ROW}}>
          {filtered.slice(view.start, view.end).map((row, index) => {
            const selected = row.symbol === symbol
            const base = coinBaseAsset(row.symbol)
            return <button type="button" key={row.symbol} className="kfsCoinRow" data-symbol={row.symbol} aria-pressed={selected} aria-current={selected || undefined}
              style={{transform: `translateY(${(view.start + index) * ROW}px)`}} onClick={() => onSelect(row.symbol)}>
              <CoinIcon symbol={row.symbol} size={28}/>
              <span className="kfsCoinId"><b>{base}</b><small><span>{row.display}</span>{row.name && row.name !== base ? <span className="kfsCoinName" title={row.name}>{row.name}</span> : null}</small></span>
              <span className="kfsCoinQuote"><strong>{marketPrice(row.price)}</strong>
                <small data-tone={row.change === null ? 'neutral' : row.change >= 0 ? 'positive' : 'negative'}>{row.change === null ? '—' : `${row.change >= 0 ? '+' : ''}${row.change.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`}</small></span>
              <span className="kfsCoinMark" aria-hidden="true">{selected && <Check/>}</span>
            </button>
          })}
        </div>}
    </div>
  </div>
}
