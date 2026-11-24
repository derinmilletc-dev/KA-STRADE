import '../../manual-analysis-sheet.css'
import SmartRead from './SmartRead'
import CoinbaseAlerts from './CoinbaseAlerts'
import EntryWatch from './EntryWatch'
import {binanceAlertCall, usePriceAlerts} from './price-alert-api'

// Coin Analist sayfasında seçili coinin altına: akıllı yorum (seçili süreye odaklı) + giriş fırsatı alarmı + fiyat alarmı.
// Yalnız okuma ve bildirim: emir göndermez.
type Props = {symbol: string; display: string; price: number | null; interval: string}

export default function AnalystInsight({symbol, display, price, interval}: Props) {
  const {alerts, reload} = usePriceAlerts(binanceAlertCall)
  const num = Number(price)
  return <div className="mxai">
    <SmartRead symbol={symbol} focus={interval} alert={{productId: symbol, call: binanceAlertCall, onSet: reload}}/>
    <EntryWatch symbol={symbol} display={display}/>
    <CoinbaseAlerts productId={symbol} display={display} price={Number.isFinite(num) && num > 0 ? num : null} alerts={alerts} onChanged={reload} call={binanceAlertCall}/>
  </div>
}
