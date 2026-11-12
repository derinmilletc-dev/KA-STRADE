// Coinbase sekmesi görünsün mü? Sunucu cevabına bakılır: kapalıysa (404) ya da izinli değilse (401/403) hiç görünmez.
import {useEffect, useState} from 'react'
import {fetchWithTimeout} from '../../master-trade-request'
import {API_BASE} from './api'
import {tabVisible} from './coinbase-manual'

export function useCoinbaseVisible(): boolean {
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    let active = true
    void (async () => {
      try {
        const response = await fetchWithTimeout(`${API_BASE}/coinbase/status`, {}, 10000)
        let body: unknown = null
        try { body = await response.json() } catch { body = null }
        if (active) setVisible(tabVisible(response.status, body))
      } catch {
        // Ağ hatasında sekme gösterilmez; ayrı bir özellik olduğu için ana ekranı bozmaz. Sayfa yenilenince tekrar denenir.
        if (active) setVisible(false)
      }
    })()
    return () => { active = false }
  }, [])
  return visible
}
