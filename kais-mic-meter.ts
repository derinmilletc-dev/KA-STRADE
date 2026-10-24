/** Gerçek mikrofon ses şiddeti ölçer (yalnız görsel tepki için). Web Audio AnalyserNode ile mikrofon akışının RMS değerini okur.
 *  Ses kaydedilmez, saklanmaz, hiçbir yere gönderilmez; analizör çıkışa BAĞLANMAZ (hoparlöre ses gitmez).
 *  Konuşma tanımayı etkilememesi için yalnız masaüstünde (geniş ekran + ince imleç) çalışır; her hata sessizce yutulur ve
 *  ölçer kapalı kalır (o durumda görsel, tanıma olaylarına döner; ses taklit edilmez). */
type Meter = {stream: MediaStream; context: AudioContext; analyser: AnalyserNode; data: Uint8Array<ArrayBuffer>; floor: number; value: number}
let meter: Meter | null = null
let starting = false

export const micMeterAllowed = () => typeof window !== 'undefined' && window.matchMedia('(min-width: 1025px) and (pointer: fine)').matches

export async function startMicMeter(stream: MediaStream): Promise<boolean> {
  stopMicMeter()
  if (starting || !micMeterAllowed()) { stream.getTracks().forEach(track => track.stop()); return false }
  starting = true
  try {
    const Ctx = window.AudioContext || (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext
    if (!Ctx) { stream.getTracks().forEach(track => track.stop()); return false }
    const context = new Ctx()
    if (context.state === 'suspended') await context.resume().catch(() => undefined)
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024; analyser.smoothingTimeConstant = .6
    context.createMediaStreamSource(stream).connect(analyser)
    meter = {stream, context, analyser, data: new Uint8Array(analyser.fftSize), floor: .01, value: 0}
    return true
  } catch { stream.getTracks().forEach(track => track.stop()); meter = null; return false } finally { starting = false }
}

export function stopMicMeter() {
  const current = meter; meter = null
  if (!current) return
  try { current.stream.getTracks().forEach(track => track.stop()) } catch { /* yok say */ }
  try { void current.context.close() } catch { /* yok say */ }
}

export const micMeterActive = () => meter !== null

/** 0–1 yumuşatılmış ses şiddeti. Ortam gürültüsü tabanı otomatik öğrenilir; sessizlikte 0 döner. dt: saniye. */
export function readMicLevel(dt: number): number {
  const current = meter
  if (!current) return 0
  current.analyser.getByteTimeDomainData(current.data)
  let sum = 0
  for (let i = 0; i < current.data.length; i++) { const v = (current.data[i] - 128) / 128; sum += v * v }
  const rms = Math.sqrt(sum / current.data.length)
  // taban: yavaş yükselir, hızlı düşer (ortam gürültüsü)
  current.floor = rms < current.floor ? current.floor + (rms - current.floor) * .2 : current.floor + (rms - current.floor) * Math.min(1, dt * .15)
  const raw = Math.max(0, rms - current.floor * 1.4 - .004)
  const target = Math.min(1, Math.sqrt(raw * 9))
  // yükselirken hızlı, düşerken yavaş (titreme yok)
  const k = target > current.value ? 1 - Math.exp(-dt / .06) : 1 - Math.exp(-dt / .25)
  current.value += (target - current.value) * k
  return current.value
}
