import {useState} from 'react'
import {createRoot} from 'react-dom/client'
import KaisEye from '../../src/KaisEye'
import type {EyePresence} from '../../../kais-eye-motion'

// Konuşan asistan gözü için test düzeneği: bağlam (presence) ve durum değiştirilebilir, bileşen sökülüp yeniden takılabilir.
function Fixture() {
  const [mounted, setMounted] = useState(true)
  const [presence, setPresence] = useState<EyePresence>('idle')
  return <main style={{padding: 24}}>
    <button onClick={() => setMounted(value => !value)}>Toggle eye</button>
    {(['idle', 'listening', 'thinking', 'speaking'] as const).map(item => <button key={item} aria-pressed={presence === item} onClick={() => setPresence(item)}>{item}</button>)}
    <div className="eyeStage" style={{marginTop: 24}}>{mounted && <KaisEye size={160} state={presence === 'thinking' ? 'thinking' : 'idle'} presence={presence}/>}</div>
  </main>
}

const root = document.getElementById('root')
if (!root) throw new Error('Kais eye motion test root is missing')
createRoot(root).render(<Fixture/>)
