// Çalan fiyat alarmını uygulama içinde gösterir (telefon bildirimi gelmese ya da kaçırılsa bile). Master Trade açıkken 20 sn'de bir bakar.
import {useEffect} from 'react'
import {showToast} from '../../toast-center'
import {binanceAlertCall, coinbaseAlertCall} from './price-alert-api'
import type {Exchange} from './CoinbaseExchangeSwitch'
import {repairPush} from '../../push-client'
import {loadNotifyPrefs} from '../../notification-prefs'
import {firedText, type FiredAlert} from './coinbase-alert'

export function usePriceAlertToasts(exchange: Exchange): void {
  // Açılışta bir kez: sunucu bu cihazı unutmuşsa aboneliği sessizce yeniden kaydet (bildirimin gelmemesinin en olası sebebi).
  useEffect(() => { void repairPush(loadNotifyPrefs()) }, [])
  useEffect(() => {
    let stopped = false
    const call = exchange === 'coinbase' ? coinbaseAlertCall : binanceAlertCall
    const check = async () => {
      if (stopped || document.visibilityState === 'hidden') return
      try {
        const data = await call<{fired?: FiredAlert[]}>('/alerts')
        const fired = Array.isArray(data.fired) ? data.fired : []
        if (stopped || fired.length === 0) return
        await call('/alerts/ack', {method: 'POST'})
        for (const item of fired.slice(0, 3)) { const text = firedText(item); showToast({title: text.title, body: text.body, tone: 'info', duration: 12000}) }
        if (fired.length > 3) showToast({body: `${fired.length - 3} alarm daha çaldı.`, tone: 'info'})
      } catch { /* oturum/bağlantı yoksa sessiz geç; sonraki turda tekrar */ }
    }
    void check()
    const timer = window.setInterval(() => void check(), 20000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [exchange])
}
