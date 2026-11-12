import './coinbase-switch.css'

export type Exchange = 'binance' | 'coinbase'

/** Borsa seçici: aynı ekranlar, hangi borsanın arka planda çalışacağını seçer. */
export default function CoinbaseExchangeSwitch({value, onChange}: {value: Exchange; onChange: (next: Exchange) => void}) {
  return <div className="exSwitch" role="group" aria-label="Borsa seçimi">
    <span>Borsa</span>
    <button type="button" aria-pressed={value === 'binance'} onClick={() => onChange('binance')}>Binance</button>
    <button type="button" aria-pressed={value === 'coinbase'} onClick={() => onChange('coinbase')}>Coinbase</button>
  </div>
}
