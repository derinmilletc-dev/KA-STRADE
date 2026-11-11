import {watchDca} from './dcaWatch'

const fmt = (n: number | null) => n === null ? '—' : String(Number(n.toPrecision(6)))
const usd = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(2)} USDT`

// DCA'nın açtığı pozisyon için canlı izleme: bot şu an ne yapıyor, sıradaki adım ne, hedef ve stop ne kadar uzakta.
export default function DcaWatch({bot, mark}: {bot: unknown; mark: number | null}) {
  const view = watchDca(bot, mark)
  const t = view.track
  return <div className="dwWatch" data-tone={view.tone} aria-label="DCA botu canlı izleme" data-testid="dca-watch">
    <div className="dwWatchHead"><span className="dwWatchDot" aria-hidden="true"/><div><small>BOT ŞU AN</small><b>{view.headline}</b></div></div>
    <p className="dwWatchText">{view.detail}</p>
    {t && <div className="dwTrack" role="img" aria-label="Fiyat çizgisi: stop, ortalama, fiyat, hedef">
      <i className="dwTrackBar"/>
      <i className="dwTrackFill" style={{left: `${Math.min(t.stop, t.tp ?? 100)}%`, width: `${Math.abs((t.tp ?? 100) - t.stop)}%`}}/>
      {t.levels.map((level, index) => <i key={index} className="dwTick" data-on={level.filled || undefined} style={{left: `${level.pos}%`}}/>)}
      <i className="dwPin dwPinStop" style={{left: `${t.stop}%`}}/>
      {t.tp !== null && <i className="dwPin dwPinTp" style={{left: `${t.tp}%`}}/>}
      {t.avg !== null && <i className="dwPin dwPinAvg" style={{left: `${t.avg}%`}}/>}
      {t.mark !== null && <i className="dwPin dwPinNow" style={{left: `${t.mark}%`}}/>}
    </div>}
    <div className="dwTrackKey"><span data-k="stop">Stop</span><span data-k="avg">Ortalama</span><span data-k="now">Fiyat</span><span data-k="tp">Hedef</span></div>
    <div className="dwWatchGrid">
      <span><small>Ek alım</small><b>{view.done} / {view.total}</b></span>
      <span><small>Sıradaki alım</small><b>{view.next !== null ? fmt(view.next) : '—'}</b>{view.nextDistPct !== null && <em>%{view.nextDistPct.toFixed(2)} uzakta</em>}</span>
      <span><small>Kâr hedefi</small><b data-tone="up">{fmt(view.tp)}</b>{view.tpDistPct !== null && <em>%{view.tpDistPct.toFixed(2)} kaldı</em>}</span>
      <span><small>Stop</small><b data-tone="down">{fmt(view.stop)}</b>{view.stopDistPct !== null && <em>%{view.stopDistPct.toFixed(2)} kaldı</em>}</span>
    </div>
    <p className="dwWatchFoot">Tur {view.cycle}{view.completed > 0 ? ` · ${view.completed} bitti` : ''} · Gerçekleşen <b data-tone={view.realized >= 0 ? 'up' : 'down'}>{usd(view.realized)}</b></p>
    {view.note && <p className="dwWatchNote">{view.note}</p>}
  </div>
}
