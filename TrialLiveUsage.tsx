import {useEffect, useState} from 'react'
import {RefreshCw} from 'lucide-react'
import './trial-live-usage.css'

type Item = {user_id: string; email: string; today: number; week: number; limit_reached: boolean}
type Data = {enabled: boolean; daily_limit: number; items: Item[]}
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

function parse(value: unknown): Data {
  if (!isRecord(value) || typeof value.enabled !== 'boolean' || typeof value.daily_limit !== 'number' || !Array.isArray(value.items)) throw new Error('Deneme canlı işlem listesi doğrulanamadı.')
  const items = value.items.map((row: unknown): Item => {
    if (!isRecord(row) || typeof row.user_id !== 'string' || typeof row.email !== 'string' || typeof row.today !== 'number'
      || typeof row.week !== 'number' || typeof row.limit_reached !== 'boolean') throw new Error('Deneme canlı işlem kaydı doğrulanamadı.')
    return {user_id: row.user_id, email: row.email, today: row.today, week: row.week, limit_reached: row.limit_reached}
  })
  return {enabled: value.enabled, daily_limit: value.daily_limit, items}
}

/** Deneme üyelerinin elle canlı işlem kullanımı (günde 5 yeni pozisyon). Yalnız izleme. */
export default function TrialLiveUsage({load}: {load: (signal: AbortSignal) => Promise<unknown>}) {
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setBusy(true)
    void load(controller.signal).then(value => {setData(parse(value)); setError('')})
      .catch((exception: unknown) => {if (!controller.signal.aborted) setError(exception instanceof Error ? exception.message : 'Liste alınamadı.')})
      .finally(() => {if (!controller.signal.aborted) setBusy(false)})
    return () => controller.abort()
  }, [attempt, load])
  return <section className="trialLive" aria-label="Deneme canlı işlemleri">
    <header>
      <div><small>{data ? (data.enabled ? 'AÇIK' : 'KAPALI') : 'DENEME'}</small><h2>Deneme canlı işlemleri</h2>
        <p>Ücretsiz deneme üyeleri elle canlı işlemde günde en fazla {data?.daily_limit ?? 5} yeni pozisyon açar (00:00 UTC'de sıfırlanır). Son 7 gün.</p></div>
      <button type="button" onClick={() => setAttempt(value => value + 1)} disabled={busy}><RefreshCw size={14}/> Yenile</button>
    </header>
    {error && <div className="trialLiveError" role="alert">{error}</div>}
    {data && (data.items.length === 0 ? <p className="trialLiveEmpty">Son 7 günde canlı işlem açan deneme üyesi yok.</p> :
      <div className="trialLiveList">{data.items.map(item => <div key={item.user_id} className="trialLiveRow">
        <span className="who"><b>{item.email || item.user_id}</b></span>
        <span className={`count ${item.limit_reached ? 'full' : ''}`}>Bugün {item.today}/{data.daily_limit}{item.limit_reached ? ' · hak doldu' : ''}</span>
        <span className="week">7 gün: {item.week}</span>
      </div>)}</div>)}
  </section>
}
