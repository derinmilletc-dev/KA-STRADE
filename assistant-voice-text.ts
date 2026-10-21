// Ses tanıma metni düzeltme yardımcıları (saf fonksiyonlar; React/DOM bağımsız, birim testlenebilir).

/** Ses tanımanın “KAİS” adını yazdığı yaygın yanlışlar (Kayış, Kaiz, Kays, Kaes, Kay is, Ka is …). */
const HEARD_AS_KAIS = /(?<![\p{L}\p{N}])(?:ka[iİıI][sSşŞzZ]|kay[iİıI][sSşŞzZ]|ka[eE][sSzZ]|kay[sSzZ]|ka[ -][iİıI][sSşŞ]|kay[ -][iİıI][sSşŞ])(?![\p{L}\p{N}])/giu
/** Duyulan metindeki adın yanlış yazılışlarını “KAİS” yapar; asistan adını doğru görür. */
export function normalizeKaisName(value: string): string {
  return value.replace(HEARD_AS_KAIS, 'KAİS')
}

/** Ses tanımanın sık yanlış yazdığı ticaret/platform sözcükleri (Türkçe tanıyıcı İngilizce terimleri bozar). */
const VOCABULARY: [RegExp, string][] = [
  [/(?<![\p{L}\p{N}])(?:bit ?koyun|bit ?koin|bit ?koyn|bitkoin|bit coin)(?![\p{L}\p{N}])/giu, 'Bitcoin'],
  [/(?<![\p{L}\p{N}])(?:eter ?yum|eteryum|eterium|etheryum|ether ?eum)(?![\p{L}\p{N}])/giu, 'Ethereum'],
  [/(?<![\p{L}\p{N}])(?:stop ?los|stoplos|stop lost|stop ?loss)(?![\p{L}\p{N}])/giu, 'stop loss'],
  [/(?<![\p{L}\p{N}])(?:teyk ?profit|tek ?profit|take ?profit|teyk ?prof[iı]t)(?![\p{L}\p{N}])/giu, 'take profit'],
  [/(?<![\p{L}\p{N}])(?:master ?tre[iy]d|mastır ?tre[iy]d|mastir ?tre[iy]d|master ?trade)(?![\p{L}\p{N}])/giu, 'Master Trade'],
  [/(?<![\p{L}\p{N}])(?:binans|baynıns|bayn[ae]ns|binance)(?![\p{L}\p{N}])/giu, 'Binance'],
  [/(?<![\p{L}\p{N}])(?:bi ti si|bitisi|b t c)(?![\p{L}\p{N}])/giu, 'BTC'],
  [/(?<![\p{L}\p{N}])(?:i ti eç|i ti ey?ç|e t h|iti eç)(?![\p{L}\p{N}])/giu, 'ETH'],
  [/(?<![\p{L}\p{N}])(?:bi en bi|bi n bi|b n b)(?![\p{L}\p{N}])/giu, 'BNB'],
  [/(?<![\p{L}\p{N}])(?:eks ar pi|ex ar pi|x r p)(?![\p{L}\p{N}])/giu, 'XRP'],
  [/(?<![\p{L}\p{N}])(?:yu es di ti|u es di ti|us di ti|usdt)(?![\p{L}\p{N}])/giu, 'USDT'],
  [/(?<![\p{L}\p{N}])(?:ar es ay|ar es i|r s i)(?![\p{L}\p{N}])/giu, 'RSI'],
  [/(?<![\p{L}\p{N}])(?:em ey si di|em ey sidi|m a c d)(?![\p{L}\p{N}])/giu, 'MACD'],
  [/(?<![\p{L}\p{N}])(?:doç ?koyn|doj ?koin|doç ?koin|dog ?koin|dogecoin)(?![\p{L}\p{N}])/giu, 'Dogecoin'],
  [/(?<![\p{L}\p{N}])(?:solana|sol ?ana|solona)(?![\p{L}\p{N}])/giu, 'Solana'],
  [/(?<![\p{L}\p{N}])(?:lonk|longu|lonğ)(?![\p{L}\p{N}])/giu, 'long'],
  [/(?<![\p{L}\p{N}])(?:şort|şortu)(?![\p{L}\p{N}])/giu, 'short'],
  [/(?<![\p{L}\p{N}])(?:kaldıraç|leverec|levirec|levıreç)(?![\p{L}\p{N}])/giu, 'kaldıraç'],
]

/** Sözlük/ad düzeltmesinin kaç yeri değiştirdiği: alternatifler arasında en tutarlı olanı seçmek için puan. */
export function vocabularyHits(value: string): number {
  let hits = (value.match(HEARD_AS_KAIS) ?? []).length
  for (const [pattern] of VOCABULARY) hits += (value.match(pattern) ?? []).length
  return hits
}

type Alternatives = ArrayLike<{transcript: string; confidence?: number}>
/**
 * Tarayıcının verdiği alternatifler arasından seçim: varsayılan ilk (en güvenilir) alternatiftir; yalnız ilk alternatif
 * hiç bilinen terim içermiyor ve başka bir alternatif içeriyorsa, makul güvenle o alternatif tercih edilir.
 */
export function pickAlternative(result: Alternatives | undefined): string {
  if (!result || !result.length) return ''
  const list = Array.from(result)
  const first = list[0]
  if (!first) return ''
  const firstHits = vocabularyHits(first.transcript)
  if (firstHits > 0) return first.transcript
  const best = list.slice(1).filter(item => item.transcript.trim() && vocabularyHits(item.transcript) > 0
    && (item.confidence === undefined || first.confidence === undefined || item.confidence >= first.confidence * 0.6))
    .sort((a, b) => vocabularyHits(b.transcript) - vocabularyHits(a.transcript))[0]
  return best?.transcript ?? first.transcript
}
/** Ad ve sözlük düzeltmelerini birlikte uygular. */
export function fixVoiceText(value: string): string {
  return VOCABULARY.reduce((text, [pattern, replacement]) => text.replace(pattern, replacement), normalizeKaisName(value))
}
