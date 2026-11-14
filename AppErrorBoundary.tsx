import { Component, type ErrorInfo, type ReactNode } from 'react'
import { RefreshCw, Sparkles, WifiOff } from 'lucide-react'
import './app-error.css'
import { reportClientError } from './api'
import { isStaffView, isStaleBuildError, reloadForNewVersion } from './error-visibility'

type Props = { children:ReactNode }
type State = { failed:boolean; message:string; stale:boolean }

export default class AppErrorBoundary extends Component<Props,State> {
  state:State = {failed:false,message:'',stale:false}

  static getDerivedStateFromError(error:unknown):State {
    return {
      failed:true,
      stale:isStaleBuildError(error),
      message:error instanceof Error ? error.message : 'Beklenmeyen bir ekran hatası oluştu.',
    }
  }

  componentDidCatch(error:unknown, info:ErrorInfo) {
    console.error('ProTreBot güvenli ekran koruması', error, info.componentStack)
    if (isStaleBuildError(error) && reloadForNewVersion()) return
    reportClientError(error, {componentStack:info.componentStack})
  }

  private recover = () => {
    this.setState({failed:false,message:'',stale:false})
    window.location.reload()
  }

  render() {
    if (!this.state.failed) return this.props.children
    const stale = this.state.stale
    return <main className="appRecoveryShell errScreen" role="alert">
      <section className="errCard">
        <div className="errIcon" aria-hidden="true"><span className="errIconCore">{stale ? <Sparkles size={30} strokeWidth={1.8}/> : <WifiOff size={30} strokeWidth={1.8}/>}</span></div>
        <span className="errPill"><i aria-hidden="true"/>{stale ? 'Yeni sürüm hazır' : 'Bağlantı sorunu'}</span>
        <h1>{stale ? 'Site güncellendi' : 'Sayfa yüklenemedi'}</h1>
        <p className="errText">{stale ? 'Sitenin yeni bir sürümü yayınlandı. Sayfayı yenilemen yeterli.' : 'Bu bölüm şu anda açılamadı. Hesabın ve işlem kayıtların korunuyor.'}</p>
        <button className="errBtn" type="button" onClick={this.recover}>
          <RefreshCw size={18} aria-hidden="true" />
          Sayfayı yenile
        </button>
        <p className="errHint">{stale ? 'Yenilemeden sonra sorun sürerse tarayıcıda Ctrl+F5 ile tekrar dene.' : 'Sorun sürerse birkaç dakika sonra tekrar dene.'}</p>
        {isStaffView() && <details className="appRecoveryDetails errDetails">
          <summary>Teknik ayrıntılar (yalnızca yönetici)</summary>
          <pre>{this.state.message || 'Veri yeniden istenecek.'}</pre>
        </details>}
      </section>
    </main>
  }
}
