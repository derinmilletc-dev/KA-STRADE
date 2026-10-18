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
const VERB = /\b(ac|acar misin|acsana|acabilir misin|git|gotur|goster|gec|getir|open|go to|show|take me)\b/

const fold = (value: string) => value.toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/[^a-z0-9\s]/g, ' ').replace(/\s+/g, ' ').trim()

export type MasterTab = 'analiz'|'canli'|'baglanti'|'pozisyonlar'
const TABS: {tab: MasterTab; re: RegExp; label: {tr: string; en: string}}[] = [
  {tab: 'pozisyonlar', re: /\b(pozisyon|position|emir|order)/, label: {tr: 'Master Pozisyonlar', en: 'Master Positions'}},
  {tab: 'baglanti', re: /\b(baglanti|connection)/, label: {tr: 'Master Bağlantı', en: 'Master Connection'}},
  {tab: 'canli', re: /\b(canli|live)/, label: {tr: 'Master Canlı', en: 'Master Live'}},
  {tab: 'analiz', re: /\banaliz\b.*\b(sekme|tab)|\b(sekme|tab)\b.*\banaliz/, label: {tr: 'Master Analiz', en: 'Master Analysis'}},
]

/** "analist aç", "master'a git", "open scanner" → hedef sayfa. Emin değilse null (mesaj normal şekilde modele gider). */
export function parseNavigation(message: string, language: 'tr' | 'en'): {view: AgentView; label: string; tab?: MasterTab} | null {
  const text = fold(message)
  if (!text || text.split(' ').length > 8 || !VERB.test(text)) return null
  // Master Trade sekmesi: yalnızca "master" geçiyorsa (ör. "master trade pozisyonlar sekmesini aç").
  if (/\bmaster/.test(text)) {
    const tab = TABS.find(item => item.re.test(text))
    if (tab) return {view: 'master-trade', label: tab.label[language], tab: tab.tab}
  }
  if (text.split(' ').length > 6) return null
  // Para harcayan/belirsiz istekler (ör. "BTC analizi aç") sayfa komutu sayılmaz; "analiz sayfasını aç" ise sayfa komutudur (Analist).
  if (/\banaliz/.test(text)) {
    if (/\b(sayfa|ekran|bolum|page|screen)/.test(text) && !/\b(btc|eth|sol|bnb|xrp|doge|ada|avax)\b/.test(text)) return {view: 'analyst', label: language === 'en' ? 'Analyst' : 'Analist'}
    if (!/\banalist\b/.test(text)) return null
  }
  const hit = PAGES.find(page => page.names.some(name => new RegExp(`\\b${name}`).test(text)))
  if (!hit) {
    // "pozisyonlarımı aç", "bağlantı sekmesini göster" → Master Trade sekmesi ("emir" bilerek hariç: emir taslağı komutlarıyla karışmasın).
    const tab = TABS.find(item => item.tab !== 'analiz' && /\b(pozisyon|position|baglanti|connection|canli islem|live trading)/.test(text) && item.re.test(text))
    if (tab) return {view: 'master-trade', label: tab.label[language], tab: tab.tab}
  }
  return hit ? {view: hit.view, label: hit.label[language]} : null
}
