// Kais ajanı — aşama 2: sesli/yazılı komuttan Demo emir TASLAĞI çıkarır. Taslak hiçbir yere gönderilmez;
// eksik alanları kullanıcı kartta kendisi doldurur (Kais SL/TP uydurmaz).
export type OrderDraft = {symbol: string; direction: 'LONG' | 'SHORT' | ''; margin: string; leverage: string; stop: string; tp1: string; tp2: string; tp3: string}

const COINS = ['btc', 'eth', 'sol', 'bnb', 'xrp', 'doge', 'ada', 'avax', 'link', 'dot', 'ltc', 'trx', 'sui', 'arb', 'op', 'near', 'atom', 'inj', 'apt']
const fold = (value: string) => value.toLocaleLowerCase('tr').replace(/ı/g, 'i').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/,/g, '.')

const number = (text: string, pattern: RegExp) => { const hit = text.match(pattern); return hit ? hit[1] : '' }

/** "BTC long 100 usdt demo emir hazırla stop 60000 tp1 63000" → taslak. Emir niyeti yoksa null. */
export function parseOrderDraft(message: string): OrderDraft | null {
  const text = fold(message)
  if (text.split(' ').length > 30) return null
  const wantsOrder = /\b(emir|emri|order|siparis)\b/.test(text) && /\b(hazirla|hazir et|ver|gir|ac|olustur|gonder|prepare|place|create|open)\b/.test(text)
  if (!wantsOrder) return null
  const symbolHit = text.match(/\b([a-z]{2,6})\s?(?:\/)?usdt\b/)?.[1]
  const coin = symbolHit && symbolHit.length >= 2 ? symbolHit : COINS.find(name => new RegExp(`\\b${name}\\b`).test(text)) ?? ''
  const direction = /\b(long|alis|al)\b/.test(text) ? 'LONG' : /\b(short|satis|sat)\b/.test(text) ? 'SHORT' : ''
  return {
    symbol: coin ? `${coin.toUpperCase()}USDT` : '', direction,
    margin: number(text, /(\d+(?:\.\d+)?)\s*(?:usdt|dolar|\$)/), leverage: number(text, /(\d+)\s*x\b/) || number(text, /kaldirac\s*(\d+)/) || '1',
    stop: number(text, /\b(?:stop|sl|zarar)\w*\s*(\d+(?:\.\d+)?)/), tp1: number(text, /\btp\s?1\s*(\d+(?:\.\d+)?)/),
    tp2: number(text, /\btp\s?2\s*(\d+(?:\.\d+)?)/), tp3: number(text, /\btp\s?3\s*(\d+(?:\.\d+)?)/),
  }
}

export type OrderBody = {symbol: string; direction: 'LONG' | 'SHORT'; order_type: 'MARKET'; margin_usdt: number; leverage: number; stop_loss: number; tp1: number; tp2: number; tp3: number}

/** Eksik/geçersiz alan varsa Türkçe açıklama, yoksa gövde. Sunucu yine kendi doğrulamasını yapar. */
export function buildOrderBody(draft: OrderDraft): {body: OrderBody; error?: undefined} | {body?: undefined; error: string} {
  const n = (value: string) => Number(value.replace(',', '.'))
  if (!/^[A-Z]{2,12}USDT$/.test(draft.symbol.trim().toUpperCase())) return {error: 'Parite gir (ör. BTCUSDT).'}
  if (!draft.direction) return {error: 'Yön seç: Long veya Short.'}
  const margin = n(draft.margin), leverage = n(draft.leverage), stop = n(draft.stop), tp1 = n(draft.tp1), tp2 = n(draft.tp2), tp3 = n(draft.tp3)
  if (!(margin >= 5 && margin <= 100)) return {error: 'Marjin 5–100 USDT arasında olmalı.'}
  if (!Number.isInteger(leverage) || leverage < 1) return {error: 'Kaldıraç 1 veya daha büyük bir tam sayı olmalı.'}
  if (![stop, tp1, tp2, tp3].every(value => Number.isFinite(value) && value > 0)) return {error: 'Stop ve TP1–TP3 fiyatlarını gir; Kais bunları uydurmaz.'}
  return {body: {symbol: draft.symbol.trim().toUpperCase(), direction: draft.direction, order_type: 'MARKET', margin_usdt: margin, leverage, stop_loss: stop, tp1, tp2, tp3}}
}
