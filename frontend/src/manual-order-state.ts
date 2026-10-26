// Manuel emir formunda coin değişimi ve analiz doldurma kuralları. Saf fonksiyonlar: ağ ve emir yok.
// Amaç: bir coinin fiyata bağlı değerleri (limit, stop, TP) başka coine yanlışlıkla taşınmasın.

export type PriceFields = {limit_price: string; stop_loss: string; tp1: string; tp2: string; tp3: string}

export const normalizeSymbol = (value: unknown): string => String(value ?? '').toUpperCase().replace('/', '').trim()

// Coin değişirse fiyata bağlı alanlar silinir; marjin, kaldıraç, yön, emir tipi ve hedef payı korunur.
// Aynı coin yeniden seçilirse form hiç değişmez (aynı nesne döner).
export function switchSymbol<T extends PriceFields & {symbol: string}>(order: T, nextSymbol: string): T {
  if (normalizeSymbol(order.symbol) === normalizeSymbol(nextSymbol)) return order
  return {...order, symbol: nextSymbol, limit_price: '', stop_loss: '', tp1: '', tp2: '', tp3: ''}
}

// "Analizden doldur" yalnız seçili coin, analizin coiniyle aynıysa çalışır. Değilse nedenini döndürür.
export function analysisFillBlock(orderSymbol: string, analysisSymbol: string): string | null {
  const selected = normalizeSymbol(orderSymbol)
  const analysed = normalizeSymbol(analysisSymbol)
  if (!analysed) return 'Analiz seviyeleri henüz yok.'
  if (selected === analysed) return null
  return `Analiz ${analysed} içindir, seçili coin ${selected || 'yok'}. Başka coinin seviyeleri bu coine uygulanmaz; önce ${analysed} coinini seç ya da stop ve hedefi elle gir.`
}

export type Direction = 'LONG' | 'SHORT'
const positive = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value > 0

// Stop mesafesi: girişe göre yüzde. Fiyatlar geçersizse null (uydurma değer üretilmez).
// sideOk: stop, yöne göre giriş fiyatının doğru tarafında mı (LONG: altında, SHORT: üstünde).
export function stopDistance(direction: Direction, entry: number, stop: number): {pct: number; sideOk: boolean} | null {
  if (!positive(entry) || !positive(stop)) return null
  return {pct: Math.abs(entry - stop) / entry * 100, sideOk: direction === 'LONG' ? stop < entry : stop > entry}
}

export function targetDistancePct(entry: number, target: number): number | null {
  if (!positive(entry) || !positive(target)) return null
  return Math.abs(target - entry) / entry * 100
}

export type MapKey = 'stop' | 'entry' | 'tp1' | 'tp2' | 'tp3'
export type MapPoint = {key: MapKey; price: number; pos: number}
export type PriceMapResult = {valid: boolean; reason: string; points: MapPoint[]}

// Fiyat haritası: soldan sağa artan fiyat. LONG'da stop solda hedefler sağda, SHORT'ta tersi.
// Sıralama yöne uymuyorsa harita çizilmez, nedeni yazılır.
export function priceMap(direction: Direction, entry: number, stop: number, targets: [number, number, number]): PriceMapResult {
  const values = [entry, stop, ...targets]
  if (!values.every(positive)) return {valid: false, reason: 'Harita için giriş, stop ve üç hedef gerekli.', points: []}
  const [tp1, tp2, tp3] = targets
  const ordered = direction === 'LONG' ? stop < entry && entry < tp1 && tp1 < tp2 && tp2 < tp3 : stop > entry && entry > tp1 && tp1 > tp2 && tp2 > tp3
  if (!ordered) return {valid: false, reason: direction === 'LONG' ? 'LONG için sıra: Stop < Giriş < TP1 < TP2 < TP3.' : 'SHORT için sıra: Stop > Giriş > TP1 > TP2 > TP3.', points: []}
  const raw: Array<[MapKey, number]> = [['stop', stop], ['entry', entry], ['tp1', tp1], ['tp2', tp2], ['tp3', tp3]]
  const min = Math.min(...raw.map(item => item[1]))
  const max = Math.max(...raw.map(item => item[1]))
  const span = max - min
  const points = raw.map(([key, price]) => ({key, price, pos: 4 + (span > 0 ? (price - min) / span : 0) * 92}))
  points.sort((left, right) => left.pos - right.pos)
  return {valid: true, reason: '', points}
}

