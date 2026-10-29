import {Activity, BarChart3, Cable, Wallet} from 'lucide-react'
import './master-trade-analysis-v2.css'
import './live-modern.css'
import './live-terminal.css'
import './live-mobile.css'
import './positions-mobile.css'
import './master-status-strip.css'
import './live-ws.css'
import './live-cockpit.css'
import './live-cockpit-mobile.css'
import './demo-master.css'

// Demo İşlem Masası, Master Trade sayfasının görünümünü kullanır. Yalnız görünüm: demo motoru ve
// demo emir akışı (BinanceDemo) aynen kalır; canlı motora hiçbir istek buradan gitmez.
export type DemoMasterTab = 'analiz' | 'canli' | 'pozisyonlar' | 'baglanti'
export const DEMO_MASTER_TABS = [
  {id: 'analiz', label: 'Analiz', icon: BarChart3},
  {id: 'canli', label: 'Demo İşlem', icon: Activity},
  {id: 'pozisyonlar', label: 'Pozisyonlar', icon: Wallet},
  {id: 'baglanti', label: 'Bağlantı', icon: Cable},
] as const

// Üst kısım her sekmede Master'ın Canlı İşlem görünümünü kullanır (Master'ın Analiz düzeni demo içeriğine uymuyor).
export default function DemoMasterShell({tab, onTab, connected, lastUpdate, dataHealth}: {
  tab: DemoMasterTab
  onTab: (tab: DemoMasterTab) => void
  connected: boolean | null
  lastUpdate: string
  dataHealth: string
}) {
  const connection = connected === null ? 'Okunuyor' : connected ? 'Bağlı' : 'Bağlı değil'
  return <section className="masterTradePage masterTrade masterLayoutV2 demoMasterPage" data-layout-tab="canli" data-demo-tab={tab} aria-label="Demo İşlem Masası" data-i18n-skip="">
    <div className="masterTradeShell">
      <header className="masterTradeTerminalHeader">
        <div className="masterTradeTerminalIdentity"><span>BINANCE FUTURES DEMO</span><strong>DEMO İŞLEM MASASI</strong></div>
      </header>
      <div className="terminalStatusStrip" data-mode="demo" data-connected={connected ? 'true' : 'false'} aria-label="Demo bağlantı durumu">
        <span className={`terminalStatusItem${connected ? ' online' : ''}`}><i /> {connection}</span>
        <span className="terminalStatusItem"><small>WS</small> --</span>
        <span className="terminalStatusItem"><small>Gecikme</small> --</span>
        <span className="terminalStatusItem"><small>Son güncelleme</small> {lastUpdate}</span>
        <span className="terminalStatusItem"><small>Veri sağlığı</small> {dataHealth}</span>
        <span className="terminalStatusItem online">Demo hesap</span>
      </div>
      <div className="masterTradeSticky">
        <nav className="masterTradeTabs" aria-label="Demo İşlem Masası" role="tablist">{DEMO_MASTER_TABS.map(({id, label, icon: Icon}) =>
          <button key={id} type="button" id={`demo-tab-${id}`} role="tab" aria-selected={tab === id} onClick={() => onTab(id)}><Icon aria-hidden="true"/><span>{label}</span></button>)}
        </nav>
      </div>
    </div>
  </section>
}
