import {useEffect, useMemo, useRef, useState} from 'react'
import {BookOpen, X} from 'lucide-react'
import {currentLang} from './i18n'
import {analysisPrice} from './master-trade-presentation'
import {summarizeJournal, tx, type JournalEntry} from './studio-quick-trade'

const locale = () => currentLang() === 'en' ? 'en-US' : 'tr-TR'
const fmt = (value: number, digits = 2) => value.toLocaleString(locale(), {minimumFractionDigits: digits, maximumFractionDigits: digits})
const when = (ts: number) => new Date(ts).toLocaleString(locale(), {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'})

/** Köşedeki küçük günlük düğmesi (GRID çipinin yanında). */
export function JournalChip({count, onOpen}: {count: number; onOpen: () => void}) {
  return <button type="button" className="sqjChip" aria-label={tx(`İşlem günlüğü (${count} kayıt)`, `Trade journal (${count} entries)`)} aria-haspopup="dialog" onClick={onOpen}>
    <BookOpen aria-hidden="true"/>{count > 0 && <em>{count > 99 ? '99+' : count}</em>}
  </button>
}

function NoteField({entry, onNote}: {entry: JournalEntry; onNote: (id: string, note: string) => void}) {
  const [text, setText] = useState(entry.note)
  useEffect(() => { setText(entry.note) }, [entry.note])
  return <textarea className="sqjNote" rows={1} maxLength={280} value={text} placeholder={tx('Not: neden girdin, ne öğrendin?', 'Note: why you entered, what you learned?')}
    aria-label={tx(`${entry.symbol} işlem notu`, `${entry.symbol} trade note`)} onChange={event => setText(event.target.value)} onBlur={() => { if (text !== entry.note) onNote(entry.id, text) }}/>
}

/** İşlem günlüğü: Studio'dan gönderilen emirler (yalnız bu tarayıcıda saklanır). Plan, risk ve analize uyum görünür; not eklenebilir. */
export function JournalPanel({entries, onNote, onClear, onClose}: {entries: readonly JournalEntry[]; onNote: (id: string, note: string) => void; onClear: () => void; onClose: () => void}) {
  const [confirmClear, setConfirmClear] = useState(false)
  const closeRef = useRef<HTMLButtonElement>(null)
  const summary = useMemo(() => summarizeJournal(entries), [entries])
  const closeFn = useRef(onClose)
  closeFn.current = onClose
  // Odak yalnız açılırken düğmeye gider; üst bileşen her saniye yeniden çizilse de yazarken odak çalınmaz.
  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); closeFn.current() } }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [])
  return <aside className="sqgPanel sqjPanel" role="dialog" aria-label={tx('İşlem günlüğü', 'Trade journal')}>
    <header className="sqgHead"><BookOpen aria-hidden="true"/><b>{tx('GÜNLÜK', 'JOURNAL')}</b>
      <button ref={closeRef} type="button" className="sqgX" style={{marginLeft: 'auto'}} aria-label={tx('Günlüğü kapat', 'Close the journal')} onClick={onClose}><X aria-hidden="true"/></button></header>
    <div className="sqgBody">
      {entries.length === 0
        ? <p className="sqgEmpty">{tx('Henüz kayıt yok. Studio’dan gönderdiğin her emir burada plan ve riskiyle birlikte listelenir.', 'No entries yet. Every order you send from the Studio is listed here with its plan and risk.')}</p>
        : <>
          <dl className="sqgStats sqjSummary">
            <div><dt>{tx('Kayıt', 'Entries')}</dt><dd>{summary.count}</dd></div>
            <div><dt>{tx('Analize uyum', 'Followed analysis')}</dt><dd>{summary.followedPct === null ? '—' : `%${fmt(summary.followedPct, 0)}`}</dd></div>
            <div><dt>{tx('Ort. risk', 'Avg. risk')}</dt><dd>{summary.avgRisk === null ? '—' : `${fmt(summary.avgRisk)} USDT`}</dd></div>
            <div><dt>{tx('Ort. R/Ö', 'Avg. R/R')}</dt><dd>{summary.avgRewardRisk === null ? '—' : `1 : ${fmt(summary.avgRewardRisk, 1)}`}</dd></div>
          </dl>
          <ul className="sqjList">{entries.map(entry => <li key={entry.id} className="sqjRow" data-side={entry.side}>
            <div className="sqjTop"><b>{entry.symbol.replace(/USDT$/, '')}</b>
              <span className="sqjTag" data-side={entry.side}>{entry.side === 'LONG' ? tx('AL', 'BUY') : tx('SAT', 'SELL')}{entry.kind === 'LIMIT' ? ' · Limit' : ''}</span>
              <span className="sqjAcc" data-demo={entry.account === 'DEMO' || undefined}>{entry.account === 'DEMO' ? 'DEMO' : tx('CANLI', 'LIVE')}</span>
              <time dateTime={new Date(entry.ts).toISOString()}>{when(entry.ts)}</time></div>
            <p className="sqjPlan">{tx('Giriş', 'Entry')} {analysisPrice(entry.entry)} · Stop {analysisPrice(entry.stop)} · TP1 {analysisPrice(entry.tp1)}</p>
            <p className="sqjMeta">{tx('Risk', 'Risk')} {fmt(entry.riskUsdt)} USDT · {entry.margin} USDT {entry.leverage}x{entry.rewardRisk !== null ? ` · ${tx('R/Ö', 'R/R')} 1 : ${fmt(entry.rewardRisk, 1)}` : ''}
              {entry.followedAnalysis !== null && <span className="sqjFollow" data-ok={entry.followedAnalysis}>{entry.followedAnalysis ? tx('analizle uyumlu', 'with analysis') : tx('analize ters', 'against analysis')}</span>}</p>
            <NoteField entry={entry} onNote={onNote}/>
          </li>)}</ul>
          {confirmClear
            ? <div className="sqgAsk" role="alertdialog" aria-label={tx('Günlüğü sil onayı', 'Clear journal confirmation')}><span>{tx('Tüm günlük kayıtları silinsin mi? Bu geri alınamaz.', 'Delete all journal entries? This cannot be undone.')}</span>
                <div><button type="button" className="sqtBtn" data-tone="primary" onClick={() => { onClear(); setConfirmClear(false) }}>{tx('Evet, sil', 'Yes, delete')}</button><button type="button" className="sqtBtn" onClick={() => setConfirmClear(false)}>{tx('Vazgeç', 'Cancel')}</button></div></div>
            : <button type="button" className="sqtLink sqjClear" onClick={() => setConfirmClear(true)}>{tx('Günlüğü temizle', 'Clear the journal')}</button>}
        </>}
      <p className="sqgFine">{tx('Kayıtlar yalnız bu tarayıcıda tutulur; gerçekleşen kâr/zarar için Pozisyonlar sekmesine bak.', 'Entries are kept in this browser only; see the Positions tab for realised profit/loss.')}</p>
    </div>
  </aside>
}
