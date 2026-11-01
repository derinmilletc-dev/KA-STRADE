import {memo, useEffect, useMemo, useRef, useState, type ReactNode} from 'react'
import {createPortal} from 'react-dom'
import {ChevronDown, Clock, Flame, Search, Star, X} from 'lucide-react'
import {CoinIcon} from './src/components/CoinIcon'
import {coinBaseAsset, coinDisplayName} from './src/components/coin-symbol'
import {filterMarketQuotes, marketPrice, type MarketFilter, type MarketQuote, type MarketScore, type MarketSort} from './master-market-data'
import './market-picker-mobile.css'

/**
 * Telefon için coin seçici: üstte tek satırlık şerit, dokununca alttan açılan akıllı liste.
 * Veri, favoriler ve seçim akışı masaüstü piyasa listesiyle aynı (rows, scores, onMarket); yeni API açmaz, fiyat uydurmaz.
 * "Son baktıkların" yalnız bu cihazda tutulur ve yalnız sembol adı içerir.
 */
const FILTERS: MarketFilter[] = ['Tümü', 'Favoriler', 'Skorlu', 'Yükselenler', 'Düşenler']
const SORTS: MarketSort[] = ['Skor', '24s %', 'Hacim', 'A-Z']
const PAGE = 60
const SYMBOL_PATTERN = /^[A-Z0-9]+USDT$/

type Props = {
  symbol: string; rows: readonly MarketQuote[]; scoreMap: ReadonlyMap<string, MarketScore>
  favorites: ReadonlySet<string>; canFavorite: boolean; onToggleFavorite: (symbol: string) => void
  userId: string | null; error?: string; stale?: boolean; onMarket: (symbol: string) => void
}

const changeText = (change: number | null) => change === null ? '—' : `${change >= 0 ? '+' : ''}${change.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}%`
const tone = (change: number | null) => change === null ? 'neutral' : change >= 0 ? 'positive' : 'negative'
const directionTone = (direction?: string) => direction === 'LONG' ? 'positive' : direction === 'SHORT' ? 'negative' : 'neutral'
const directionLabel = (direction?: string) => direction === 'LONG' || direction === 'SHORT' ? direction : 'NÖTR'

function recentKey(userId: string | null) { return `protrebot:master-market-recent:${userId ?? 'anon'}` }
function readRecent(userId: string | null): string[] {
  try {
    const payload: unknown = JSON.parse(localStorage.getItem(recentKey(userId)) ?? '[]')
    return Array.isArray(payload) ? payload.filter((item): item is string => typeof item === 'string' && SYMBOL_PATTERN.test(item)).slice(0, 6) : []
  } catch { return [] }
}
function writeRecent(userId: string | null, list: string[]) {
  try { localStorage.setItem(recentKey(userId), JSON.stringify(list.slice(0, 6))) } catch { /* yalnız kolaylık; yazılamazsa liste boş kalır */ }
}

/** Aramada en yakın eşleşme üste: tam taban (BTC) > taban başlangıcı > isim başlangıcı > içerir. */
function searchRank(row: MarketQuote, search: string) {
  if (!search) return 0
  const base = coinBaseAsset(row.symbol).toUpperCase()
  const name = coinDisplayName(row.symbol).toUpperCase().replace(/[^A-Z0-9]/g, '')
  if (base === search || row.symbol === search) return 0
  if (base.startsWith(search)) return 1
  if (name.startsWith(search)) return 2
  return 3
}

export function ScoreBadge({score}: {score?: MarketScore}) {
  if (!score) return null
  return <span className="mkScore" data-tone={directionTone(score.direction)} title="Tarayıcı skoru">{Math.round(score.score)}<small>{directionLabel(score.direction)}</small></span>
}

