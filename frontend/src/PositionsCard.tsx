import {useMemo, useState, type CSSProperties} from 'react'
import {ChevronDown, SlidersHorizontal, Target, X} from 'lucide-react'
import {duration} from './live-guide'
import {closeEstimate, netOfFees, TAKER_FEE_RATE, type QuoteMap} from './live-quote'
import {activeCloseTarget, netAtPrice, parseAmount, priceForNetProfit, type CloseTargetMode} from './close-target'
import {
  formatPct, formatPrice, formatR, formatUsdt, num, positionInsights, positionMetrics, sortPositions, summarize,
  type Ladder, type PositionMetrics, type PositionSort, type RawRecord,
} from './position-view'

// Kapatma / düzenleme hedefleri LiveCockpit'teki türlerle birebir aynıdır (orada onay penceresi açılır; bu dosya emir göndermez).
// long/entryPrice/quantity: onay penceresi açıkken K/Z'yi canlı fiyattan yeniden hesaplamak için.
export type PositionCloseTarget = {planId: string; symbol: string; direction: string; entry: string; pnl: number | null; long?: boolean; entryPrice?: number | null; quantity?: number | null}
export type PositionManualTarget = PositionCloseTarget & {stop: string; targets: [string, string, string]; mark: string}
export type SmartPositionItem = {plan_id?: string; symbol?: string; mode?: string; last_score?: number | null; timer_end?: number | null; direction_state?: string; opposite_confidence?: number | null}

type Props = {
  live: boolean; stale: boolean; staleAge: number; running: boolean; busy: boolean; now: number
  positions: RawRecord[]
  findPlan: (position: RawRecord) => RawRecord | undefined
  maxPositions: number | null; exposureLimit: number | null; wallet: number | null; dailyLoss: number | null; dailyLimit: number | null
  smartItems: SmartPositionItem[]; smartEnabled: boolean
  tp1Share: (plan: RawRecord | undefined) => number; tp2Share: (plan: RawRecord | undefined) => number
  onClose: (target: PositionCloseTarget) => void
  onEdit: (target: PositionManualTarget) => void
  onReduce: (target: PositionManualTarget, percent: 25 | 50 | 75) => void
  /** Binance'ten canlı en iyi alış/satış (useLiveQuotes). Taze değilse sunucu verisi gösterilir ve uyarı çıkar. */
  quotes?: QuoteMap; quoteNow?: number
  /** Risk kutusundaki "Düzenle" butonu: manuel işlem risk limitleri penceresini açar. Ayar yoksa verilmez, buton görünmez. */
  onRiskSettings?: () => void
  /** Manuel pozisyon kapanış hedefi kur/kaldır ("fiyat X olunca" / "net Y USDT kârda"). Sunucu ~10 sn'de bir bakar. */
  onCloseTarget?: (planId: string, mode: CloseTargetMode | 'off', value: number | null) => Promise<boolean>
  /** Emir borsaya yeni gitti ama bu coinin pozisyonu listede henüz yok: boş durum yerine "yükleniyor" gösterilir. */
  awaitingSymbol?: string | null
}

const SORTS: ReadonlyArray<readonly [PositionSort, string]> = [['risk', 'Risk'], ['pnl', 'Kâr/Zarar'], ['time', 'Süre']]
const SMART_MODE: Record<string, string> = {NORMAL: 'Normal', KAR_KORUMA: 'Kâr koruma', CIKISA_HAZIR: 'Çıkışa hazır'}
const VIEW_KEY = 'kais-positions-view'
const readView = (): 'card' | 'table' => { try { return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'card' } catch { return 'card' } }

