// Coinbase işlem performansı: kapanan işlemlerin komisyon dahil net sonucu, kazanma oranı, coin dağılımı ve Kais önerisi / elle karşılaştırması.
// Yalnızca gösterim; veri GET /api/coinbase/stats'tan gelir (salt okunur). Kapanan işlem yoksa ya da rota yoksa kart hiç görünmez.
import {useEffect, useState} from 'react'
import {BarChart3, ChevronDown} from 'lucide-react'
import {CoinbaseApiError, coinbaseCall} from './coinbase-manual-api'
import {exitText, fmtMoney, moneyTone, parseMoney, rateOf, readStats, sourceVerdict, weakHourNote, type StatsData} from './coinbase-stats'

export default function CoinbaseStats({refreshKey}: {refreshKey: number}) {
  const [data, setData] = useState<StatsData | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    let alive = true
    void coinbaseCall<unknown>('/stats')
      .then(raw => { if (alive) setData(readStats(raw)) })
      .catch(error => { if (alive && !(error instanceof CoinbaseApiError)) setData(null) })   // sunucu henüz güncel değilse (404) ya da hata varsa kart sessizce gizli kalır
    return () => { alive = false }
  }, [refreshKey])
  if (!data) return null
  const net = parseMoney(data.net)
  const verdict = sourceVerdict(data.by_source)
  const exits = exitText(data.by_exit)
  const weak = weakHourNote(data.insights, new Date())
  return <section className="cbStats" aria-label="İşlem performansı" data-open={open || undefined}>
    <button type="button" className="cbStatsHead" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span className="cbStatsIcon"><BarChart3 aria-hidden="true"/></span>
      <span className="cbStatsTitle"><b>İşlem performansı</b><small>Toplam net · {data.trades} kapanan işlem · komisyon dahil</small></span>
      <b className="cbStatsNet" data-tone={moneyTone(net)}>{fmtMoney(net)}</b>
      <ChevronDown aria-hidden="true"/>
    </button>
    {weak && <p className="cbStatsNote cbStatsWeak" role="status">{weak}</p>}
    {open && <div className="cbStatsBody">
      <dl className="cbStatsGrid">
        <div><dt>Kazanma oranı</dt><dd>%{data.win_rate ?? 0}<small>{data.wins} kazanç · {data.losses} kayıp</small></dd></div>
        <div><dt>Ortalama kazanç</dt><dd data-tone="up">{fmtMoney(parseMoney(data.avg_win))}</dd></div>
        <div><dt>Ortalama kayıp</dt><dd data-tone="down">{fmtMoney(parseMoney(data.avg_loss))}</dd></div>
        <div><dt>Ödenen komisyon</dt><dd>{fmtMoney(parseMoney(data.fees), false)}</dd></div>
        {data.profit_factor !== null && <div><dt>Kâr faktörü</dt><dd>{data.profit_factor.toFixed(2)}<small>{data.profit_factor >= 1 ? 'kazançlar kayıpları aşıyor' : 'kayıplar kazançları aşıyor'}</small></dd></div>}
        {exits && <div><dt>Nasıl kapandı</dt><dd className="cbStatsExit">{exits}</dd></div>}
      </dl>
      {data.by_coin.length > 0 && <ul className="cbStatsCoins" aria-label="Coin bazında sonuç">{data.by_coin.map(row => {
        const value = parseMoney(row.net)
        return <li key={row.coin}><b>{row.coin}</b><span>{row.trades} işlem · %{rateOf(row) ?? 0}</span><em data-tone={moneyTone(value)}>{fmtMoney(value)}</em></li>
      })}</ul>}
      <div className="cbStatsSrc" data-ready={verdict.ready || undefined}>
        <b>Kais önerisi mi, elle mi?</b>
        <div><span>Kais önerisiyle<small>{data.by_source.kais.trades} işlem</small></span><em data-tone={moneyTone(parseMoney(data.by_source.kais.net))}>{fmtMoney(parseMoney(data.by_source.kais.net))}</em></div>
        <div><span>Elle<small>{data.by_source.manual.trades} işlem</small></span><em data-tone={moneyTone(parseMoney(data.by_source.manual.net))}>{fmtMoney(parseMoney(data.by_source.manual.net))}</em></div>
        <p>{verdict.text}</p>
      </div>
      {data.insights && data.insights.insights.length > 0 && <div className="cbStatsSrc" aria-label="Senin kalıpların">
        <b>Senin kalıpların</b>
        {data.insights.insights.map(item => <p key={item.text} data-tone={item.tone}>{item.text}</p>)}
        <small className="cbStatsNote">Geçmiş işlemlerinin dağılımıdır; neden-sonuç göstermez.</small>
      </div>}
      {data.unknown > 0 && <p className="cbStatsNote">{data.unknown} kapanışın giriş kaydı geçmişte kalmış olduğu için sayıya katılmadı.</p>}
      <p className="cbStatsNote">Rakamlar Coinbase işlem kayıtlarından hesaplanır; fonlama ücreti dahil değildir.</p>
    </div>}
  </section>
}