const Row = memo(function Row({row, score, selected, favorite, canFavorite, onPick, onToggleFavorite}: {
  row: MarketQuote; score?: MarketScore; selected: boolean; favorite: boolean; canFavorite: boolean; onPick: (symbol: string) => void; onToggleFavorite: (symbol: string) => void
}) {
  const previous = useRef(row.price)
  const [flash, setFlash] = useState<'up' | 'down' | null>(null)
  useEffect(() => {
    const before = previous.current
    previous.current = row.price
    if (before === null || row.price === null || before === row.price) return
    setFlash(row.price > before ? 'up' : 'down')
    const timer = window.setTimeout(() => setFlash(null), 700)
    return () => window.clearTimeout(timer)
  }, [row.price])
  const base = coinBaseAsset(row.symbol)
  return <div className="mkRow" data-symbol={row.symbol} data-selected={selected || undefined}>
    <button type="button" className="mkPick" aria-pressed={selected} onClick={() => onPick(row.symbol)}>
      <CoinIcon symbol={row.symbol} size={34}/>
      <span className="mkId"><b>{base}<em>/USDT</em></b><small>{coinDisplayName(row.symbol)}</small></span>
      <ScoreBadge score={score}/>
      <span className="mkQuote" data-flash={flash ?? undefined}><strong>{marketPrice(row.price)}</strong><small data-tone={tone(row.change)}>{changeText(row.change)}</small></span>
    </button>
    <button type="button" className="mkFav" aria-label={`${row.symbol} favori`} aria-pressed={favorite} disabled={!canFavorite} onClick={() => onToggleFavorite(row.symbol)}><Star aria-hidden="true"/></button>
  </div>
})

export function MarketPickerMobile({symbol, rows, scoreMap, favorites, canFavorite, onToggleFavorite, userId, error, stale, onMarket}: Props) {
  const [open, setOpen] = useState(false)
  const current = rows.find(row => row.symbol === symbol)
  const trigger = useRef<HTMLButtonElement>(null)
  return <>
    <button ref={trigger} type="button" className="mkBar" aria-haspopup="dialog" aria-expanded={open} aria-label={`Coin seç, şu an ${symbol}`} onClick={() => setOpen(true)}>
      <CoinIcon symbol={symbol} size={36}/>
      <span className="mkId"><b>{coinBaseAsset(symbol)}<em>/USDT</em></b><small>Coin değiştir</small></span>
      <ScoreBadge score={scoreMap.get(symbol)}/>
      <span className="mkQuote"><strong>{marketPrice(current?.price ?? null)}</strong><small data-tone={tone(current?.change ?? null)}>{changeText(current?.change ?? null)}</small></span>
      <ChevronDown className="mkChev" aria-hidden="true"/>
    </button>
    {(error || stale) && <p className="mkNotice" role="status">{error || 'Son kayıt gösteriliyor · Piyasa verisi güncel değil.'}</p>}
    {open && <MarketSheet symbol={symbol} rows={rows} scoreMap={scoreMap} favorites={favorites} canFavorite={canFavorite} onToggleFavorite={onToggleFavorite} userId={userId} error={error}
      onPick={value => { writeRecent(userId, [value, ...readRecent(userId).filter(item => item !== value)]); setOpen(false); onMarket(value) }}
      onClose={() => { setOpen(false); trigger.current?.focus({preventScroll: true}) }}/>}
  </>
}

