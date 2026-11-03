import {useEffect, useState} from 'react'
import {ArrowRight, ShieldCheck} from 'lucide-react'
import {API_BASE} from './api'
import './founding-resume.css'

type Status = 'account_created' | 'payment_pending' | 'active' | string | null

/** Davetle hesap açıp üyeliğini bitirmemiş kurucu üyeye 'kaldığın yerden devam et' bandı. */
export default function FoundingResume({onBilling}: {onBilling: () => void}) {
  const [status, setStatus] = useState<Status>(null)
  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API_BASE}/v22/founding/me`, {credentials: 'same-origin', signal: controller.signal})
      .then(response => response.ok ? response.json() : null)
      .then(body => { if (body && typeof body.status === 'string') setStatus(body.status) })
      .catch(() => { /* bant isteğe bağlı */ })
    return () => controller.abort()
  }, [])
  if (status !== 'payment_pending' && status !== 'account_created') return null
  const payment = status === 'payment_pending'
  return <section className="fdResume" role="status" aria-label="Kurucu üyelik">
    <ShieldCheck aria-hidden="true"/>
    <span><b>{payment ? 'Kurucu üyeliğini tamamla' : 'Güvenlik adımını tamamla'}</b>
      <small>{payment ? 'Hesabın ve 2FA hazır. Ödemen doğrulandığında üyeliğin otomatik açılır.' : 'Üyeliğine devam etmek için iki aşamalı doğrulamayı (2FA) etkinleştir.'}</small></span>
    {payment && <button type="button" onClick={onBilling}>Ödemeye geç<ArrowRight aria-hidden="true"/></button>}
  </section>
}
