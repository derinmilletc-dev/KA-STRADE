// Kais ajanı — aşama 1: yalnızca sayfa açma. Bu dosya sadece arayüzde bir sayfa değiştirir;
// veri okumaz, emir vermez, ağa istek atmaz.
export type AgentView = 'dashboard'|'trading'|'risk'|'analyst'|'scanner'|'performance'|'ops'|'support'|'setup'|'pricing'|'billing'|'master-trade'

const PAGES: {view: AgentView; names: string[]; label: {tr: string; en: string}}[] = [
  {view: 'master-trade', names: ['master', 'master trade', 'master islem', 'usta'], label: {tr: 'Master', en: 'Master'}},
  {view: 'analyst', names: ['analist', 'analyst'], label: {tr: 'Analist', en: 'Analyst'}},
  {view: 'scanner', names: ['tarama', 'tarayici', 'scanner'], label: {tr: 'Tarama', en: 'Scanner'}},
  {view: 'performance', names: ['performans', 'performance'], label: {tr: 'Performans', en: 'Performance'}},
  {view: 'risk', names: ['risk'], label: {tr: 'Risk', en: 'Risk'}},
  {view: 'trading', names: ['islem sayfa', 'islemler', 'trading'], label: {tr: 'İşlem', en: 'Trading'}},
  {view: 'support', names: ['destek', 'support'], label: {tr: 'Destek', en: 'Support'}},
  {view: 'setup', names: ['ayarlar', 'kurulum', 'settings'], label: {tr: 'Ayarlar', en: 'Settings'}},
  {view: 'ops', names: ['operasyon', 'operations'], label: {tr: 'Operasyon', en: 'Operations'}},
  {view: 'pricing', names: ['paketler', 'fiyatlar', 'planlar', 'pricing'], label: {tr: 'Paketler', en: 'Plans'}},
  {view: 'billing', names: ['fatura', 'billing'], label: {tr: 'Fatura', en: 'Billing'}},
  {view: 'dashboard', names: ['ana sayfa', 'anasayfa', 'ana ekran', 'home', 'dashboard'], label: {tr: 'Ana sayfa', en: 'Home'}},
]
const VERB = /\b(ac|acar misin|git|gotur|goster|gec|open|go to|show|take me)\b/

const fold = (value: string) => value.toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()

/** "analist aç", "master'a git", "open scanner" → hedef sayfa. Emin değilse null (mesaj normal şekilde modele gider). */
export function parseNavigation(message: string, language: 'tr' | 'en'): {view: AgentView; label: string} | null {
  const text = fold(message)
  if (!text || text.split(' ').length > 6 || !VERB.test(text)) return null
  // Para harcayan/belirsiz istekler (ör. "BTC analizi aç") sayfa komutu sayılmaz.
  if (/\banaliz/.test(text)) return null
  const hit = PAGES.find(page => page.names.some(name => new RegExp(`\\b${name}`).test(text)))
  return hit ? {view: hit.view, label: hit.label[language]} : null
}