// Coinin güncel güven puanı: sunucu her kapanmış 15 dk mumda botun giriş stratejisini yeniden çalıştırıp plana yazar (live_score).
// Henüz yoksa Akıllı Kâr Koruma'nın son puanı kullanılır. Giriş puanı ve eşiği planın giriş kaydından okunur.
type LiveScore = {score?: number | null; mismatch?: boolean; opposite_confidence?: number | null; candle?: number | null}
type ScoreInfo = {now: number | null; entry: number | null; threshold: number; mismatch: boolean; opposite: number | null; candle: number | null; delta: number | null; tone: 'up' | 'warn' | 'down'}
function scoreInfo(plan: RawRecord | undefined, smart: SmartPositionItem | undefined): ScoreInfo | null {
  if (!plan) return null
  const live = plan.live_score && typeof plan.live_score === 'object' ? plan.live_score as LiveScore : null
  const record = plan.original_record as {entry?: {signal?: {confidence?: unknown}; policy?: {min_confidence?: unknown}}} | undefined
  const entry = num(record?.entry?.signal?.confidence)
  const threshold = num(record?.entry?.policy?.min_confidence) ?? 70
  const mismatch = live ? live.mismatch === true : smart?.direction_state === 'MISMATCH'
  let current = live && typeof live.score === 'number' ? live.score : null
  if (!live && typeof smart?.last_score === 'number') current = smart.last_score
  if (current === null && !mismatch) return null
  const opposite = num(live ? live.opposite_confidence : smart?.opposite_confidence)
  const delta = current !== null && entry !== null ? Math.round(current - entry) : null
  const tone = mismatch || current === null ? 'down' : current >= threshold ? 'up' : current >= threshold - 10 ? 'warn' : 'down'
  return {now: current, entry, threshold, mismatch, opposite, candle: live && typeof live.candle === 'number' ? live.candle : null, delta, tone}
}

const tone = (value: number | null) => value === null ? undefined : value < 0 ? 'down' : value > 0 ? 'up' : undefined

function LadderView({ladder, metrics}: {ladder: Ladder; metrics: PositionMetrics}) {
  const rows = ladder.markers.some(marker => marker.row === 1) ? 2 : 1
  return <div className="pvLadder" role="group" aria-label="Fiyat çubuğu" style={{'--rows': rows, '--e': `${ladder.entryPos}%`} as CSSProperties}>
    <div className="pvTrack" data-rev={!ladder.long || undefined} aria-hidden="true"/>
    {ladder.markers.map(marker => <span key={marker.key} className="pvMk" data-row={marker.row} data-key={marker.key} style={{left: `${marker.pos}%`}}><small>{marker.label}</small><b>{formatPrice(marker.price)}</b></span>)}
    <i className="pvDot" data-level={metrics.level} style={{left: `${ladder.markPos}%`}} aria-hidden="true"/>
    <span className="pvMk pvNow" data-level={metrics.level} style={{left: `${ladder.markPos}%`}}><b>{formatPrice(metrics.mark)}</b><small>şu an</small></span>
  </div>
}

