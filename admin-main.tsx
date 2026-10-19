import React from 'react'
import ReactDOM from 'react-dom/client'
import AppErrorBoundary from './AppErrorBoundary'
import AuthGate from './AuthGate'
import { initI18n } from './i18n'
import { installAuthorizedFetch, installErrorMonitoring } from './api'
import './tokens.css'
import './theme.css'

/** Yönetim yüzeyi: yalnızca giriş + admin/moderatör panelleri. Müşteri uygulaması (TestnetFirstApp, piyasa, analiz) bu pakete girmez. */
installAuthorizedFetch()
installErrorMonitoring()
void initI18n().then(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode><AppErrorBoundary><AuthGate surface="admin">{null}</AuthGate></AppErrorBoundary></React.StrictMode>,
  )
})
