import './coinbase-switch.css'

export type Exchange = 'binance' | 'coinbase'

const OPTIONS: {id: Exchange; name: string; note: string}[] = [
  {id: 'binance', name: 'Binance', note: 'USDT-M Futures'},
  {id: 'coinbase', name: 'Coinbase', note: 'ABD Vadeli · manuel'},
]

/** Borsa seçici: aynı ekranlar, hangi borsanın arka planda çalışacağını seçer. Master Trade sekme çubuğuyla aynı görünüm. */
export default function CoinbaseExchangeSwitch({value, onChange}: {value: Exchange; onChange: (next: Exchange) => void}) {
  return <div className="exSwitch" role="radiogroup" aria-label="Borsa seçimi">
    {OPTIONS.map(option => <button key={option.id} type="button" role="radio" aria-checked={value === option.id} data-exchange={option.id}
      onClick={() => onChange(option.id)}>
      <i aria-hidden="true"/>
      <span><b>{option.name}</b><small>{option.note}</small></span>
    </button>)}
  </div>
}