// Kapanış hedefi kutusu: "net X USDT kârda kapat" ya da "fiyat Y olunca kapat". Önizleme canlı fiyattan hesaplanır;
// emri sunucu verir (koruma döngüsü ~10 sn). Stop ve hedefler olduğu gibi kalır.
const PROFIT_CHIPS = [0.5, 1, 2, 5]
function CloseTargetBox({planId, metrics, plan, busy, onSet}: {planId: string; metrics: PositionMetrics; plan: RawRecord | undefined; busy: boolean; onSet: NonNullable<Props['onCloseTarget']>}) {
  const active = activeCloseTarget(plan?.close_target)
  const [editing, setEditing] = useState(false)
  const [mode, setMode] = useState<CloseTargetMode>('profit')
  const [text, setText] = useState('')
  const [saving, setSaving] = useState(false)
  const long = metrics.long === true
  const entry = metrics.entry, quantity = metrics.quantity, mark = metrics.mark
  const value = parseAmount(text)
  const coinPrice = (price: number | null) => price === null ? '—' : formatPrice(price)
  const pctFrom = (price: number | null) => price !== null && mark !== null && mark > 0 ? (price - mark) / mark * 100 : null
  const signedPct = (pct: number | null) => pct === null ? '' : ` (${pct >= 0 ? '+' : '−'}${formatPct(Math.abs(pct), 2)})`
  const levels = Array.isArray(plan?.targets) ? (plan?.targets as unknown[]).map(item => Number(item)).filter(item => Number.isFinite(item) && item > 0) : []
  const breakeven = priceForNetProfit(long, entry, quantity, 0, TAKER_FEE_RATE)
  const neededPrice = mode === 'profit' && value !== null ? priceForNetProfit(long, entry, quantity, value, TAKER_FEE_RATE) : null
  const netThere = mode === 'price' && value !== null ? netAtPrice(long, entry, quantity, value, TAKER_FEE_RATE) : null
  const nowNet = netAtPrice(long, entry, quantity, mark, TAKER_FEE_RATE)
  const instant = mode === 'profit' ? value !== null && nowNet !== null && nowNet >= value : false
  const preview = value === null ? null : mode === 'profit'
    ? (neededPrice === null ? 'Bu kâr için fiyat hesaplanamadı.' : `Fiyat ≈ ${coinPrice(neededPrice)}${signedPct(pctFrom(neededPrice))} olunca kapanır.`)
    : `${mark !== null && value > mark ? 'Fiyat çıkıp' : 'Fiyat inip'} ${coinPrice(value)}${signedPct(pctFrom(value))} olunca kapanır · o fiyatta net ${netThere === null ? '—' : `${formatUsdt(netThere, true)} USDT`}.`
  const submit = async () => {
    if (value === null || saving) return
    setSaving(true)
    const done = await onSet(planId, mode, value).catch(() => false)
    setSaving(false)
    if (done) { setEditing(false); setText('') }
  }
  const clear = async () => { setSaving(true); await onSet(planId, 'off', null).catch(() => false); setSaving(false) }
  if (active && !editing) {
    const activePrice = active.mode === 'profit' ? priceForNetProfit(long, entry, quantity, active.value, TAKER_FEE_RATE) : active.value
    return <div className="pvTarget" data-active="yes">
      <span className="pvTargetIcon" aria-hidden="true"><Target/></span>
      <span className="pvTargetText"><small>KAPANIŞ HEDEFİ AKTİF</small>
        <b>{active.mode === 'profit' ? `Net +${formatUsdt(active.value)} USDT kârda kapanır` : `Fiyat ${coinPrice(active.value)} olunca kapanır`}</b>
        <em>{active.mode === 'profit' ? `Fiyat ≈ ${coinPrice(activePrice)}${signedPct(pctFrom(activePrice))}` : `${active.side === 'ABOVE' ? 'Yukarı' : 'Aşağı'} yönde${signedPct(pctFrom(activePrice))}`}{nowNet !== null ? ` · şu an net ${formatUsdt(nowNet, true)}` : ''}</em></span>
      <span className="pvTargetBtns"><button type="button" disabled={busy || saving} onClick={() => { setMode(active.mode); setText(String(active.value).replace('.', ',')); setEditing(true) }}>Değiştir</button>
        <button type="button" data-k="off" disabled={busy || saving} onClick={() => void clear()}>Kaldır</button></span>
    </div>
  }
  if (!editing) return <button type="button" className="pvTargetOpen" disabled={busy} onClick={() => setEditing(true)}>
    <Target aria-hidden="true"/><span><b>Kapanış hedefi ekle</b><small>Örn. net 1 USDT kârda ya da belli bir fiyatta otomatik kapat</small></span>
  </button>
  return <div className="pvTarget" data-editing="yes">
    <div className="pvTargetHead"><b><Target aria-hidden="true"/>Kapanış hedefi</b>
      <span className="pvSeg" role="group" aria-label="Hedef türü">
        <button type="button" aria-pressed={mode === 'profit'} onClick={() => { setMode('profit'); setText('') }}>Kâr ile</button>
        <button type="button" aria-pressed={mode === 'price'} onClick={() => { setMode('price'); setText('') }}>Fiyat ile</button>
      </span></div>
    <label className="pvTargetField"><span>{mode === 'profit' ? 'Net kâr (komisyon sonrası)' : 'Kapanış fiyatı'}</span>
      <span className="pvTargetInput"><input inputMode="decimal" autoComplete="off" placeholder={mode === 'profit' ? '1,00' : coinPrice(mark)} value={text} onChange={event => setText(event.target.value)}/><em>{mode === 'profit' ? 'USDT' : 'USDT fiyat'}</em></span></label>
    <div className="pvTargetChips">
      {mode === 'profit'
        ? PROFIT_CHIPS.map(chip => <button type="button" key={chip} aria-pressed={value === chip} onClick={() => setText(String(chip).replace('.', ','))}>{`+${String(chip).replace('.', ',')} USDT`}</button>)
        : <>{levels[0] ? <button type="button" onClick={() => setText(String(levels[0]))}>TP1 · {coinPrice(levels[0])}</button> : null}
          {breakeven !== null ? <button type="button" onClick={() => setText(String(Number(breakeven.toPrecision(6))))}>Başa baş · {coinPrice(breakeven)}</button> : null}</>}
    </div>
    {preview && <p className="pvTargetPreview" data-warn={instant || undefined}>{instant ? 'Şu anki kâr bu hedefin üstünde: kurarsan ilk kontrolde (~10 sn) kapanır.' : preview}</p>}
    <p className="pvTargetNote">Sunucu fiyatı ~10 sn'de bir kontrol eder; hedefe ulaşınca piyasa emriyle kapatır, kapanış fiyatı hedeften biraz farklı olabilir. Stop ve hedeflerin yerinde kalır.</p>
    <div className="pvTargetBtns"><button type="button" data-k="ghost" disabled={saving} onClick={() => { setEditing(false); setText('') }}>Vazgeç</button>
      <button type="button" data-k="main" disabled={busy || saving || value === null} onClick={() => void submit()}>{saving ? 'Kuruluyor…' : 'Hedefi kur'}</button></div>
  </div>
}

