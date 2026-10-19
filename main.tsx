import React from 'react'
import ReactDOM from 'react-dom/client'
import TestnetFirstApp from './TestnetFirstApp'
import AppErrorBoundary from './AppErrorBoundary'
import WebAccessGate from './WebAccessGate'
import AuthGate from './AuthGate'
import EmailVerificationRoute from './EmailVerificationRoute'
import PublicPolicyPage from './PublicPolicyPage'
import { publicPolicyForPath } from './compliance-content'
import {applyPageMetadataForPath} from './public-page-metadata'
import CopyProtection from './CopyProtection'
import BackToTop from './BackToTop'
import LanguageToggle from './LanguageToggle'
import { initI18n } from './i18n'
import { MemberAccessProvider } from './premium-access'
import { installAuthorizedFetch, installErrorMonitoring } from './api'
import './frontend/src/binance-demo.css'
import './tokens.css'
import './master-trade-live.css'
import './terminal-theme.css'
import './subscription.css'
import './coin-analysis.css'
import './scanner-modern.css'
import './theme.css'
import './mobile-premium.css'
import './branding-overrides.css'
import './back-to-top.css'
import './market-picker-modern.css'

import { reloadForNewVersion } from './error-visibility'

// Vercel dosya önceliği: admin.* alan adında "/" (ve "/index.html") istekleri yönlendirme kuralına ulaşmadan bu müşteri
// sayfasını açar. Yönetim alanında müşteri uygulaması hiç çalışmaz; "/admin"e geçilir (orada admin.html sunulur).
const adminHost = /^admin\./i.test(window.location.hostname)
if (adminHost) {
  window.location.replace(`/admin${window.location.search}${window.location.hash}`)
} else {
  installAuthorizedFetch()
  window.addEventListener('vite:preloadError', event => { if (reloadForNewVersion()) event.preventDefault() })
  applyPageMetadataForPath(window.location.pathname)
  installErrorMonitoring()
  const publicPolicy = publicPolicyForPath(window.location.pathname)
  const application = publicPolicy
    ? <><PublicPolicyPage policy={publicPolicy}/><LanguageToggle/></>
    : <><CopyProtection/><BackToTop/><WebAccessGate><AuthGate><MemberAccessProvider><TestnetFirstApp/></MemberAccessProvider></AuthGate></WebAccessGate></>
  void initI18n().then(() => {
    ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><AppErrorBoundary><EmailVerificationRoute>{application}</EmailVerificationRoute></AppErrorBoundary></React.StrictMode>)
  })
}