function MarketSheet({symbol, rows, scoreMap, favorites, canFavorite, onToggleFavorite, userId, error, onPick, onClose}: Omit<Props, 'onMarket' | 'stale'> & {onPick: (symbol: string) => void; onClose: () => void}) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<MarketFilter>('Tümü')
  const [sort, setSort] = useState<MarketSort>('Skor')
  const [limit, setLimit] = useState(PAGE)
  const [recent] = useState(() => readRecent(userId))
  const panel = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  const usable = useMemo(() => rows.filter(row => !row.test && SYMBOL_PATTERN.test(row.symbol)), [rows])
  const bySymbol = useMemo(() => new Map(usable.map(row => [row.symbol, row])), [usable])
  const search = query.trim().toUpperCase().replace(/[^A-Z0-9]/g, '')
  const filtered = useMemo(() => {
    // İsimle arama: "bitcoin" yazınca BTC bulunsun diye isim eşleşmeleri de eklenir.
    const withNames = usable.map(row => ({...row, name: row.name ?? coinDisplayName(row.symbol)}))
    const result = filterMarketQuotes(withNames, scoreMap, favorites, query, filter, sort)
    return search ? result.map((row, index) => ({row, index, rank: searchRank(row, search)})).sort((a, b) => a.rank - b.rank || a.index - b.index).map(item => item.row) : result
  }, [usable, scoreMap, favorites, query, filter, sort, search])
  const smart = !search && filter === 'Tümü'
  const favoriteRows = useMemo(() => smart ? usable.filter(row => favorites.has(row.symbol)) : [], [smart, usable, favorites])
  // Bölümler birbirini tekrar etmesin: favoriler > en yüksek skor > son baktıkların.
  const topRows = useMemo(() => smart ? [...scoreMap.values()].filter(item => bySymbol.has(item.symbol) && !favorites.has(item.symbol)).sort((a, b) => b.score - a.score).slice(0, 5).map(item => bySymbol.get(item.symbol)!) : [], [smart, scoreMap, bySymbol, favorites])
  const recentRows = useMemo(() => {
    if (!smart) return []
    const shown = new Set(topRows.map(row => row.symbol))
    return recent.filter(item => !favorites.has(item) && !shown.has(item)).map(item => bySymbol.get(item)).filter((row): row is MarketQuote => Boolean(row))
  }, [smart, recent, bySymbol, favorites, topRows])

  useEffect(() => { setLimit(PAGE); if (list.current) list.current.scrollTop = 0 }, [query, filter, sort])
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panel.current?.focus({preventScroll: true})
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); closeRef.current() } }
    document.addEventListener('keydown', onKey)
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', onKey) }
  }, [])

  const row = (item: MarketQuote, keyPrefix: string) => <Row key={`${keyPrefix}${item.symbol}`} row={item} score={scoreMap.get(item.symbol)} selected={item.symbol === symbol}
    favorite={favorites.has(item.symbol)} canFavorite={canFavorite} onPick={onPick} onToggleFavorite={onToggleFavorite}/>
  const section = (title: string, icon: ReactNode, items: MarketQuote[], prefix: string) => items.length ? <section className="mkSection" aria-label={title}>
    <h4>{icon}{title}</h4>{items.map(item => row(item, prefix))}
  </section> : null

  return createPortal(<>
    <div className="mkShade" role="presentation" onClick={onClose}/>
    <div ref={panel} className="mkSheet" role="dialog" aria-modal="true" aria-labelledby="mkSheetTitle" tabIndex={-1}>
      <div className="mkHead">
        <span className="mkGrip" aria-hidden="true"/>
        <div className="mkTitle"><h3 id="mkSheetTitle">Piyasalar</h3><span>{filtered.length}</span><button type="button" className="mkClose" aria-label="Kapat" onClick={onClose}><X aria-hidden="true"/></button></div>
        <label className="mkSearch"><Search aria-hidden="true"/>
          <input type="search" inputMode="search" enterKeyHint="search" autoComplete="off" autoCapitalize="characters" spellCheck={false} aria-label="Coin ara" placeholder="Coin ara: BTC, ETH, Solana…" value={query} onChange={event => setQuery(event.target.value)}/>
          {query && <button type="button" className="mkClear" aria-label="Aramayı temizle" onClick={() => setQuery('')}><X aria-hidden="true"/></button>}
        </label>
        <div className="mkChips" role="group" aria-label="Piyasa filtreleri">
          {FILTERS.filter(item => item !== 'Favoriler' || canFavorite).map(item => <button type="button" key={item} aria-pressed={filter === item} onClick={() => setFilter(item)}>{item}</button>)}
          <label className="mkSort"><span>Sırala:</span><select aria-label="Piyasa sıralaması" value={sort} onChange={event => { const next = SORTS.find(item => item === event.target.value); if (next) setSort(next) }}>{SORTS.map(item => <option key={item}>{item}</option>)}</select><ChevronDown aria-hidden="true"/></label>
        </div>
      </div>
      <div ref={list} className="mkList" aria-label="Piyasa sembolleri">
        {!usable.length ? <p className="mkEmpty" role="status">{error || 'Piyasa verisi yükleniyor…'}</p> : <>
          {smart && section('Favorilerin', <Star aria-hidden="true"/>, favoriteRows, 'f-')}
          {smart && section('En yüksek skor', <Flame aria-hidden="true"/>, topRows, 't-')}
          {smart && section('Son baktıkların', <Clock aria-hidden="true"/>, recentRows, 'r-')}
          <section className="mkSection" aria-label={smart ? 'Tüm piyasalar' : 'Sonuçlar'}>
            <h4>{smart ? 'Tüm piyasalar' : search ? `"${query.trim()}" için sonuçlar` : filter}</h4>
            {!filtered.length ? <p className="mkEmpty" role="status">{filter === 'Favoriler' ? 'Henüz favorin yok. Bir coinin yanındaki yıldıza dokun.' : filter === 'Skorlu' ? 'Şu an skoru olan coin yok. Tarayıcı çalışınca burada görünür.' : 'Sonuç bulunamadı.'}</p>
              : filtered.slice(0, limit).map(item => row(item, 'a-'))}
            {filtered.length > limit && <button type="button" className="mkMore" onClick={() => setLimit(value => value + PAGE)}>Daha fazla göster ({filtered.length - limit})</button>}
          </section>
        </>}
      </div>
    </div>
  </>, document.body)
}
