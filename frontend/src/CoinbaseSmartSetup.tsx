// Akıllı kurulum kartı: bakiyene, borsa marjinine ve kayıp limitine göre hazır boyutlar. Yalnızca gösterim; seçimi üst bileşen uygular.
import {Zap} from 'lucide-react'
import type {SizeOption, SizeResult} from './coinbase-autosize'

const usd = (value: number) => value.toLocaleString('tr-TR', {minimumFractionDigits: 2, maximumFractionDigits: 2})

export default function CoinbaseSmartSetup({result, source, onPick}: {result: SizeResult; source: string; onPick: (option: SizeOption) => void}) {
  return <section className="cbSmart" aria-label="Akıllı kurulum">
    <div className="cbSmartHead"><Zap aria-hidden="true"/><div className="cbSmartTitle"><b>Akıllı kurulum</b><small>{source}</small></div></div>
    {result.blocked
      ? <p className="cbSmartNone" role="note">{result.blocked}</p>
      : <>
        <div className="cbSmartList" role="group" aria-label="Hazır boyutlar">{result.options.map(option => <button type="button" key={option.id} className="cbSmartOpt" data-warn={option.warn ? '1' : undefined} onClick={() => onPick(option)}>
          <div className="cbSmartName">{option.label}</div>
          <div className="cbSmartMain">{option.contracts} sözleşme · pozisyon {usd(option.notional)} USD</div>
          <div className="cbSmartNums"><div data-tone="down">Stopta −{usd(option.lossUsd)}</div>{option.gainUsd !== null && <div data-tone="up">Hedefte +{usd(option.gainUsd)}</div>}</div>
          {option.warn && <div className="cbSmartWarn">{option.warn}</div>}
        </button>)}</div>
        <p className="cbSmartHint">Birine dokun: teminat, stop ve hedef otomatik dolar, doğrudan son kontrole geçersin. Komisyon dahil.</p>
      </>}
  </section>
}
