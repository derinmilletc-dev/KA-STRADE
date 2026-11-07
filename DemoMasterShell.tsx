import type {RefCallback} from 'react'
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
import BrandLogo from './BrandLogo'

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
export default function DemoMasterShell({tab, onTab, connected, lastUpdate, dataHealth, assistantSlotRef, topBar = false}: {
  topBar?: boolean
  tab: DemoMasterTab
  onTab: (tab: DemoMasterTab) => void
  connected: boolean | null
  lastUpdate: string
  dataHealth: string
  assistantSlotRef?: RefCallback<HTMLDivElement>
}) {
  const connection = connected === null ? 'Okunuyor' : connected ? 'Bağlı' : 'Bağlı değil'
  return <section className="masterTradePage masterTrade masterLayoutV2 demoMasterPage" data-layout-tab="canli" data-demo-tab={tab} aria-label="Demo İşlem Masası">
    <div className="masterTradeShell">
      {/* Master Trade'in Canlı İşlem başlığıyla aynı yapı; yalnız ad "Demo İşlem". */}
      <header className="masterTradeTerminalHeader">
        <div className="masterTradeTerminalIdentity"><span>KURUMSAL İŞLEM MASASI</span><strong>DEMO İŞLEM</strong></div>
        <span className="masterTradeTerminalMode">DEMO İŞLEMLER · GERÇEK PARA YOK</span>
        {assistantSlotRef && <div className="assistantMasterSlot" ref={assistantSlotRef}/>}
      </header>
      <div className="terminalStatusStrip" data-mode="demo" data-connected={connected ? 'true' : 'false'} aria-label="Demo bağlantı durumu">
        <span className={`terminalStatusItem${connected ? ' online' : ''}`}><i /> {connection}</span>
        <span className="terminalStatusItem"><small>WS</small> --</span>
        <span className="terminalStatusItem"><small>Gecikme</small> --</span>
        <span className="terminalStatusItem"><small>Son güncelleme</small> {lastUpdate}</span>
        <span className="terminalStatusItem"><small>Veri sağlığı</small> {dataHealth}</span>
        <span className="terminalStatusItem online">DEMO HESAP</span>
      </div>
      <div className="masterTradeSticky">
        <nav className="masterTradeTabs" aria-label="Demo İşlem Masası" role="tablist"><img className="masterTradeBrandLogo" src="/kaistrade-logo-trim.png" alt="KaisTrade" width={896} height={216}/>{DEMO_MASTER_TABS.map(({id, label, icon: Icon}) =>
          <button key={id} type="button" id={topBar ? undefined : `demo-tab-${id}`} role="tab" aria-selected={tab === id} onClick={() => onTab(id)}><Icon aria-hidden="true"/><span>{label}</span></button>)}
        </nav>
      </div>
    </div>
  </section>
}

// Bilgisayar görünümünde Analiz dışındaki demo sekmeleri de Analiz sekmesiyle aynı üst çubuğu kullanır (logo, sekmeler, bağlantı rozeti, profil, asistan).
// Bu bölümün varlığı, eski demo başlığı ve sekme şeridini CSS ile gizler (demo-master.css, :has kuralı); sayfa sekmeler arası zıplamaz.
export function DemoTopBar({tab, onTab, connected, assistantSlotRef}: {
  tab: DemoMasterTab
  onTab: (tab: DemoMasterTab) => void
  connected: boolean | null
  assistantSlotRef?: RefCallback<HTMLDivElement>
}) {
  return <section className="masterReferenceAnalysis demoTopBarOnly" data-layout-tab="analiz" aria-label="Demo İşlem üst çubuğu">
    <header className="refHeader masterTradeTerminalHeader">
      <a className="refBrand" href="/" aria-label="Kaistrade ana sayfa"><BrandLogo height={31}/></a>
      <nav role="tablist" aria-label="Demo İşlem Masası">{DEMO_MASTER_TABS.map(({id, label, icon: Icon}) =>
        <button key={id} id={`demo-tab-${id}`} type="button" role="tab" aria-selected={tab === id} onClick={() => onTab(id)}><Icon/>{label}</button>)}
      </nav>
      <span className="refConnection" data-connected={connected === true}><i/>{connected === null ? 'DEMO OKUNUYOR' : connected ? 'DEMO BAĞLI' : 'DEMO BAĞLANTI YOK'}</span>
      <div className="masterTradeProfileSlot" aria-label="Profil menüsü"/>{assistantSlotRef && <div className="assistantMasterSlot" ref={assistantSlotRef}/>}
    </header>
  </section>
}
