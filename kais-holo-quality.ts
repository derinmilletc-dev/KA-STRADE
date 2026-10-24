/** Hologram ekranı için uyarlanır kalite ölçeri. Ekranın gerçek kare aralığını (requestAnimationFrame) ölçer; cihaz yetişemiyorsa kaliteyi
 *  kademeli düşürür, böylece ekran donmaz. 2 = tam, 1 = hafif (arka plan 20 kare/sn, parçacık ve gezginler azalır, çekirdek 30 kare/sn),
 *  0 = en hafif (arka plan neredeyse durağan, çekirdek 24 kare/sn). Yükselme yalnız uzun süre rahat çalışırsa olur.
 *  Hiçbir ses/mikrofon koduna dokunmaz; yalnız görsel çizim sıklığını etkiler. */
let level = 2
let users = 0
let handle = 0
let lastTime = 0
let windowStart = 0
let frames = 0
let sinceChange = 0
let goodSeconds = 0

const tick = (now: number) => {
  if (!users) return
  if (!windowStart) { windowStart = now; lastTime = now; frames = 0 }
  frames += 1
  const elapsed = now - windowStart
  if (elapsed >= 1500) {
    const interval = elapsed / frames           // ms / kare (ölçerin kendi rAF döngüsü: sistem yükünü yansıtır)
    sinceChange += elapsed
    if (interval > 36 && level > 0 && sinceChange > 2500) { level -= 1; sinceChange = 0; goodSeconds = 0 }
    else if (interval < 19) { goodSeconds += elapsed / 1000; if (goodSeconds > 20 && level < 2 && sinceChange > 20000) { level += 1; sinceChange = 0; goodSeconds = 0 } }
    else goodSeconds = 0
    windowStart = now; frames = 0
  }
  handle = requestAnimationFrame(tick)
}

/** Ölçeri açar; dönen işlev kapatır (sayaçlı: birden çok çizim motoru paylaşır). */
export function acquireQuality(): () => void {
  users += 1
  if (users === 1) { windowStart = 0; handle = requestAnimationFrame(tick) }
  let released = false
  return () => { if (released) return; released = true; users = Math.max(0, users - 1); if (!users) { cancelAnimationFrame(handle); windowStart = 0 } }
}
export const getQuality = () => level
