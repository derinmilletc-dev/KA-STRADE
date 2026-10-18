import { Component, type ErrorInfo, type ReactNode } from 'react'
import { RefreshCw, ShieldCheck } from 'lucide-react'
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
    return <main className="appRecoveryShell" role="alert">
      <section className="appRecoveryCard">
        <span className="appRecoveryIcon" aria-hidden="true"><ShieldCheck size={34} strokeWidth={1.7} /></span>
        <small className="appRecoveryEyebrow">SECURE RECOVERY</small>
        <h1>{stale ? 'Site güncellendi' : 'Sayfa yüklenemedi'}</h1>
        <p>{stale ? 'Sitenin yeni bir sürümü yayınlandı. Sayfayı yenilemen yeterli.' : 'Bu bölüm şu anda açılamadı. Hesabın ve işlem kayıtların korunuyor.'}</p>
        <p className="appRecoveryHint">{stale ? 'Yenilemeden sonra sorun sürerse tarayıcıda Ctrl+F5 ile tekrar dene.' : 'Sayfayı yenile. Sorun sürerse birkaç dakika sonra tekrar dene.'}</p>
        <button className="appRecoveryAction" type="button" onClick={this.recover}>
          <RefreshCw size={16} aria-hidden="true" />
          Paneli yeniden yükle
        </button>
        {isStaffView() && <details className="appRecoveryDetails">
          <summary>Teknik ayrıntılar (yalnızca yönetici)</summary>
          <pre>{this.state.message || 'Veri yeniden istenecek.'}</pre>
        </details>}
      </section>
    </main>
  }
}
