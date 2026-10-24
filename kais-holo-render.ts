/** Kais "yaşayan enerji çekirdeği" çizim motoru (Canvas 2D, bağımlılıksız).
 *  Tek geçişte derinlikli sahne: hacimli ışık → arka parçacıklar → arka halka yarıları → küre telleri ve sinir ağı (arka yarı)
 *  → göz (iris) → enerji sütunu/taban → ön sinir ağı/teller/halkalar → cam kabuk → ön parçacıklar.
 *  Durum (listening/thinking/speaking/error) ve ses seviyesi dışarıdan GERÇEK uygulama durumundan verilir; burada durum uydurulmaz.
 *  Seviye (0–1), konuşma tanıma/okuma olaylarından türetilen --kh-level değeridir; gerçek ses genliği değildir. */

import {acquireQuality, getQuality} from './kais-holo-quality'

export type CorePhase = 'listening' | 'thinking' | 'speaking' | 'error'
export type CoreOptions = {
  getPhase: () => CorePhase
  getLevel: () => number
  getLook: () => [number, number]
  getParallax: () => [number, number]
  lite: boolean
  reduced: boolean
}

type V3 = [number, number, number]
const TAU = Math.PI * 2
const CYAN: V3 = [53, 221, 226], SOFT: V3 = [101, 240, 222], ICE: V3 = [181, 255, 255], ERR: V3 = [240, 107, 117]

