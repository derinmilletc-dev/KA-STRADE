import {useState} from 'react'
import './copy-phrase.css'

/** Onay için yazılması istenen ifadeyi tek dokunuşla panoya kopyalar (etiketin dışında durur,
 *  böylece giriş kutusunun erişilebilir adı değişmez). */
export default function CopyPhrase({text}: {text: string}) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Pano kullanılamazsa ifade yine de elle seçilip kopyalanabilir.
    }
  }
  return <button type="button" className="copyPhraseBtn" aria-label={`${text} ifadesini kopyala`} onClick={() => void copy()}>
    {copied ? 'Kopyalandı ✓' : 'İfadeyi kopyala'}</button>
}