const Cell = ({label, value, tone: cellTone}: {label: string; value: string | null; tone?: 'up' | 'down' | 'warn'}) =>
  value === null ? null : <div><small>{label}</small><b data-tone={cellTone}>{value}</b></div>

export default function PositionsCard(props: Props) {
  const {live, stale, staleAge, running, busy, now, positions, findPlan} = props
  const [sort, setSort] = useState<PositionSort>('time')
  const [view, setView] = useState<'card' | 'table'>(readView)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const changeView = (next: 'card' | 'table') => { setView(next); try { localStorage.setItem(VIEW_KEY, next) } catch { /* tercih bu sayfada kalır */ } }

  const items = useMemo(() => positions.map((position, index) => {
    const plan = findPlan(position)
    const external = !plan || plan.provenance_state === 'READ_ONLY_EXTERNAL' || plan.source === 'READ_ONLY_EXTERNAL'
    const confirmed = !external && plan?.provenance_state === 'CONFIRMED'
    const protection = confirmed ? (plan?.exchange_protection as {stop?: boolean; tp1?: boolean; tp2?: boolean; tp3?: boolean} | undefined) : undefined
    // Dış pozisyonda plan yoktur: stop/hedef bilinmez, yalnız Binance verisi gösterilir.
    const base = positionMetrics(position, external ? undefined : plan, now)
    // Ekrandaki K/Z: Binance'in anlık kapanış fiyatından (LONG → alış, SHORT → satış). Taze fiyat yoksa sunucu verisi.
    const estimate = closeEstimate({long: base.long, entry: base.entry, quantity: base.quantity, quote: props.quotes?.[base.symbol.toUpperCase()], nowMs: props.quoteNow ?? Date.now()})
    // Gösterilen K/Z komisyon sonrası nettir (giriş + çıkış taker komisyonu düşülür); Binance'in ham "unrealized" değeri komisyonsuzdur.
    const net = estimate ? estimate.netPnl : netOfFees(base.pnl, base.entry, base.mark, base.quantity)
    const metrics = estimate ? positionMetrics(position, external ? undefined : plan, now, {price: estimate.exitPrice, pnl: estimate.netPnl})
      : net !== null && base.mark !== null ? positionMetrics(position, external ? undefined : plan, now, {price: base.mark, pnl: net}) : base
    return {key: `${String(position.symbol)}-${index}`, position, plan, external, confirmed, protection, metrics, livePrice: estimate !== null, protectedStop: confirmed && protection?.stop === true}
  }), [positions, findPlan, now, props.quotes, props.quoteNow])
  const sorted = useMemo(() => sortPositions(items, sort), [items, sort])
  const summary = useMemo(() => summarize(items, {wallet: props.wallet, dailyLoss: props.dailyLoss, dailyLimit: props.dailyLimit, exposureLimit: props.exposureLimit}), [items, props.wallet, props.dailyLoss, props.dailyLimit, props.exposureLimit])

  const title = `Açık pozisyonlar${live ? ` · ${positions.length}${props.maxPositions !== null ? ` / ${props.maxPositions}` : ''}` : ''}`
  const allLive = items.length > 0 && items.every(item => item.livePrice)
  const freshness = live ? stale ? `${duration(staleAge)} önce` : positions.length === 0 ? 'Binance verisi' : allLive ? '● Canlı fiyat' : '⚠ Fiyat gecikmeli olabilir' : 'Doğrulama bekleniyor'
  const smartFor = (plan: RawRecord | undefined, symbol: string) => props.smartItems.find(item => (item.plan_id && item.plan_id === plan?.id) || item.symbol === symbol)

  return <section className="lcCard pvRoot" aria-label="Açık pozisyonlar" data-view={view}>
    <header className="lcCardHead"><div><small>POZİSYONLAR</small><h3>{title}</h3></div><small data-tone={live && !stale && positions.length > 0 ? allLive ? 'up' : 'warn' : undefined}>{freshness}</small></header>
    {live && positions.length > 0 && !allLive && <p className="lcStaleNote" role="status">Canlı fiyat alınamadı: kâr/zarar 10–30 sn geriden gelebilir. Kapatmadan önce Binance uygulamasından kontrol et.</p>}
    {live && props.awaitingSymbol && <p className="lcStaleNote" role="status" data-testid="pv-awaiting">⏳ {props.awaitingSymbol} pozisyonu yükleniyor… Emir borsaya gitti, birkaç saniye içinde burada görünür.</p>}
    {positions.length === 0 ? (props.awaitingSymbol && live ? null : <p className="lcEmpty">{live ? running ? 'Şu an açık pozisyon yok. Uygun fırsat çıkınca stop ve hedefleriyle burada görünür.' : 'Açık pozisyon yok.' : 'Güncel pozisyonlar doğrulanamadı.'}</p>) : <div className="pvInner">
      <div className="pvTools">
        <div className="pvSeg pvViewSeg" role="group" aria-label="Görünüm">
          <button type="button" aria-pressed={view === 'card'} onClick={() => changeView('card')}>Kart</button>
          <button type="button" aria-pressed={view === 'table'} onClick={() => changeView('table')}>Tablo</button>
        </div>
        <div className="pvSeg" role="group" aria-label="Sıralama">
          {SORTS.map(([id, label]) => <button type="button" key={id} aria-pressed={sort === id} onClick={() => setSort(id)}>{label}</button>)}
        </div>
      </div>

      <div className="pvSum" aria-label="Portföy özeti">
        <div className="pvTile"><small>Toplam kâr / zarar · net</small><b data-tone={tone(summary.pnl)}>{summary.pnl === null ? '—' : `${formatUsdt(summary.pnl, true)} USDT`}</b>
          <em>{summary.pnlOfBalancePct !== null ? `${formatPct(summary.pnlOfBalancePct, 1)} hesap` : `${summary.count} açık pozisyon`} · komisyon dahil</em></div>
        <div className="pvTile pvRiskTile"><small>Stopta toplam risk</small><b>{summary.riskAtStop === null ? '—' : `${formatUsdt(summary.riskAtStop)} USDT`}</b>
          <em>{summary.riskOfRemainingDailyPct !== null ? `Kalan günlük sınırın ${formatPct(summary.riskOfRemainingDailyPct, 0)}'i` : summary.riskUnknown > 0 ? `${summary.riskUnknown} pozisyon için hesaplanamadı` : 'Tüm stoplar çalışırsa'}</em>
          {summary.riskOfRemainingDailyPct !== null && <span className="pvBar" data-high={summary.riskOfRemainingDailyPct >= 80 || undefined}><i style={{width: `${Math.min(100, summary.riskOfRemainingDailyPct)}%`}}/></span>}
          {props.onRiskSettings && <button type="button" className="pvEditBtn" onClick={props.onRiskSettings} aria-label="Manuel işlem risk limitlerini düzenle" title="Manuel işlem risk limitleri"><SlidersHorizontal aria-hidden="true"/>Düzenle</button>}</div>
        <div className="pvTile"><small>{summary.exposureLimit !== null ? 'Büyüklük / sistem sınırı' : 'Toplam büyüklük'}</small>
          <b>{summary.notional === null ? '—' : summary.exposureLimit !== null ? `${formatUsdt(summary.notional)} / ${formatUsdt(summary.exposureLimit)}` : `${formatUsdt(summary.notional)} USDT`}</b>
          <em>{summary.margin !== null ? `Teminat ${formatUsdt(summary.margin)} USDT` : 'Teminat bilgisi yok'}</em>
          {summary.exposurePct !== null && <span className="pvBar" data-high={summary.exposurePct >= 85 || undefined}><i style={{width: `${Math.min(100, summary.exposurePct)}%`}}/></span>}</div>
        <div className="pvTile"><small>Koruma durumu</small><b data-tone={summary.protectedCount === summary.count ? 'up' : 'warn'}>{`${summary.protectedCount} / ${summary.count} korumalı`}</b>
          <em>{summary.protectedCount === summary.count ? 'Stop ve hedefler Binance’te' : 'Doğrulanamayan pozisyon var'}</em></div>
      </div>

      <div className="pvCards">{sorted.map(({key, position, plan, external, confirmed, protection, metrics, livePrice, protectedStop}) => {
        const pnl = metrics.pnl
        const planId = plan && typeof plan.id === 'string' ? plan.id : ''
        // Yalnız sahipliği doğrulanmış bot pozisyonu kapatılabilir (sunucu da aynısını ister); dış pozisyona dokunulmaz.
        const closable = !external && confirmed && planId.length >= 6 && live
        const closableExternal = external && live && Boolean(position.symbol)
        const manual = plan?.source === 'MANUAL'
        const source = external ? 'Dış' : manual ? 'Manuel' : plan?.source === 'ORIGINAL_V2_LIVE' ? 'Kais Original' : 'Bot'
        // İnce kart: varsayılan kapalı; yalnız stopa çok yakın pozisyon kendiliğinden açılır.
        const isOpen = open[key] ?? false
        const insights = external ? [] : positionInsights(metrics, plan, protectedStop)
        const breakeven = plan?.breakeven_done === true || plan?.original_tp1_complete === true
        const smart = !external && props.smartEnabled ? smartFor(plan, String(position.symbol ?? '')) : undefined
        const flag = (ok: boolean | undefined, name: string, withBinance = false) => <span className="pvFlag" data-ok={ok === true || undefined}>{ok === true ? `${name} ✓${withBinance ? ' Binance’te' : ''}` : `${name} doğrulanamadı`}</span>
        const levels = Array.isArray(plan?.targets) ? (plan?.targets as unknown[]).map(value => String(value ?? '')) : []
        const manualTarget: PositionManualTarget = {planId, symbol: String(position.symbol || ''), direction: String(position.direction || ''), entry: String(position.entry_price ?? '—'), pnl,
          stop: String(plan?.stop_loss ?? ''), targets: [levels[0] ?? '', levels[1] ?? '', levels[2] ?? ''], mark: metrics.mark !== null ? formatPrice(metrics.mark) : String(position.mark_price ?? '—')}
        const opened = metrics.openedMs !== null ? duration((now - metrics.openedMs) / 1000) : null
        const risk = metrics.riskAtStop
        const toggle = () => setOpen(current => ({...current, [key]: !isOpen}))
        const askClose = () => props.onClose({planId: external ? '' : planId, symbol: String(position.symbol || ''), direction: String(position.direction || ''), entry: String(position.entry_price ?? '—'), pnl,
          long: metrics.long, entryPrice: metrics.entry, quantity: metrics.quantity})
        // İnce çubuk: fiyatın stop (sol) ile TP1 (sağ) arasındaki yeri. TP1 sonrası / bilinmiyorsa çubuk yok.
        const tp1Left = metrics.toTp1Pct !== null && !breakeven ? metrics.toTp1Pct : null
        const score = external ? null : scoreInfo(plan, smartFor(plan, String(position.symbol ?? '')))
        const scoreTime = score?.candle ? new Date(score.candle * 1000).toLocaleTimeString('tr-TR', {hour: '2-digit', minute: '2-digit'}) : null
        const progress = external || metrics.toStopPct === null || tp1Left === null ? null
          : tp1Left <= 0 ? 100 : Math.max(0, Math.min(100, metrics.toStopPct / (metrics.toStopPct + tp1Left) * 100))
        const status = external ? 'Dış pozisyon' : metrics.level === 'danger' ? 'Stopa çok yakın' : metrics.level === 'warn' ? 'Stopa yakın' : breakeven ? 'Stop girişte' : protectedStop ? 'Korumalı' : 'Koruma doğrulanamadı'
        const statusTone = external ? undefined : metrics.level === 'danger' ? 'down' : metrics.level === 'warn' || !protectedStop ? 'warn' : undefined
        return <article className="pvCard" key={key} data-pos-symbol={metrics.symbol.toUpperCase()} data-level={metrics.level} data-open={isOpen || undefined} data-external={external || undefined}>
          <button type="button" className="pvSlim" aria-expanded={isOpen} aria-label={`${metrics.coin || 'Pozisyon'} ${isOpen ? 'detayını gizle' : 'detayını göster'}`} onClick={toggle}>
            <span className="pvSlimRow">
              <span className="pvCoin" aria-hidden="true">{metrics.coin.slice(0, 4)}</span>
              <span className="pvSlimId">
                <span className="pvSlimName"><b>{metrics.coin || '—'}</b><em data-dir={metrics.direction}>{`${metrics.direction || '—'}${metrics.leverage !== null ? ` ${metrics.leverage}x` : ''}`}</em></span>
                <small><span>{opened ?? source}</span><span data-tone={statusTone}>{status}</span></small>
              </span>
              <span className="pvSlimPnl"><b data-tone={tone(pnl)}>{pnl === null ? '—' : formatUsdt(pnl, true)}</b><small>{metrics.rMultiple !== null ? `USDT net · ${formatR(metrics.rMultiple)}` : 'USDT net'}{livePrice ? '' : ' · gecikmeli'}</small></span>
              <ChevronDown className="pvSlimChev" aria-hidden="true"/>
            </span>
            {progress !== null && <span className="pvThin" data-level={metrics.level} aria-hidden="true"><i style={{width: `${progress}%`}}/><em style={{left: `${progress}%`}}/></span>}
            {!external && (metrics.toStopPct !== null || tp1Left !== null || score) && <span className="pvSlimFoot">
              <span>{metrics.toStopPct !== null ? (breakeven ? 'Stop girişte' : `Stop ${formatPct(metrics.toStopPct, 1)}`) : ''}</span>
              {score && <span className="pvSlimScore" data-tone={score.tone} title="Güncel güven puanı (son kapanmış 15 dk mum)">{score.mismatch
                ? 'Ters sinyal'
                : <>Güven <b>{score.now}</b>{score.delta ? <em data-dir={score.delta > 0 ? 'up' : 'down'}>{score.delta > 0 ? '▲' : '▼'}{Math.abs(score.delta)}</em> : null}</>}</span>}
              <span>{tp1Left !== null ? (tp1Left > 0 ? `TP1 ${formatPct(tp1Left, 1)}` : 'TP1 geçildi') : ''}</span>
            </span>}
          </button>

          {/* Kapat tuşu kart kapalıyken de görünür; dokununca her zamanki onay penceresi açılır. */}
          {(closable || closableExternal) && <div className="pvQuick">
            <span>{metrics.mark !== null ? <>Kapanış ≈ <b>{formatPrice(metrics.mark)}</b>{livePrice ? null : <em> · gecikmeli</em>}</> : null}</span>
            <button type="button" className="pvQuickClose" disabled={busy} aria-label={`${metrics.coin || 'Pozisyonu'} kapat`} onClick={askClose}><X aria-hidden="true"/>Kapat</button>
          </div>}

          <div className="pvDetail">
            {metrics.ladder && !external && <LadderView ladder={metrics.ladder} metrics={metrics}/>}
            {closable && manual && props.onCloseTarget && <CloseTargetBox planId={planId} metrics={metrics} plan={plan} busy={busy} onSet={props.onCloseTarget}/>}
            {!external && <div className="pvKeys">
              <div data-tone={metrics.level === 'ok' ? 'down' : 'warn'}><small>Stopa kalan</small><b>{metrics.toStopPct !== null ? formatPct(metrics.toStopPct) : '—'}</b></div>
              <div data-tone="up"><small>{breakeven ? 'Sonraki hedef' : 'TP1’e kalan'}</small><b>{metrics.toTp1Pct !== null && metrics.toTp1Pct > 0 && !breakeven ? formatPct(metrics.toTp1Pct) : '—'}</b></div>
              <div data-tone={metrics.toLiqPct !== null && metrics.toLiqPct < 15 ? 'warn' : undefined}><small>Likidasyon</small><b>{metrics.toLiqPct !== null ? formatPct(metrics.toLiqPct, 0) : '—'}</b></div>
            </div>}
            <div className="pvMeta">
              <Cell label="Giriş" value={metrics.entry !== null ? formatPrice(metrics.entry) : null}/>
              <Cell label="Büyüklük" value={metrics.quantity !== null ? `${metrics.quantity.toLocaleString('tr-TR', {maximumFractionDigits: 6})} ${metrics.coin}` : null}/>
              <Cell label="Teminat" value={metrics.margin !== null ? `${formatUsdt(metrics.margin)} USDT` : null}/>
            </div>

            {!external && <div className="pvProt" aria-label="Koruma emirleri">
              <span className="pvChip" data-kind={manual ? 'manual' : 'bot'}>{source}</span>{flag(protection?.stop, 'Stop', true)}{flag(protection?.tp1, `TP1 %${props.tp1Share(plan)}`)}{manual && flag(protection?.tp2, `TP2 %${props.tp2Share(plan)}`)}{flag(protection?.tp3, 'TP3')}
            </div>}

            {(insights.length > 0 || smart || score) && <details className="pvIns"><summary>Akıllı notlar<em>{insights.length + (smart ? 1 : 0) + (score ? 1 : 0)}</em><ChevronDown aria-hidden="true"/></summary>
              {score && <p data-tone={score.tone === 'up' ? 'info' : 'warn'}>{score.mismatch
                ? `Strateji şu an ters yönü gösteriyor${score.opposite !== null ? ` (karşı yön güveni ${score.opposite})` : ''}.`
                : score.now !== null && score.now < score.threshold
                  ? `Güven puanı ${score.now}; giriş eşiği ${score.threshold}.${scoreTime ? ` Son ölçüm ${scoreTime} mumu.` : ''}`
                  : `Güven puanı ${score.now}; giriş eşiğinin (${score.threshold}) üstünde.${scoreTime ? ` Son ölçüm ${scoreTime} mumu.` : ''}`}</p>}
              {insights.map(item => <p key={item.text} data-tone={item.tone}>{item.text}</p>)}
              {smart && <p data-tone="info">{`Akıllı Kâr Koruma · ${SMART_MODE[smart.mode ?? 'NORMAL'] ?? smart.mode}${smart.last_score !== undefined && smart.last_score !== null ? ` · puan ${smart.last_score}` : ''}`}</p>}
            </details>}

            {external && <p className="pvNote">Bu pozisyonun sistemde planı yok: stop ve hedef takip edilmiyor. Kapat ile hemen kapatabilirsin; Binance'taki kendi stop/TP emirlerin varsa onları kendin iptal et.</p>}
            {!external && !confirmed && <p className="pvNote" data-tone="warn">Plan sahipliği doğrulanamadı.</p>}

            {closable && manual && <div className="pvActs">
              <>
                <button type="button" className="lcBtn" disabled={busy || protection?.stop !== true} onClick={() => props.onEdit(manualTarget)}>Stop / hedef değiştir</button>
                <span className="pvPct" role="group" aria-label="Kısmi kapat">{([25, 50, 75] as const).map(percent =>
                  <button type="button" key={percent} className="lcBtn" disabled={busy || protection?.stop !== true} aria-label={`Pozisyonun %${percent}’ini kapat`} onClick={() => props.onReduce(manualTarget, percent)}>{`%${percent}`}</button>)}</span>
              </>
            </div>}
            <button type="button" className="pvToggle" aria-expanded={isOpen} onClick={toggle}>Gizle<ChevronDown aria-hidden="true"/></button>
          </div>
        </article>
      })}</div>

      <div className="pvTable" role="table" aria-label="Pozisyon tablosu">
        <div className="pvRow pvRowHead" role="row">{['COIN', 'YÖN', 'BÜYÜKLÜK', 'GİRİŞ', 'FİYAT', 'STOPA', 'TP1’E', 'NET K/Z', 'R'].map(label => <span key={label} role="columnheader">{label}</span>)}</div>
        {sorted.map(({key, position, metrics}) => <div className="pvRow" role="row" key={key} data-level={metrics.level}>
          <b role="cell">{metrics.coin}</b>
          <span role="cell" data-tone={metrics.long ? 'up' : 'down'}>{metrics.direction}</span>
          <span role="cell">{metrics.quantity !== null ? `${metrics.quantity.toLocaleString('tr-TR', {maximumFractionDigits: 4})}${metrics.notional !== null ? ` · ${formatUsdt(metrics.notional).split(',')[0]} USDT` : ''}` : '—'}</span>
          <span role="cell">{formatPrice(num(position.entry_price))}</span>
          <span role="cell">{formatPrice(metrics.mark)}</span>
          <span role="cell" data-tone={metrics.level === 'ok' ? undefined : 'warn'}>{formatPct(metrics.toStopPct)}</span>
          <span role="cell">{metrics.toTp1Pct !== null && metrics.toTp1Pct > 0 ? formatPct(metrics.toTp1Pct) : '—'}</span>
          <b role="cell" data-tone={tone(metrics.pnl)}>{formatUsdt(metrics.pnl, true)}</b>
          <b role="cell" data-tone={tone(metrics.rMultiple)}>{metrics.rMultiple === null ? '—' : formatUsdt(metrics.rMultiple, true)}</b>
        </div>)}
        <p className="pvTableNote">Ayrıntı ve işlemler (kapat, stop/hedef değiştir) için Kart görünümünü kullan.</p>
      </div>
    </div>}
  </section>
}