function seeded(seed: number) {
  let state = seed >>> 0
  return () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 4294967296 }
}
const mix = (a: V3, b: V3, k: number): V3 => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]
const rgba = (c: V3, a: number) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${Math.max(0, Math.min(1, a)).toFixed(3)})`
const clamp01 = (v: number) => Math.max(0, Math.min(1, v))

const PHASE_TARGET: Record<CorePhase, {speed: number; glow: number; pulse: number; pupil: number; err: number; net: number}> = {
  listening: {speed: 1, glow: .85, pulse: .8, pupil: .0, err: 0, net: .75},
  thinking: {speed: 2.8, glow: 1, pulse: 2.6, pupil: -.08, err: 0, net: 1},
  speaking: {speed: 1.6, glow: 1, pulse: 1.5, pupil: .05, err: 0, net: .9},
  error: {speed: .12, glow: .32, pulse: .1, pupil: -.04, err: 1, net: .25},
}

export function createHoloRenderer(canvas: HTMLCanvasElement, options: CoreOptions) {
  const context = canvas.getContext('2d')
  if (!context) return null
  const ctx: CanvasRenderingContext2D = context
  const rnd = seeded(20241024)
  const lite = options.lite

  // ---- Sabit geometri (birim küre; yarıçap 1) ----
  // Katmanlı sinir ağı: gözün çevresinde eğik düzlemlerde dönen 5 yörünge katmanı (içten dışa). Her katman kendi eğimi, yarıçapı ve
  // salınım hızıyla hareket eder; bağlantılar anlamlıdır: katman içi zincir + bir sonraki katmana ışınsal dallar.
  const NET_LAYERS = [
    {r: .4, n: 6, tilt: 1.0, roll: .3, w: .55, amp: .32, ph: 0},
    {r: .55, n: 8, tilt: 1.15, roll: -.4, w: .38, amp: .26, ph: 1.3},
    {r: .7, n: 10, tilt: .9, roll: .8, w: .27, amp: .2, ph: 2.6},
    {r: .84, n: 12, tilt: 1.25, roll: -.9, w: .19, amp: .16, ph: 3.9},
    {r: .96, n: 14, tilt: 1.05, roll: .15, w: .13, amp: .12, ph: 5.1},
  ].map(layer => lite ? {...layer, n: Math.max(5, Math.round(layer.n * .6))} : layer)
  type NetNode = {layer: number; k: number; ang: number; yo: number}
  const nodes: NetNode[] = []
  const layerStart: number[] = []
  NET_LAYERS.forEach((layer, li) => {
    layerStart.push(nodes.length)
    for (let k = 0; k < layer.n; k++) nodes.push({layer: li, k, ang: (k / layer.n) * TAU + li * .4, yo: (rnd() * 2 - 1) * .11})
  })
  const links: [number, number][] = []
  const outward: number[][] = nodes.map(() => [])
  nodes.forEach((node, i) => {
    const layer = NET_LAYERS[node.layer]
    links.push([i, layerStart[node.layer] + (node.k + 1) % layer.n])   // katman içi zincir
    const next = NET_LAYERS[node.layer + 1]
    if (next) {
      const t0 = Math.round(node.k * next.n / layer.n) % next.n
      for (const off of [0, 1]) { const j = layerStart[node.layer + 1] + (t0 + off) % next.n; links.push([i, j]); outward[i].push(j) }
    }
  })
  // Sinyal yolları: iç katmandan dış katmana giden zincirler (konuşurken dışa, dinlerken içe, düşünürken kademeli akar)
  const PATHS = Array.from({length: lite ? 8 : 18}, (_, index) => {
    const path = [layerStart[0] + (index % NET_LAYERS[0].n)]
    while (outward[path[path.length - 1]].length) { const options = outward[path[path.length - 1]]; path.push(options[Math.floor(rnd() * options.length)]) }
    return {path, offset: rnd(), rate: .12 + rnd() * .12}
  })
  const DUST = Array.from({length: lite ? 40 : 110}, () => ({x: (rnd() * 2 - 1) * 1.5, y: (rnd() * 2 - 1) * 1.4, z: rnd() * 2 - 1, size: .4 + rnd() * 1.4, speed: .006 + rnd() * .02, phase: rnd() * TAU}))
  const RINGS = [
    {radius: 1.14, tilt: 1.12, roll: .28, spin: .0, rate: .11, dots: 5, width: 1.2, alpha: .55},
    {radius: 1.3, tilt: 1.25, roll: -.5, spin: 1.9, rate: -.07, dots: 4, width: 1, alpha: .42},
    {radius: .98, tilt: .86, roll: .9, spin: 3.4, rate: .17, dots: 3, width: .9, alpha: .38},
  ]
  const FIBERS = Array.from({length: lite ? 70 : 150}, () => ({a: rnd() * TAU, in: .02 + rnd() * .1, out: .66 + rnd() * .3, w: .5 + rnd() * .9, al: .1 + rnd() * .3}))
  const CRYPTS = Array.from({length: 16}, () => ({a: rnd() * TAU, r: .46 + rnd() * .22, len: .08 + rnd() * .1}))
  const BARS = 84
  const FIBERS_B = Array.from({length: lite ? 40 : 90}, () => ({a: rnd() * TAU, in: .04 + rnd() * .08, out: .5 + rnd() * .38, bend: (rnd() * 2 - 1) * .5, w: .4 + rnd() * .7, al: .08 + rnd() * .2}))
  const RUNNERS = FIBERS.slice(0, lite ? 10 : 26).map((fiber, index) => ({fiber, offset: rnd(), rate: .22 + rnd() * .4, size: index % 5 === 0 ? 1.4 : 1}))

  // ---- Yumuşatılmış durum ----
  const live = {speed: 1, glow: .85, pulse: .8, pupil: 0, err: 0, net: .75, level: 0, lookX: 0, lookY: 0, spin: 0, ringSpin: 0, pulseClock: 0, netClock: 0, phaseSince: 0}
  let lastPhase: CorePhase = options.getPhase()
  const waves: {born: number; dir: number; power: number}[] = []   // gerçek ses artışlarında doğan radyal dalgalar
  let prevLevel = 0, lastWave = -9, nextBlink = 3 + rnd() * 4, blinkAt = -9
  let last = 0
  let width = 0, height = 0, dpr = 1

  function resize() {
    const rect = canvas.getBoundingClientRect()
    const cssWidth = canvas.clientWidth || rect.width || 600
    dpr = Math.min(window.devicePixelRatio || 1, 1.25, (lite ? 760 : 1100) / Math.max(cssWidth, 1))
    width = Math.max(2, Math.round(cssWidth * dpr)); height = width
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height }
  }

  // ---- Projeksiyon ----
  let cx = 0, cy = 0, S = 0, R = 0
  const project = (p: V3, ay: number, ax: number): {x: number; y: number; z: number; f: number} => {
    const x1 = p[0] * Math.cos(ay) + p[2] * Math.sin(ay), z1 = -p[0] * Math.sin(ay) + p[2] * Math.cos(ay)
    const y2 = p[1] * Math.cos(ax) - z1 * Math.sin(ax), z2 = p[1] * Math.sin(ax) + z1 * Math.cos(ax)
    const f = 1 / (1 - .22 * z2)
    return {x: cx + x1 * f * R, y: cy + y2 * f * R, z: z2, f}
  }

  const ringPoint = (ring: {radius: number; tilt: number; roll: number}, a: number): V3 => {
    const x = Math.cos(a) * ring.radius, z0 = Math.sin(a) * ring.radius
    const y1 = -z0 * Math.sin(ring.tilt), z1 = z0 * Math.cos(ring.tilt)
    return [x * Math.cos(ring.roll) - y1 * Math.sin(ring.roll), x * Math.sin(ring.roll) + y1 * Math.cos(ring.roll), z1]
  }

  // Parıltı noktaları: renk başına bir kez çizilen küçük sprite (8'lik renk adımlarıyla); her karede gradyan üretmek yerine drawImage
  const glowCache = new Map<number, HTMLCanvasElement>()
  const glowSprite = (color: V3) => {
    const key = ((color[0] >> 3) << 10) | ((color[1] >> 3) << 5) | (color[2] >> 3)
    let sprite = glowCache.get(key)
    if (!sprite) {
      sprite = document.createElement('canvas'); sprite.width = sprite.height = 64
      const g = sprite.getContext('2d')
      if (g) {
        const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32), c: V3 = [(color[0] >> 3) << 3, (color[1] >> 3) << 3, (color[2] >> 3) << 3]
        gr.addColorStop(0, rgba(c, 1)); gr.addColorStop(.35, rgba(c, .35)); gr.addColorStop(1, rgba(c, 0))
        g.fillStyle = gr; g.fillRect(0, 0, 64, 64)
      }
      glowCache.set(key, sprite)
    }
    return sprite
  }
  function glowDot(x: number, y: number, radius: number, color: V3, alpha: number) {
    if (alpha <= .004 || radius < .5) return
    ctx.globalAlpha = Math.min(1, alpha)
    ctx.drawImage(glowSprite(color), x - radius, y - radius, radius * 2, radius * 2)
    ctx.globalAlpha = 1
  }

  function frame(now: number) {
    if (!width) resize()
    const dt = Math.min(.08, last ? (now - last) / 1000 : .016); last = now
    const t = now / 1000
    const phase = options.getPhase()
    if (phase !== lastPhase) { lastPhase = phase; live.phaseSince = t }
    const goal = PHASE_TARGET[phase]
    const k = 1 - Math.exp(-dt / .45)
    live.speed += (goal.speed - live.speed) * k; live.glow += (goal.glow - live.glow) * k; live.pulse += (goal.pulse - live.pulse) * k
    live.pupil += (goal.pupil - live.pupil) * k; live.err += (goal.err - live.err) * k; live.net += (goal.net - live.net) * k
    const level = options.getLevel()
    live.level += (level - live.level) * (1 - Math.exp(-dt / (level > live.level ? .05 : .24)))   // hızlı yükselir, yavaş söner
    // Ses yükselişi (gerçek seviyenin yukarı eşik geçişi) → dalga. Dinlerken içe, konuşurken dışa.
    if ((phase === 'listening' || phase === 'speaking') && level > .22 && prevLevel <= .22 && t - lastWave > .35) {
      lastWave = t; waves.push({born: t, dir: phase === 'listening' ? -1 : 1, power: clamp01(level)}); if (waves.length > 6) waves.shift()
    }
    prevLevel = level
    // Nadir, yumuşak göz kısılması (ışık daralması); hata durumunda yok
    if (t > nextBlink && live.err < .2) { blinkAt = t; nextBlink = t + 4 + rnd() * 6 }
    const bu = (t - blinkAt) / .34
    const blink = bu >= 0 && bu < 1 ? Math.sin(bu * Math.PI) : 0
    const [lookX, lookY] = options.getLook()
    live.lookX += (lookX - live.lookX) * (1 - Math.exp(-dt / .18)); live.lookY += (lookY - live.lookY) * (1 - Math.exp(-dt / .18))
    live.spin += dt * .16 * live.speed; live.ringSpin += dt * (live.speed + live.level * 1.8); live.netClock += dt * (.45 + live.speed * .55 + live.level * .8); live.pulseClock += dt * live.pulse
    const [px, py] = options.getParallax()

    const W = width
    cx = W / 2 + px * W * .008; cy = W / 2 + py * W * .005; S = W / 2; R = S * .5 * (1 + live.level * .018 + Math.sin(t * (TAU / 4.2)) * .005)   // boşta 4 sn nefes, ses şiddetiyle hafif şişme
    const tint = (c: V3) => mix(c, ERR, live.err)
    const cyan = tint(CYAN), soft = tint(SOFT), ice = tint(ICE)
    const energy = clamp01(live.glow * (.78 + live.level * .5))
    const ay = live.spin, ax = -.2 + Math.sin(t * .13) * .05

    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, W, W)
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'
    ctx.globalCompositeOperation = 'lighter'

    // 1) Hacimli ışık: sphere arkasında geniş sis + dikey ışık sütunu
    const haze = ctx.createRadialGradient(cx, cy, R * .1, cx, cy, R * 2.05)
    haze.addColorStop(0, rgba(cyan, .2 * energy)); haze.addColorStop(.38, rgba(mix(cyan, [16, 80, 110], .5), .1 * energy)); haze.addColorStop(1, rgba(cyan, 0))
    ctx.fillStyle = haze; ctx.fillRect(0, 0, W, W)
    const shaft = ctx.createLinearGradient(0, cy - R * 1.6, 0, cy + R * 1.4)
    shaft.addColorStop(0, rgba(ice, 0)); shaft.addColorStop(.45, rgba(ice, .05 * energy)); shaft.addColorStop(1, rgba(ice, 0))
    ctx.fillStyle = shaft; ctx.fillRect(cx - R * .22, cy - R * 1.6, R * .44, R * 3)

    // 2) Arka plan parçacıkları (derinlik z<0)
    const dust = (front: boolean) => {
      for (const d of DUST) {
        if ((d.z > 0) !== front) continue
        const rise = ((t * d.speed * live.speed + d.phase) % 1)
        const y = d.y - rise * 2.8 + 1.4
        const wrapped = y < -1.4 ? y + 2.8 : y
        const depth = .6 + d.z * .4
        const x = cx + (d.x + Math.sin(t * .3 + d.phase) * .03) * R * 1.18 * depth + px * (d.z) * R * .06
        const yy = cy + wrapped * R * .95 * depth - py * d.z * R * .04
        const fade = 1 - Math.min(1, Math.abs(wrapped) / 1.4)
        const alpha = (.18 + (d.z + 1) * .18) * fade * energy
        const size = d.size * (front ? 1.6 : 1) * (W / 900) * (.7 + (d.z + 1) * .35)
        glowDot(x, yy, size * 3.2, soft, alpha * .6)
        ctx.fillStyle = rgba(ice, alpha * 1.4); ctx.beginPath(); ctx.arc(x, yy, Math.max(.5, size * .6), 0, TAU); ctx.fill()
      }
    }
    dust(false)

    // 3) Orbital halkalar (z<0 yarı arkada; z>0 yarı ön geçişte)
    const ringPass = (front: boolean) => {
      RINGS.forEach((ring, ringIndex) => {
        const steps = lite ? 60 : 110
        const rot = ring.spin + live.ringSpin * ring.rate
        const lvl = 1 + live.level * .9
        const rr = {radius: ring.radius * (1 + live.level * .035), tilt: ring.tilt, roll: ring.roll}
        let drawing = false
        ctx.lineWidth = ring.width * dpr
        for (let i = 0; i <= steps; i++) {
          const a = (i / steps) * TAU
          // halka düzlemi: x-z çemberi, ekseni tilt ile eğik, rot ile döner
          const q = project(ringPoint(rr, a), rot, ax * .4)
          const isFront = q.z > 0
          if (isFront !== front) { if (drawing) { ctx.stroke(); drawing = false } continue }
          if (!drawing) { ctx.beginPath(); ctx.moveTo(q.x, q.y); drawing = true } else ctx.lineTo(q.x, q.y)
          ctx.strokeStyle = rgba(ringIndex === 1 ? soft : cyan, Math.min(1, ring.alpha * lvl) * energy * (front ? 1 : .55))
        }
        if (drawing) ctx.stroke()
        // enerji noktaları
        for (let d = 0; d < ring.dots; d++) {
          const a = (d / ring.dots) * TAU + live.ringSpin * ring.rate * 2.4 + ringIndex
          const q = project(ringPoint(rr, a), rot, ax * .4)
          if ((q.z > 0) !== front) continue
          glowDot(q.x, q.y, R * .042 * q.f * lvl, ice, (front ? .9 : .45) * energy); ctx.fillStyle = rgba(ice, (front ? .95 : .5) * energy); ctx.beginPath(); ctx.arc(q.x, q.y, Math.max(1, R * .008 * q.f), 0, TAU); ctx.fill()
        }
      })
    }
    ringPass(false)

    // 4) Küre telleri + sinir ağı
    const eyeClear = R * .33
    const awayFromEye = (x: number, y: number) => clamp01((Math.hypot(x - cx, y - cy) - eyeClear * .9) / (eyeClear * .8))
    const overEye = (x: number, y: number) => Math.hypot(x - cx, y - cy) < eyeClear * 1.3
    // Küre telleri: üç ayrı katman, üç ayrı hız/yön (dış kabuk yavaş, iç küre ters yönde hızlı, hale çok yavaş)
    const wireSphere = (front: boolean, rad: number, rot: number, tilt: number, aScale: number, lats: number[], lons: number) => {
      ctx.lineWidth = 1 * dpr
      for (const deg of lats) {
        const lat = deg * Math.PI / 180
        let drawing = false
        for (let i = 0; i <= 72; i++) {
          const a = (i / 72) * TAU
          const q = project([Math.cos(lat) * Math.cos(a) * rad, Math.sin(lat) * rad, Math.cos(lat) * Math.sin(a) * rad], rot, tilt)
          if ((q.z > 0) !== front || (front && overEye(q.x, q.y))) { if (drawing) { ctx.stroke(); drawing = false } continue }
          if (!drawing) { ctx.beginPath(); ctx.moveTo(q.x, q.y); drawing = true } else ctx.lineTo(q.x, q.y)
          ctx.strokeStyle = rgba(ice, (front ? .22 : .1) * energy * aScale)
        }
        if (drawing) ctx.stroke()
      }
      for (let m = 0; m < lons; m++) {
        const lon = (m / lons) * Math.PI
        let drawing = false
        for (let i = 0; i <= 64; i++) {
          const a = (i / 64) * TAU
          const q = project([Math.cos(a) * Math.cos(lon) * rad, Math.sin(a) * rad, Math.cos(a) * Math.sin(lon) * rad], rot, tilt)
          if ((q.z > 0) !== front || (front && overEye(q.x, q.y))) { if (drawing) { ctx.stroke(); drawing = false } continue }
          if (!drawing) { ctx.beginPath(); ctx.moveTo(q.x, q.y); drawing = true } else ctx.lineTo(q.x, q.y)
          ctx.strokeStyle = rgba(soft, (front ? .18 : .08) * energy * aScale)
        }
        if (drawing) ctx.stroke()
      }
    }
    const wire = (front: boolean) => {
      wireSphere(front, 1, ay, ax, 1, [-60, -35, -12, 12, 35, 60], 9)                     // dış kabuk
      wireSphere(front, 1.065, ay * -.38 + .8, ax * .6 + .12, .55, [-48, 0, 48], 6)       // dış hale: çok yavaş, ters yön
      wireSphere(front, .62, -ay * 1.9 + t * .04, ax * 1.4 - .1, .8 + live.level * .5, [-50, -15, 15, 50], 6)   // iç küre: hızlı, ters yön
    }
    // Katman etkinleşmesi: düşünürken içten dışa kademeli, konuşurken dışa (sesle), dinlerken içe (sesle)
    const NL = NET_LAYERS.length
    const layerAct = NET_LAYERS.map((_, l) => {
      const pos = l / (NL - 1)
      if (phase === 'thinking') { const fr = ((t * .75) % 1.5) - .25; return Math.exp(-((pos - fr) ** 2) / .035) }
      if (phase === 'speaking') { const fr = ((t * .9) % 1.4) - .2; return Math.exp(-((pos - fr) ** 2) / .05) * (.25 + live.level) }
      if (phase === 'listening') { const fr = 1.2 - ((t * .9) % 1.4); return Math.exp(-((pos - fr) ** 2) / .05) * (.25 + live.level) }
      return 0
    })
    const netPoints = nodes.map(node => {
      const layer = NET_LAYERS[node.layer]
      const a = node.ang + layer.amp * Math.sin(live.netClock * layer.w * 2.2 + layer.ph)
      const point = ringPoint({radius: layer.r * (1 + live.level * .025), tilt: layer.tilt, roll: layer.roll}, a)
      point[1] += node.yo
      return project(point, ay * 1.3, ax)
    })
    const network = (front: boolean) => {
      ctx.lineWidth = 1.1 * dpr
      links.forEach(([a, b]) => {
        const pa = netPoints[a], pb = netPoints[b]
        const z = (pa.z + pb.z) / 2
        if ((z > 0) !== front) return
        const fade = awayFromEye((pa.x + pb.x) / 2, (pa.y + pb.y) / 2)
        const act = Math.max(layerAct[nodes[a].layer], layerAct[nodes[b].layer])
        ctx.strokeStyle = rgba(act > .15 ? ice : soft, (.14 + (z + 1) * .18 + act * .5) * live.net * energy * fade)
        ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke()
      })
      // sinyaller: iç→dış yollar üzerinde (yön duruma göre); yol dışında rastgele uçuş yok
      const dirSign = phase === 'listening' ? -1 : 1
      PATHS.forEach(item => {
        const u = (item.offset + live.netClock * item.rate * (phase === 'thinking' ? 2.2 : 1)) % 1
        const pos = dirSign > 0 ? u : 1 - u
        const seg = Math.min(item.path.length - 2, Math.floor(pos * (item.path.length - 1)))
        const f = pos * (item.path.length - 1) - seg
        const pa = netPoints[item.path[seg]], pb = netPoints[item.path[seg + 1]]
        const x = pa.x + (pb.x - pa.x) * f, y = pa.y + (pb.y - pa.y) * f
        if (((pa.z + pb.z) / 2 > 0) !== front) return
        const fade = awayFromEye(x, y)
        const strength = live.net * fade * Math.sin(Math.min(1, u) * Math.PI) * (.55 + live.level * .8)
        const tx = x - (pb.x - pa.x) * .18 * dirSign, ty = y - (pb.y - pa.y) * .18 * dirSign
        ctx.strokeStyle = rgba(ice, .5 * strength); ctx.lineWidth = 1.6 * dpr; ctx.beginPath(); ctx.moveTo(tx, ty); ctx.lineTo(x, y); ctx.stroke()
        glowDot(x, y, R * .05 * (1 + live.level), ice, .85 * strength)
      })
      netPoints.forEach((q, i) => {
        if ((q.z > 0) !== front) return
        const fade = awayFromEye(q.x, q.y)
        const act = layerAct[nodes[i].layer]
        const size = R * (nodes[i].k === 0 ? .018 : .011) * q.f * (1 + act * .5)
        glowDot(q.x, q.y, size * (3 + act * 1.2), act > .2 ? ice : soft, (.3 + (q.z + 1) * .25 + act * .5) * live.net * fade * energy)
        ctx.fillStyle = rgba(ice, (.5 + (q.z + 1) * .25 + act * .3) * fade * energy); ctx.beginPath(); ctx.arc(q.x, q.y, Math.max(.7, size), 0, TAU); ctx.fill()
      })
    }
    wire(false); network(false)

    // 5) Gözün arkasında iç ışık
    glowDot(cx, cy, R * .78, cyan, (.28 + live.level * .3) * energy)
    // Hacim: küre içinde alt kenarda toplanan ışık ve yavaş dönen iç sis (derinlik hissi)
    const under = ctx.createRadialGradient(cx, cy + R * .62, 0, cx, cy + R * .62, R * .7)
    under.addColorStop(0, rgba(ice, .16 * energy)); under.addColorStop(1, rgba(cyan, 0))
    ctx.fillStyle = under; ctx.beginPath(); ctx.arc(cx, cy, R * .98, 0, TAU); ctx.fill()
    if (typeof ctx.createConicGradient === 'function') {
      const neb = ctx.createConicGradient(t * .09, cx, cy)
      const na = (.05 + live.level * .06) * energy
      neb.addColorStop(0, rgba(cyan, 0)); neb.addColorStop(.18, rgba(soft, na)); neb.addColorStop(.36, rgba(cyan, 0)); neb.addColorStop(.62, rgba(ice, na * .8)); neb.addColorStop(.8, rgba(cyan, 0)); neb.addColorStop(1, rgba(cyan, 0))
      ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, R * .95, 0, TAU); ctx.arc(cx, cy, R * .4, 0, TAU, true); ctx.clip('evenodd')
      ctx.fillStyle = neb; ctx.fillRect(cx - R, cy - R, R * 2, R * 2); ctx.restore()
    }

    // 6) Enerji sütunu + taban (arka yarı)
    const baseY = cy + R * .9, baseRx = R * 1.12, baseRy = baseRx * .17
    const colTop = cy + R * .3, colH = baseY - colTop
    ctx.save(); ctx.translate(cx, colTop + colH / 2); ctx.scale(.085, 1)
    const colGlow = ctx.createRadialGradient(0, 0, 0, 0, 0, colH / 2)
    colGlow.addColorStop(0, rgba(ice, .34 * energy)); colGlow.addColorStop(.55, rgba(cyan, .12 * energy)); colGlow.addColorStop(1, rgba(cyan, 0))
    ctx.fillStyle = colGlow; ctx.beginPath(); ctx.arc(0, 0, colH / 2, 0, TAU); ctx.fill(); ctx.restore()
    const colCore = ctx.createLinearGradient(0, colTop, 0, baseY)
    colCore.addColorStop(0, rgba(ice, 0)); colCore.addColorStop(.55, rgba(ice, .28 * energy)); colCore.addColorStop(1, rgba(ice, .6 * energy))
    ctx.fillStyle = colCore; ctx.fillRect(cx - 1.2 * dpr, colTop, 2.4 * dpr, colH)
    for (let i = 0; i < 6; i++) {   // yükselen enerji zerreleri
      const u = ((t * .35 * live.speed + i / 6) % 1)
      glowDot(cx + Math.sin(i * 2.1 + t) * R * .03, baseY - u * (baseY - cy - R * .34), R * .028, ice, (1 - u) * .8 * energy)
    }
    const baseRings = (front: boolean) => {
      for (let i = 0; i < 4; i++) {
        const rx = baseRx * (1 - i * .2), ry = baseRy * (1 - i * .2)
        ctx.lineWidth = (i === 0 ? 1.4 : 1) * dpr
        ctx.strokeStyle = rgba(i % 2 ? soft : cyan, (.6 - i * .1) * energy)
        if (i === 1) ctx.setLineDash([8 * dpr, 6 * dpr]); else if (i === 3) ctx.setLineDash([2 * dpr, 5 * dpr]); else ctx.setLineDash([])
        ctx.beginPath(); ctx.ellipse(cx, baseY, rx, ry, 0, front ? 0 : Math.PI, front ? Math.PI : TAU); ctx.stroke()
      }
      ctx.setLineDash([])
    }
    baseRings(false)
    const disc = ctx.createRadialGradient(cx, baseY, 0, cx, baseY, baseRx * .62)
    disc.addColorStop(0, rgba(ice, .55 * energy)); disc.addColorStop(.4, rgba(cyan, .22 * energy)); disc.addColorStop(1, rgba(cyan, 0))
    ctx.save(); ctx.translate(cx, baseY); ctx.scale(1, .22); ctx.translate(-cx, -baseY); ctx.fillStyle = disc; ctx.beginPath(); ctx.arc(cx, baseY, baseRx * .62, 0, TAU); ctx.fill(); ctx.restore()
    // taban dalgası: cevap bitince / yüksek seviyede dışa doğru halka
    const wave = ((t - live.phaseSince) * .9) % 1.6
    if (live.level > .05 || (phase === 'thinking')) {
      const ru = clamp01(wave / 1.6)
      ctx.strokeStyle = rgba(ice, (1 - ru) * .45 * Math.max(live.level, phase === 'thinking' ? .5 : 0)); ctx.lineWidth = 1.2 * dpr
      ctx.beginPath(); ctx.ellipse(cx, baseY, baseRx * (.5 + ru * .7), baseRy * (.5 + ru * .7), 0, 0, TAU); ctx.stroke()
    }
    for (let i = 0; i < 48; i++) {   // taban işaret çizgileri
      const a = (i / 48) * TAU + live.ringSpin * .05
      const x1 = cx + Math.cos(a) * baseRx * 1.04, y1 = baseY + Math.sin(a) * baseRy * 1.04
      const x2 = cx + Math.cos(a) * baseRx * 1.09, y2 = baseY + Math.sin(a) * baseRy * 1.09
      ctx.strokeStyle = rgba(soft, .3 * energy); ctx.lineWidth = 1 * dpr; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
    }

    // 7) GÖZ (iris küresi)
    drawEye()
    // Radyal ses dalgaları (göz düzleminde): içe = dinleme, dışa = konuşma
    ctx.globalCompositeOperation = 'lighter'
    for (let i = waves.length - 1; i >= 0; i--) {
      const w = waves[i], u = (t - w.born) / 1.3
      if (u >= 1) { waves.splice(i, 1); continue }
      const e = w.dir > 0 ? u : 1 - u
      const rr = R * (.36 + e * .64)
      ctx.strokeStyle = rgba(ice, (1 - u) * (1 - u) * .55 * (.5 + w.power * .5)); ctx.lineWidth = (1.2 + (1 - u) * 1.6) * dpr
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, TAU); ctx.stroke()
    }

    // 8) Ön sinir ağı, teller, halkalar, taban ön yarısı
    network(true); wire(true); ringPass(true); baseRings(true)

    // 9) Cam kabuk: fresnel kenar + yansıma
    ctx.globalCompositeOperation = 'lighter'
    const shell = ctx.createRadialGradient(cx, cy, R * .55, cx, cy, R * 1.02)
    shell.addColorStop(0, rgba(cyan, 0)); shell.addColorStop(.8, rgba(cyan, .05 * energy)); shell.addColorStop(.96, rgba(ice, .2 * energy)); shell.addColorStop(1, rgba(ice, 0))
    ctx.fillStyle = shell; ctx.beginPath(); ctx.arc(cx, cy, R * 1.03, 0, TAU); ctx.fill()
    ctx.lineWidth = 1.3 * dpr; ctx.strokeStyle = rgba(ice, .34 * energy); ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke()
    ctx.lineWidth = 3 * dpr; ctx.strokeStyle = rgba(ice, .22 * energy); ctx.beginPath(); ctx.arc(cx, cy, R * .97, Math.PI * 1.08, Math.PI * 1.52); ctx.stroke()
    ctx.lineWidth = 1.5 * dpr; ctx.strokeStyle = rgba(ice, .16 * energy); ctx.beginPath(); ctx.arc(cx, cy, R * .94, Math.PI * .05, Math.PI * .22); ctx.stroke()
    dust(true)
    ctx.globalCompositeOperation = 'source-over'

    function drawEye() {
      const eyeR = R * .33
      const ox = live.lookX * eyeR * .16, oy = live.lookY * eyeR * .11
      const ex = cx + ox * .4, ey = cy + oy * .4
      const lum = .82 + live.level * .75                       // iris aydınlanması: gerçek ses şiddetiyle artar
      const irisSpin = t * .015 * (1 + live.speed * .5)
      const conic = typeof ctx.createConicGradient === 'function'
      // dış ışıma (katmanlı: geniş sis + yakın halo)
      glowDot(ex, ey, eyeR * 2.6, cyan, (.38 * energy + live.level * .3))
      glowDot(ex, ey, eyeR * 1.5, ice, (.12 + live.level * .22) * energy)
      ctx.globalCompositeOperation = 'source-over'
      // iris tabanı: derin koyu kenar → turkuaz gövde → açık halka çevresi
      const base = ctx.createRadialGradient(ex + ox * .5, ey + oy * .5, eyeR * .12, ex, ey, eyeR)
      base.addColorStop(0, rgba(mix([200, 255, 255], ERR, live.err * .6), 1))
      base.addColorStop(.26, rgba(mix([104, 232, 242], ERR, live.err * .6), 1))
      base.addColorStop(.55, rgba(mix([22, 146, 188], ERR, live.err * .7), 1))
      base.addColorStop(.84, rgba(mix([7, 60, 94], ERR, live.err * .5), 1))
      base.addColorStop(1, rgba([2, 18, 30], 1))
      ctx.fillStyle = base; ctx.beginPath(); ctx.arc(ex, ey, eyeR, 0, TAU); ctx.fill()
      ctx.save(); ctx.beginPath(); ctx.arc(ex, ey, eyeR, 0, TAU); ctx.clip()
      ctx.globalCompositeOperation = 'lighter'
      // iris içinde akan enerji: iki ters yönde dönen konik ışık katmanı
      if (conic) {
        const swirlA = ctx.createConicGradient(irisSpin * 9, ex, ey)
        const aa = .2 * lum * energy
        swirlA.addColorStop(0, rgba(ice, 0)); swirlA.addColorStop(.1, rgba(ice, aa)); swirlA.addColorStop(.24, rgba(cyan, 0)); swirlA.addColorStop(.46, rgba(soft, aa * .8)); swirlA.addColorStop(.6, rgba(cyan, 0)); swirlA.addColorStop(.78, rgba(ice, aa * .9)); swirlA.addColorStop(1, rgba(ice, 0))
        ctx.fillStyle = swirlA; ctx.beginPath(); ctx.arc(ex, ey, eyeR * .94, 0, TAU); ctx.fill()
        const swirlB = ctx.createConicGradient(-irisSpin * 6 + 1.4, ex, ey)
        swirlB.addColorStop(0, rgba(cyan, 0)); swirlB.addColorStop(.15, rgba(cyan, aa * .7)); swirlB.addColorStop(.3, rgba(cyan, 0)); swirlB.addColorStop(.7, rgba(soft, aa * .6)); swirlB.addColorStop(.85, rgba(cyan, 0)); swirlB.addColorStop(1, rgba(cyan, 0))
        ctx.fillStyle = swirlB; ctx.beginPath(); ctx.arc(ex, ey, eyeR * .78, 0, TAU); ctx.fill()
      }
      // katman A: düz kas lifleri (yavaş, saat yönü)
      for (const fiber of FIBERS) {
        const a = fiber.a + irisSpin
        const r1 = eyeR * (.3 + fiber.in), r2 = eyeR * fiber.out
        ctx.strokeStyle = rgba(ice, fiber.al * (.5 + energy * .5) * lum * (1 - live.err * .4)); ctx.lineWidth = fiber.w * dpr * (W / 900)
        ctx.beginPath(); ctx.moveTo(ex + Math.cos(a) * r1 + ox, ey + Math.sin(a) * r1 + oy)
        ctx.lineTo(ex + Math.cos(a + .05) * r2, ey + Math.sin(a + .05) * r2); ctx.stroke()
      }
      // katman B: kıvrık ince lifler (ters yön, farklı hız) → organik doku
      for (const fiber of FIBERS_B) {
        const a = fiber.a - irisSpin * .7
        const r1 = eyeR * (.34 + fiber.in), r2 = eyeR * fiber.out, rm = (r1 + r2) / 2
        const x1 = ex + Math.cos(a) * r1 + ox, y1 = ey + Math.sin(a) * r1 + oy
        const x2 = ex + Math.cos(a + fiber.bend * .5) * r2, y2 = ey + Math.sin(a + fiber.bend * .5) * r2
        ctx.strokeStyle = rgba(soft, fiber.al * lum * energy * (1 - live.err * .4)); ctx.lineWidth = fiber.w * dpr * (W / 900)
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(ex + Math.cos(a + fiber.bend) * rm, ey + Math.sin(a + fiber.bend) * rm, x2, y2); ctx.stroke()
      }
      // enerji akışı: bazı liflerde göz bebeğinden dışa kayan ışık noktaları (hız gerçek seviyeyle artar)
      const flow = .35 + live.level * 1.6 + live.speed * .15
      for (const runner of RUNNERS) {
        const u = (runner.offset + t * runner.rate * flow) % 1
        const fiber = runner.fiber, a = fiber.a + irisSpin + .05 * u
        const r = eyeR * ((.3 + fiber.in) + (fiber.out - .3 - fiber.in) * u)
        glowDot(ex + Math.cos(a) * r + ox * (1 - u), ey + Math.sin(a) * r + oy * (1 - u), eyeR * .07 * runner.size, ice, Math.sin(u * Math.PI) * .75 * lum * energy)
      }
      // kollaret: tırtıklı iç halka + ince ikinci halka
      ctx.strokeStyle = rgba(ICE, (.42 + live.level * .3) * energy); ctx.lineWidth = 1.2 * dpr
      ctx.beginPath()
      for (let i = 0; i <= 90; i++) {
        const a = (i / 90) * TAU + irisSpin * 2
        const r = eyeR * (.5 + Math.sin(i * 1.7) * .018 + Math.sin(i * .6 + t * .4) * .01)
        const x = ex + Math.cos(a) * r + ox * .6, y = ey + Math.sin(a) * r + oy * .6
        if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y)
      }
      ctx.stroke()
      ctx.strokeStyle = rgba(soft, .22 * energy); ctx.lineWidth = 1 * dpr
      ctx.beginPath(); ctx.arc(ex + ox * .5, ey + oy * .5, eyeR * .58, 0, TAU); ctx.stroke()
      for (const crypt of CRYPTS) {
        const a = crypt.a + irisSpin
        ctx.strokeStyle = rgba([2, 40, 60], .5); ctx.lineWidth = 1.6 * dpr
        ctx.beginPath(); ctx.arc(ex, ey, eyeR * crypt.r, a, a + crypt.len); ctx.stroke()
      }
      ctx.globalCompositeOperation = 'source-over'
      // limbal karartma: iris kenarı derinleşir (hacim)
      const limbal = ctx.createRadialGradient(ex, ey, eyeR * .72, ex, ey, eyeR)
      limbal.addColorStop(0, 'rgba(2,14,26,0)'); limbal.addColorStop(.7, 'rgba(2,14,26,.38)'); limbal.addColorStop(1, 'rgba(1,8,16,.82)')
      ctx.fillStyle = limbal; ctx.beginPath(); ctx.arc(ex, ey, eyeR, 0, TAU); ctx.fill()
      // organik göz bebeği: düzensiz, yavaşça soluyan kenar (daire değil)
      const pupilR = eyeR * (.36 + live.pupil + live.level * .06 + Math.sin(t * .8) * .006) * (1 - blink * .22)
      const px0 = ex + ox, py0 = ey + oy
      const pupilRadius = (a: number) => pupilR * (1 + .02 * Math.sin(3 * a + t * .6) + .014 * Math.sin(5 * a - t * .45 + 1.2) + .008 * Math.sin(9 * a + t * 1.1))
      const pupilPath = (scale: number) => {
        ctx.beginPath()
        for (let i = 0; i <= 72; i++) {
          const a = (i / 72) * TAU, r = pupilRadius(a) * scale
          const x = px0 + Math.cos(a) * r, y = py0 + Math.sin(a) * r
          if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y)
        }
        ctx.closePath()
      }
      const halo = ctx.createRadialGradient(px0, py0, pupilR * .9, px0, py0, pupilR * 1.45)
      halo.addColorStop(0, 'rgba(0,6,14,.85)'); halo.addColorStop(1, 'rgba(0,10,20,0)')
      ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(px0, py0, pupilR * 1.45, 0, TAU); ctx.fill()
      const pupil = ctx.createRadialGradient(px0 - pupilR * .15, py0 - pupilR * .15, 0, px0, py0, pupilR * 1.1)
      pupil.addColorStop(0, 'rgba(2,14,24,1)'); pupil.addColorStop(.55, 'rgba(0,5,12,1)'); pupil.addColorStop(1, 'rgba(0,3,8,1)')
      ctx.fillStyle = pupil; pupilPath(1); ctx.fill()
      // gözbebeği derinliği: içinde ağın zayıf yansıması (bakışa ters yönde kayar → paralaks)
      ctx.globalCompositeOperation = 'lighter'
      ctx.save(); pupilPath(.96); ctx.clip()
      const spin2 = t * .35
      for (let i = 0; i < 3; i++) {
        const a = spin2 * (.6 + i * .25) + i * 2.1
        glowDot(px0 - ox * .5 + Math.cos(a) * pupilR * .4, py0 - oy * .5 + Math.sin(a) * pupilR * .4, pupilR * .16, soft, .28 * energy)
      }
      ctx.restore()
      // göz bebeği kenarı: ince parlak halka + sese göre şişen iç ışıma
      ctx.strokeStyle = rgba(ice, (.4 + live.level * .45) * energy); ctx.lineWidth = (1.2 + live.level * 2.2) * dpr; pupilPath(1.03); ctx.stroke()
      ctx.strokeStyle = rgba(cyan, (.16 + live.level * .3) * energy); ctx.lineWidth = (3 + live.level * 7) * dpr; pupilPath(1.12); ctx.stroke()
      ctx.globalCompositeOperation = 'source-over'
      // düşünürken tarama kaması
      if (phase === 'thinking') {
        const sweep = t * 3.2
        const wedge = ctx.createRadialGradient(ex, ey, pupilR, ex, ey, eyeR)
        wedge.addColorStop(0, rgba(ICE, .0)); wedge.addColorStop(1, rgba(ICE, .45))
        ctx.globalCompositeOperation = 'lighter'
        ctx.fillStyle = wedge; ctx.beginPath(); ctx.moveTo(ex, ey); ctx.arc(ex, ey, eyeR, sweep, sweep + .55); ctx.closePath(); ctx.fill()
        ctx.globalCompositeOperation = 'source-over'
      }
      // cam kubbe yansımaları
      const dome = ctx.createRadialGradient(ex - eyeR * .38, ey - eyeR * .42, 0, ex - eyeR * .38, ey - eyeR * .42, eyeR * .85)
      dome.addColorStop(0, 'rgba(255,255,255,.5)'); dome.addColorStop(.25, 'rgba(220,255,255,.14)'); dome.addColorStop(1, 'rgba(220,255,255,0)')
      ctx.fillStyle = dome; ctx.beginPath(); ctx.arc(ex, ey, eyeR, 0, TAU); ctx.fill()
      ctx.fillStyle = 'rgba(235,255,255,.5)'; ctx.beginPath(); ctx.ellipse(ex - eyeR * .34, ey - eyeR * .44, eyeR * .12, eyeR * .055, -.6, 0, TAU); ctx.fill()
      ctx.fillStyle = 'rgba(200,250,255,.18)'; ctx.beginPath(); ctx.ellipse(ex + eyeR * .3, ey + eyeR * .46, eyeR * .09, eyeR * .035, -.5, 0, TAU); ctx.fill()
      ctx.strokeStyle = 'rgba(190,250,255,.22)'; ctx.lineWidth = 2 * dpr; ctx.beginPath(); ctx.arc(ex, ey, eyeR * .88, Math.PI * .15, Math.PI * .55); ctx.stroke()
      ctx.restore()
      // dış kenar ışıması
      ctx.globalCompositeOperation = 'lighter'
      ctx.strokeStyle = rgba(ice, (.55 + live.level * .3) * energy); ctx.lineWidth = 1.6 * dpr; ctx.beginPath(); ctx.arc(ex, ey, eyeR, 0, TAU); ctx.stroke()
      ctx.strokeStyle = rgba(cyan, (.22 + live.level * .25) * energy); ctx.lineWidth = 6 * dpr; ctx.beginPath(); ctx.arc(ex, ey, eyeR * 1.02, 0, TAU); ctx.stroke()
      // dijital halka: gözün çevresinde ince dönen işaretler (iris ile ters yönde)
      ctx.strokeStyle = rgba(soft, .3 * energy); ctx.lineWidth = 1.2 * dpr
      ctx.setLineDash([1.5 * dpr, 7 * dpr]); ctx.lineDashOffset = -t * 6 * dpr
      ctx.beginPath(); ctx.arc(ex, ey, eyeR * 1.1, 0, TAU); ctx.stroke(); ctx.setLineDash([])
      const segStart = -t * .5
      ctx.strokeStyle = rgba(ice, (.3 + live.level * .3) * energy); ctx.lineWidth = 1.6 * dpr
      for (let i = 0; i < 3; i++) { ctx.beginPath(); ctx.arc(ex, ey, eyeR * 1.15, segStart + i * 2.09, segStart + i * 2.09 + .55); ctx.stroke() }
      // dairesel dalga çubukları (yalnız dinlerken/konuşurken, gerçek seviyeyle)
      const barsOn = phase === 'listening' || phase === 'speaking'
      for (let i = 0; i < BARS; i++) {
        const a = (i / BARS) * TAU
        const wob = .5 + .5 * Math.sin(i * 2.7 + t * 6) * Math.sin(i * .9 - t * 3.1)
        const len = eyeR * (.035 + (barsOn ? live.level * (.22 + .3 * wob) : 0))
        const r1 = eyeR * 1.24
        ctx.strokeStyle = rgba(soft, (.25 + (barsOn ? live.level * .6 : 0)) * energy); ctx.lineWidth = 1.5 * dpr
        ctx.beginPath(); ctx.moveTo(ex + Math.cos(a) * r1, ey + Math.sin(a) * r1); ctx.lineTo(ex + Math.cos(a) * (r1 + len), ey + Math.sin(a) * (r1 + len)); ctx.stroke()
      }
      ctx.globalCompositeOperation = 'source-over'
    }
  }

  let handle = 0
  let running = false
  let drawnAt = 0
  let releaseQuality: (() => void) | null = null
  const loop = (now: number) => { if (!running) return; const gap = [41, 32, 0][getQuality()]; if (now - drawnAt >= gap - 2) { drawnAt = now; frame(now) } handle = requestAnimationFrame(loop) }
  return {
    resize,
    start() { if (running) return; running = true; last = 0; if (options.reduced) { resize(); frame(performance.now()); frame(performance.now() + 16); running = false; return } releaseQuality = acquireQuality(); handle = requestAnimationFrame(loop) },
    stop() { running = false; cancelAnimationFrame(handle); releaseQuality?.(); releaseQuality = null },
    redraw() { resize(); frame(performance.now()) },
    dispose() { running = false; cancelAnimationFrame(handle); releaseQuality?.(); releaseQuality = null; ctx.clearRect(0, 0, canvas.width, canvas.height) },
  }
}
