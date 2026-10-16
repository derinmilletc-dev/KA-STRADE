// Display-only Turkish labels for Master Trade. Decision logic keeps its English
// internal values; translate only at render time so comparisons stay unchanged.
const EXACT: Record<string, string> = {
  // Durum
  'NO TRADE': 'İŞLEM YOK', 'WATCH': 'İZLE', 'WAIT': 'BEKLE', 'LONG SETUP': 'LONG KURULUMU', 'SHORT SETUP': 'SHORT KURULUMU',
  'STRONG LONG': 'GÜÇLÜ LONG', 'STRONG SHORT': 'GÜÇLÜ SHORT', 'WAITING FOR MARKET DATA': 'PİYASA VERİSİ BEKLENİYOR',
  // Sinyal gücü
  'VERY WEAK': 'ÇOK ZAYIF', 'WEAK': 'ZAYIF', 'MODERATE': 'ORTA', 'STRONG': 'GÜÇLÜ', 'VERY STRONG': 'ÇOK GÜÇLÜ',
  // Giriş kalitesi
  'POOR': 'ZAYIF', 'FAIR': 'ORTA', 'GOOD': 'İYİ', 'EXCELLENT': 'ÇOK İYİ',
  // Piyasa yapısı
  'RANGING': 'YATAY', 'UNCLEAR': 'BELİRSİZ', 'TRENDING BULLISH': 'YÜKSELİŞ TRENDİ', 'TRENDING BEARISH': 'DÜŞÜŞ TRENDİ', 'BREAKOUT': 'KIRILIM', 'BREAKDOWN': 'AŞAĞI KIRILIM',
  // Momentum
  'POSITIVE': 'POZİTİF', 'NEGATIVE': 'NEGATİF', 'NEUTRAL': 'NÖTR',
  // Gerekçeler
  'Trend aligned': 'Trend uyumlu', 'Trend not aligned': 'Trend uyumsuz',
  'MACD bullish': 'MACD yükseliş yönlü', 'MACD bearish': 'MACD düşüş yönlü', 'MACD not bullish': 'MACD yükseliş yönlü değil', 'MACD not bearish': 'MACD düşüş yönlü değil',
  'RSI healthy': 'RSI sağlıklı aralıkta',
  'Volume confirmed': 'Hacim teyitli', 'Volume not confirmed': 'Hacim teyitsiz', 'Volume increasing': 'Hacim artıyor', 'Volume expansion': 'Hacim genişliyor',
  'Volume deterioration': 'Hacim zayıflıyor', 'Volume insufficient': 'Hacim yetersiz', 'Weak volume': 'Hacim zayıf',
  'MTF supportive': 'MTF destekliyor', 'MTF not confirmed': 'MTF teyitsiz', 'MTF conflict': 'MTF çelişkili', 'MTF confirmation missing': 'MTF teyidi eksik',
  'Support nearby': 'Destek yakın', 'Support lost': 'Destek kaybedildi', 'Support breakdown': 'Destek kırıldı',
  'Resistance nearby': 'Direnç yakın', 'Resistance reclaimed': 'Direnç geri alındı', 'Resistance breakout': 'Direnç kırıldı',
  'Stale signal': 'Sinyal eski', 'Signal stale': 'Sinyal eski', 'Risk/Reward below minimum': 'Risk/Ödül minimumun altında',
  'No clear directional structure': 'Net bir yön yapısı yok', 'Market data unavailable': 'Piyasa verisi yok',
  'Market analysis data unavailable': 'Analiz verisi yok', 'No current snapshot': 'Güncel veri yok',
  // Tetik durumu
  'Waiting': 'Bekliyor', 'Armed': 'Hazır', 'Triggered': 'Tetiklendi', 'Confirmed': 'Teyit edildi', 'Invalidated': 'Geçersiz', 'Expired': 'Süresi doldu',
  'WAITING': 'BEKLİYOR', 'ARMED': 'HAZIR', 'TRIGGERED': 'TETİKLENDİ', 'CONFIRMED': 'TEYİT EDİLDİ', 'INVALIDATED': 'GEÇERSİZ', 'EXPIRED': 'SÜRESİ DOLDU',
}

export function trLabel(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const exact = EXACT[value]
  if (exact) return exact
  const mtf = /^MTF confirmation (\d+)\/(\d+)$/.exec(value)
  if (mtf) return `MTF teyidi ${mtf[1]}/${mtf[2]}`
  return value
}