export type CheckState = 'ok' | 'bad' | 'pending'
export type CheckInput = {
  symbolKnown: boolean | null; balanceOk: boolean | null; marginOk: boolean; leverageOk: boolean
  quantityOk: boolean | null; stopOk: boolean | null; targetsOk: boolean | null
  dryRunPassed: boolean; dryRunStale: boolean
}
export type CheckRow = {key: string; label: string; state: CheckState; detail: string}
const tri = (value: boolean | null): CheckState => value === null ? 'pending' : value ? 'ok' : 'bad'

// Son kontrol listesi: yalnız gerçek duruma bakar. Bilinmeyen madde "bekliyor" kalır, başarılı gösterilmez.
// Binance filtreleri ve emir verisi güncelliği ancak dry-run (borsa test emri) geçince onaylanır.
export function checkRows(input: CheckInput): CheckRow[] {
  const exchange: CheckState = input.dryRunPassed ? 'ok' : input.dryRunStale ? 'bad' : 'pending'
  return [
    {key: 'symbol', label: 'Sembol doğrulandı', state: tri(input.symbolKnown), detail: input.symbolKnown === null ? 'Coin listesi bekleniyor' : input.symbolKnown ? 'Binance listesinde var' : 'Coin listede bulunamadı'},
    {key: 'balance', label: 'Bakiye yeterli', state: tri(input.balanceOk), detail: input.balanceOk === null ? 'Bakiye okunamadı' : input.balanceOk ? 'Marjin kullanılabilir bakiyeyi aşmıyor' : 'Marjin kullanılabilir bakiyeden büyük'},
    {key: 'margin', label: 'Marjin geçerli', state: tri(input.marginOk), detail: input.marginOk ? 'Sınırlar içinde' : 'Marjin 5 USDT ile risk sınırı arasında olmalı'},
    {key: 'leverage', label: 'Kaldıraç geçerli', state: tri(input.leverageOk), detail: input.leverageOk ? 'Sınırlar içinde' : 'Kaldıraç 1x ile izin verilen sınır arasında tam sayı olmalı'},
    {key: 'quantity', label: 'Pozisyon miktarı geçerli', state: tri(input.quantityOk), detail: input.quantityOk === null ? 'Stop girilince sunucu hesaplar' : input.quantityOk ? 'Sunucu risk hesabı yaptı' : 'Sunucu risk hesabını reddetti'},
    {key: 'stop', label: 'Stop fiyatı geçerli', state: tri(input.stopOk), detail: input.stopOk === null ? 'Stop bekleniyor' : input.stopOk ? 'Doğru tarafta' : 'Yöne göre yanlış tarafta'},
    {key: 'targets', label: 'Hedef fiyatları geçerli', state: tri(input.targetsOk), detail: input.targetsOk === null ? 'Hedefler bekleniyor' : input.targetsOk ? 'Sıralı ve doğru tarafta' : 'Sıra veya taraf hatalı'},
    {key: 'exchange', label: 'Binance filtreleri uygun', state: exchange, detail: input.dryRunPassed ? 'Borsa test emri geçti' : input.dryRunStale ? 'Emir değişti, dry-run yenilenmeli' : 'Dry-run ile borsa kontrol eder'},
    {key: 'fresh', label: 'Emir verileri güncel', state: exchange, detail: input.dryRunPassed ? 'Dry-run bu emirle yapıldı' : input.dryRunStale ? 'Dry-run eski emre ait' : 'Dry-run bekliyor'},
  ]
}
