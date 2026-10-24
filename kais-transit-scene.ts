/** Sinematik bilinç geçişi sahnesi (Canvas 2D, bağımlılıksız, yalnız görsel). Tek bir zaman çizelgesi (ms) ile sürülür; dört aşama:
 *  AWARENESS 0–350: göz bebeği genişler, iris halkaları farklı hızlarda döner, sinir ağı merkeze çekilir, ışık yoğunlaşır.
 *  SYNCHRONIZATION 350–750: küre içinde enerji dalgaları, parçacıklar çekirdekle etkileşir, (varsa) gerçek ses seviyesi ışığı sürer.
 *  TRANSIT 750–1250: kamera göz bebeğine ilerler, küre katmanları farklı hızlarda perspektifle dışa açılır, arka plan ışık tüneline döner,
 *  göz bebeği yuvarlak bir portal gibi açılmaya başlar. MATERIALIZATION 1100–1800: portal büyür; gerçek sayfa içinden görünür.
 *  Portal, tuvalde "destination-out" ile açılan gerçek bir delik: sayfa karanlık bir kutunun arkasında beklemez, doğrudan görünür.
 *  Ses/mikrofon/komut/yönlendirme koduna dokunmaz; sayfa değişimi dışarıdan (commit) yapılır. */

type V3 = [number, number, number]
const TAU = Math.PI * 2
const CYAN: V3 = [53, 221, 226], SOFT: V3 = [101, 240, 222], ICE: V3 = [181, 255, 255]
const cl = (v: number) => Math.max(0, Math.min(1, v))
const easeOut = (x: number) => 1 - Math.pow(1 - x, 3)
const easeInOut = (x: number) => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2
const rgba = (c: V3, a: number) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${Math.max(0, Math.min(1, a)).toFixed(3)})`
const seeded = (seed: number) => { let s = seed >>> 0; return () => { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296 } }

export const TRANSIT = {commitAt: 620, holdAt: 1100, endAt: 1800}

export type TransitSceneOptions = {
  canvas: HTMLCanvasElement
  origin: {x: number; y: number; size: number}
  core: {x: number; y: number; r: number} | null    // hologram çekirdeği ekrandaysa onun konumu: sahne kesintisiz devam eder
  lite: boolean
  label: string
  glyph: string
  getLevel: () => number
}

export function createTransitScene(options: TransitSceneOptions) {
  const context = options.canvas.getContext('2d')
  if (!context) return null
  const ctx: CanvasRenderingContext2D = context
  const canvas = options.canvas, lite = options.lite
  const rnd = seeded(8812024)

  // ---- Sabit geometri ----
  const LAYERS = [
    {r: .4, n: lite ? 4 : 6, tilt: 1.0, roll: .3, w: .9}, {r: .55, n: lite ? 5 : 8, tilt: 1.15, roll: -.4, w: .7},
    {r: .7, n: lite ? 6 : 10, tilt: .9, roll: .8, w: .55}, {r: .84, n: lite ? 7 : 12, tilt: 1.25, roll: -.9, w: .4},
    {r: .96, n: lite ? 8 : 14, tilt: 1.05, roll: .15, w: .3},
  ]
  type NetNode = {layer: number; ang: number; yo: number}
  const nodes: NetNode[] = []
  const layerStart: number[] = []
  LAYERS.forEach((layer, li) => { layerStart.push(nodes.length); for (let k = 0; k < layer.n; k++) nodes.push({layer: li, ang: (k / layer.n) * TAU + li * .4, yo: (rnd() * 2 - 1) * .1}) })
  const links: [number, number][] = []
  nodes.forEach((node, i) => {
    const layer = LAYERS[node.layer], index = i - layerStart[node.layer]
    links.push([i, layerStart[node.layer] + (index + 1) % layer.n])
    const next = LAYERS[node.layer + 1]
    if (next) links.push([i, layerStart[node.layer + 1] + Math.round(index * next.n / layer.n) % next.n])
  })
  const DUST = Array.from({length: lite ? 36 : 90}, () => ({a: rnd() * TAU, r: .25 + rnd() * 1.1, z: rnd() * 2 - 1, s: .6 + rnd() * 1.4, size: .6 + rnd() * 1.4}))
  const STREAKS = Array.from({length: lite ? 40 : 96}, () => ({a: rnd() * TAU, d: .08 + Math.pow(rnd(), .7) * .95, w: .5 + rnd() * 1.1, al: .25 + rnd() * .6}))
  const FIBERS = Array.from({length: lite ? 56 : 120}, () => ({a: rnd() * TAU, in: .02 + rnd() * .1, out: .66 + rnd() * .3, w: .5 + rnd() * .9, al: .1 + rnd() * .3}))
  const glyph = (() => { try { return new Path2D(options.glyph) } catch { return null } })()

  // ---- Parıltı sprite'ı (gradyan üretmeden çizim) ----
  const sprite = document.createElement('canvas'); sprite.width = sprite.height = 64
  const sg = sprite.getContext('2d')
  if (sg) { const gr = sg.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, 'rgba(255,255,255,1)'); gr.addColorStop(.3, 'rgba(255,255,255,.4)'); gr.addColorStop(1, 'rgba(255,255,255,0)'); sg.fillStyle = gr; sg.fillRect(0, 0, 64, 64) }
  const tinted = new Map<number, HTMLCanvasElement>()
  const glowSprite = (c: V3) => {
    const key = (c[0] << 16) | (c[1] << 8) | c[2]
    let s = tinted.get(key)
    if (!s) {
      s = document.createElement('canvas'); s.width = s.height = 64
      const g = s.getContext('2d')
      if (g) { g.drawImage(sprite, 0, 0); g.globalCompositeOperation = 'source-in'; g.fillStyle = rgba(c, 1); g.fillRect(0, 0, 64, 64) }
      tinted.set(key, s)
    }
    return s
  }

  let W = 0, H = 0, dpr = 1, reach = 1, targetR = 1, zEnd = 20
  let level = 0, lastClock = 0

  function resize() {
    W = canvas.clientWidth || window.innerWidth; H = canvas.clientHeight || window.innerHeight
    dpr = Math.min(window.devicePixelRatio || 1, lite ? 1 : 1.5, (lite ? 760 : 1280) / Math.max(W, 1))
    const pw = Math.max(2, Math.round(W * dpr)), ph = Math.max(2, Math.round(H * dpr))
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph }
    reach = Math.hypot(W / 2, H / 2)
    targetR = options.core ? options.core.r : Math.min(W, H) * .3
    zEnd = (reach * 1.12) / (targetR * .33 * .5)
  }

  /** tt: etkin zaman çizelgesi (ms); clock: sürekli saat (sn, ortam hareketi); gate: portalın "hazır mı" bilgisi zaman çizelgesine zaten yansır. */
  function draw(tt: number, clock: number) {
    if (!W) resize()
    const dt = Math.min(.08, lastClock ? clock - lastClock : .016); lastClock = clock
    level += (options.getLevel() - level) * (1 - Math.exp(-dt / .12))
    const aw = cl(tt / 350), sy = cl((tt - 350) / 400), tr = cl((tt - 750) / 500)
    const hole = cl((tt - TRANSIT.holdAt) / (TRANSIT.endAt - TRANSIT.holdAt))
    const fadeIn = easeOut(cl(tt / 220))
    const Z = Math.exp(Math.log(zEnd) * Math.pow(tr, 2.2))
    const mk = (k: number) => Math.pow(Z, k)
    // merkez ve yarıçap: hologram ekrandaysa sabit; üst menüdeki gözden geliyorsa ekran ortasına süzülür
    const o = options.origin
    const center = options.core ? {x: options.core.x, y: options.core.y} : {x: o.x + (W / 2 - o.x) * easeOut(aw), y: o.y + (H / 2 - o.y) * easeOut(aw)}
    const Rs = options.core ? options.core.r : (o.size * 1.1) + (targetR - o.size * 1.1) * easeOut(aw)
    const cx = center.x, cy = center.y
    const spin = clock * .5 + aw * .8
    const glow = (.85 + aw * .6 + sy * .3 + level * .5)
    const eyeR = Rs * .33 * Z
    const dil = .36 + .12 * easeOut(aw) + level * .04
    const pupilR = eyeR * dil

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.globalCompositeOperation = 'source-over'
    ctx.clearRect(0, 0, W, H)
    ctx.lineCap = 'round'
    // 0) Arka plan: hologram ekranıyla aynı koyu petrol zemin; geçişte ışık tüneline döner
    ctx.globalAlpha = fadeIn
    const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, reach * 1.1)
    bg.addColorStop(0, '#0b2e3d'); bg.addColorStop(.45, '#05161f'); bg.addColorStop(1, '#02080e')
    ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H)
    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = 'lighter'
    const dot = (x: number, y: number, size: number, c: V3, a: number) => {
      if (a <= .004 || size < .5) return
      ctx.globalAlpha = Math.min(1, a) * fadeIn; ctx.drawImage(glowSprite(c), x - size / 2, y - size / 2, size, size); ctx.globalAlpha = 1
    }
    // geniş merkez ışığı (aşama ilerledikçe yoğunlaşır)
    dot(cx, cy, Rs * 3.4 * (1 + tr * 2), CYAN, .42 * glow * (1 - hole * .6))

    // 1) Işık tüneli: göz bebeğinin kenarından dışa akan ışık çizgileri ve eş merkezli halkalar (kamera ilerledikçe belirginleşir)
    if (tr > 0.01 || sy > .6) {
      const strength = Math.pow(tr, 1.1) * 1.1 + sy * .08
      const edge = Math.max(pupilR, Rs * .05)
      for (const s of STREAKS) {
        const travel = ((clock * (.35 + tr * 1.4) * s.al + s.d * 3) % 1)
        const r1 = edge * (1.08 + travel * 2.4 + s.d * .8)
        const r2 = r1 + edge * (.18 + tr * 1.1) * (.4 + s.w * .5)
        if (r1 > reach * 1.9) continue
        const fade = Math.sin(Math.min(1, travel) * Math.PI)
        const x1 = cx + Math.cos(s.a) * r1, y1 = cy + Math.sin(s.a) * r1, x2 = cx + Math.cos(s.a) * r2, y2 = cy + Math.sin(s.a) * r2
        ctx.strokeStyle = rgba(s.al > .55 ? ICE : SOFT, strength * s.al * fade * .85 * fadeIn); ctx.lineWidth = s.w * (1 + tr * 1.2)
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke()
      }
      for (let k = 0; k < (lite ? 4 : 7); k++) {
        const u = ((clock * (.5 + tr) * .6 + k / (lite ? 4 : 7)) % 1)
        const radius = edge * (1.06 + Math.pow(u, 1.7) * 3.4)
        if (radius > reach * 1.8) continue
        ctx.strokeStyle = rgba(k % 2 ? SOFT : CYAN, strength * .45 * (1 - u) * fadeIn); ctx.lineWidth = 1.2 + (1 - u) * 1.6
        ctx.beginPath(); ctx.arc(cx, cy, radius, 0, TAU); ctx.stroke()
      }
    }

    // 2) Perspektifli küre telleri: dış hale, dış kabuk, iç küre farklı hız ve yönlerde; transitte farklı oranlarda dışa açılır
    const project = (p: V3, ay: number, ax: number, scale: number): [number, number, number] => {
      const x1 = p[0] * Math.cos(ay) + p[2] * Math.sin(ay), z1 = -p[0] * Math.sin(ay) + p[2] * Math.cos(ay)
      const y2 = p[1] * Math.cos(ax) - z1 * Math.sin(ax), z2 = p[1] * Math.sin(ax) + z1 * Math.cos(ax)
      const f = 1 / (1 - .22 * z2)
      return [cx + x1 * f * scale, cy + y2 * f * scale, z2]
    }
    const wire = (rad: number, rot: number, tilt: number, alpha: number, lats: number[], lons: number, scale: number) => {
      if (scale * rad > reach * 3.2 || alpha <= .004) return
      ctx.lineWidth = 1
      const pass = (fn: (a: number) => V3, steps: number, color: V3) => {
        let prev: [number, number, number] | null = null
        ctx.beginPath()
        for (let i = 0; i <= steps; i++) {
          const q = project(fn((i / steps) * TAU), rot, tilt, scale)
          if (prev && (q[2] > 0 || prev[2] > 0)) { ctx.moveTo(prev[0], prev[1]); ctx.lineTo(q[0], q[1]) }
          prev = q
        }
        ctx.strokeStyle = rgba(color, alpha); ctx.stroke()
      }
      for (const deg of lats) { const lat = deg * Math.PI / 180; pass(a => [Math.cos(lat) * Math.cos(a) * rad, Math.sin(lat) * rad, Math.cos(lat) * Math.sin(a) * rad], 48, ICE) }
      for (let m = 0; m < lons; m++) { const lon = (m / lons) * Math.PI; pass(a => [Math.cos(a) * Math.cos(lon) * rad, Math.sin(a) * rad, Math.cos(a) * Math.sin(lon) * rad], 48, SOFT) }
    }
    const keep = 1 - hole * .9
    wire(1.065, -spin * .4 + .8, -.1, .16 * glow * keep * fadeIn, [-48, 0, 48], 6, Rs * mk(1.5))
    wire(1, spin, -.2, .24 * glow * keep * fadeIn, [-60, -35, -12, 12, 35, 60], 9, Rs * mk(1.25))
    wire(.62, -spin * (1.9 + aw * 1.4), -.1, .22 * glow * keep * fadeIn, [-50, -15, 15, 50], 6, Rs * mk(.8))

    // 3) Sinir ağı: merkeze çekilir (awareness), enerji dalgasıyla titreşir (sync), katmanlar farklı oranda dışa açılır (transit)
    const pull = 1 - .26 * easeOut(aw) * (1 - sy) + Math.sin(sy * Math.PI) * .06
    const pts = nodes.map(node => {
      const L = LAYERS[node.layer]
      const a = node.ang + clock * L.w * .35 * (1 + aw)
      const x = Math.cos(a) * L.r, z0 = Math.sin(a) * L.r
      const y1 = -z0 * Math.sin(L.tilt), z1 = z0 * Math.cos(L.tilt)
      const p: V3 = [x * Math.cos(L.roll) - y1 * Math.sin(L.roll), x * Math.sin(L.roll) + y1 * Math.cos(L.roll) + node.yo, z1]
      return project(p, spin * 1.3, -.2, Rs * pull * mk(.95 + node.layer * .03))
    })
    const eyeClear = Rs * .33 * 1.1 * Z
    ctx.lineWidth = 1.1
    links.forEach(([a, b]) => {
      const pa = pts[a], pb = pts[b]
      const mid = Math.hypot((pa[0] + pb[0]) / 2 - cx, (pa[1] + pb[1]) / 2 - cy)
      if (mid < eyeClear * .9 && tr > 0) return
      const act = Math.max(0, Math.sin((sy * 1.6 - LAYERS[nodes[a].layer].r) * Math.PI)) * sy
      ctx.strokeStyle = rgba(act > .3 ? ICE : SOFT, (.16 + act * .5 + aw * .1) * glow * keep * fadeIn)
      ctx.beginPath(); ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]); ctx.stroke()
    })
    pts.forEach((q, i) => {
      const dist = Math.hypot(q[0] - cx, q[1] - cy)
      if (dist < eyeClear * .9 && tr > 0) return
      dot(q[0], q[1], Rs * .045 * (1 + (nodes[i].layer === 4 ? .3 : 0)) * (1 + sy * .5), ICE, (.55 + (q[2] + 1) * .2) * glow * keep)
    })

    // 4) Parçacıklar çekirdekle etkileşir: awareness'ta yavaş, sync'te içe doğru spiral, transitte çizgiye döner
    for (const p of DUST) {
      const attract = easeOut(sy) * (1 - tr) * .55
      const radius = p.r * (1 - attract * (1 - Math.min(1, p.r)) ) * Rs * mk(1.35)
      const a = p.a + clock * p.s * (.35 + sy * 1.2)
      const x = cx + Math.cos(a) * radius, y = cy + Math.sin(a) * radius * (.55 + .45 * Math.abs(p.z))
      if (Math.hypot(x - cx, y - cy) > reach * 1.6) continue
      dot(x, y, 4 + p.size * 5 * (1 + level), p.z > 0 ? ICE : SOFT, (.28 + p.z * .12 + level * .3) * glow * keep)
    }

    // 5) Enerji dalgaları: sync boyunca çekirdekten içeri-dışarı yayılır
    for (let w = 0; w < 3; w++) {
      const u = cl((tt - 330 - w * 150) / 520)
      if (u <= 0 || u >= 1) continue
      ctx.strokeStyle = rgba(ICE, (1 - u) * (1 - u) * .6 * fadeIn * keep); ctx.lineWidth = 1.2 + (1 - u) * 2
      ctx.beginPath(); ctx.arc(cx, cy, Rs * (.3 + u * .8) * mk(1.0), 0, TAU); ctx.stroke()
    }

    // 6) Göz: iris halkaları farklı hızlarda döner, göz bebeği organik ve genişler; kamera ilerledikçe ekranı kaplar
    ctx.globalCompositeOperation = 'source-over'
    if (eyeR < reach * 6) {
      const base = ctx.createRadialGradient(cx, cy, eyeR * .12, cx, cy, eyeR)
      base.addColorStop(0, '#c8ffff'); base.addColorStop(.26, '#68e8f2'); base.addColorStop(.55, '#1692bc'); base.addColorStop(.84, '#073c5e'); base.addColorStop(1, '#021220')
      ctx.globalAlpha = fadeIn; ctx.fillStyle = base; ctx.beginPath(); ctx.arc(cx, cy, eyeR, 0, TAU); ctx.fill(); ctx.globalAlpha = 1
      ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, eyeR, 0, TAU); ctx.clip()
      ctx.globalCompositeOperation = 'lighter'
      const lum = (.8 + level * .7 + aw * .35) * Math.max(0, 1 - tr * .6)
      for (const f of FIBERS) {
        const a = f.a + clock * .12 * (.4 + f.in * 6)   // iç lifler yavaş, dış lifler hızlı
        const r1 = eyeR * (.3 + f.in), r2 = eyeR * f.out
        ctx.strokeStyle = rgba(ICE, f.al * lum * fadeIn); ctx.lineWidth = f.w
        ctx.beginPath(); ctx.moveTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1); ctx.lineTo(cx + Math.cos(a + .05) * r2, cy + Math.sin(a + .05) * r2); ctx.stroke()
      }
      // iris halkaları: üç halka, üç farklı hız ve yön
      const ringSpeeds = [.9, -1.6, 2.6]
      ringSpeeds.forEach((speed, i) => {
        const rr = eyeR * (.52 + i * .13), rot = clock * speed * (1 + aw * 1.5)
        ctx.setLineDash([eyeR * .06, eyeR * .035 * (i + 1)]); ctx.lineDashOffset = -rot * eyeR * .3
        ctx.strokeStyle = rgba(i === 1 ? SOFT : ICE, (.35 - i * .06) * lum * fadeIn); ctx.lineWidth = 1.4
        ctx.beginPath(); ctx.arc(cx, cy, rr, 0, TAU); ctx.stroke()
      })
      ctx.setLineDash([])
      ctx.restore()
      ctx.globalCompositeOperation = 'source-over'
      ctx.strokeStyle = rgba(ICE, (.55 + level * .3) * fadeIn); ctx.lineWidth = 1.6; ctx.beginPath(); ctx.arc(cx, cy, eyeR, 0, TAU); ctx.stroke()
    }
    // organik göz bebeği
    const pr = (a: number) => pupilR * (1 + .02 * Math.sin(3 * a + clock * .6) + .014 * Math.sin(5 * a - clock * .45 + 1.2))
    const pupilPath = (k: number) => { ctx.beginPath(); for (let i = 0; i <= 72; i++) { const a = (i / 72) * TAU, r = pr(a) * k; const x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r; if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y) } ctx.closePath() }
    ctx.globalAlpha = fadeIn; ctx.fillStyle = '#01070c'; pupilPath(1); ctx.fill(); ctx.globalAlpha = 1
    if (tr > .35 && hole === 0) {
      // geçit açılmadan önce göz bebeğinin içi boş kara kalmasın: derinden gelen yumuşak ışık
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, pupilR)
      const a = cl((tr - .35) / .5) * (.28 + level * .2)
      g.addColorStop(0, rgba(ICE, a)); g.addColorStop(.55, rgba(CYAN, a * .45)); g.addColorStop(1, rgba(CYAN, 0))
      ctx.globalCompositeOperation = 'lighter'; ctx.fillStyle = g; pupilPath(1); ctx.fill(); ctx.globalCompositeOperation = 'source-over'
    }
    ctx.globalCompositeOperation = 'lighter'
    ctx.strokeStyle = rgba(ICE, (.45 + level * .4 + tr * .3) * fadeIn); ctx.lineWidth = 1.2 + level * 2 + tr * 2; pupilPath(1.02); ctx.stroke()
    ctx.strokeStyle = rgba(CYAN, (.18 + level * .3 + tr * .25) * fadeIn); ctx.lineWidth = 3 + level * 6 + tr * 10; pupilPath(1.1); ctx.stroke()
    ctx.globalCompositeOperation = 'source-over'

    // 7) Sayfa etiketi: göz bebeği ekranı kaplarken beliren ikon ve ad
    const tagA = cl((tr - .45) / .3) * (1 - cl((hole - .02) / .12))
    if (tagA > .01) {
      ctx.save(); ctx.globalAlpha = tagA; ctx.translate(cx, cy)
      ctx.fillStyle = 'rgba(200,255,245,.95)'; ctx.strokeStyle = 'rgba(200,255,245,.95)'
      if (glyph) { ctx.save(); ctx.translate(-17, -34); ctx.scale(34 / 24, 34 / 24); ctx.lineWidth = 1.5; ctx.lineJoin = 'round'; ctx.stroke(glyph); ctx.restore() }
      ctx.font = '600 13px system-ui, -apple-system, Segoe UI, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'top'
      const spacing = (ctx as unknown as {letterSpacing?: string})
      if ('letterSpacing' in ctx) spacing.letterSpacing = '3px'
      ctx.fillText(options.label.toLocaleUpperCase(), 0, 12)
      ctx.restore()
    }

    // 8) PORTAL: tuvalde gerçek bir delik; gerçek sayfa içinden görünür, kenarda ince ışık halkası
    if (hole > 0) {
      const r0 = Math.min(pupilR * .97, reach * 1.1), radius = r0 + easeInOut(hole) * Math.max(0, reach * 1.15 - r0) + 2
      ctx.globalCompositeOperation = 'destination-out'
      const soft = Math.max(6, radius * .05)
      const cut = ctx.createRadialGradient(cx, cy, Math.max(0, radius - soft), cx, cy, radius + 1)
      cut.addColorStop(0, 'rgba(0,0,0,1)'); cut.addColorStop(1, 'rgba(0,0,0,0)')
      ctx.fillStyle = cut; ctx.beginPath(); ctx.arc(cx, cy, radius + 1, 0, TAU); ctx.fill()
      ctx.globalCompositeOperation = 'lighter'
      const ringA = (1 - cl((hole - .55) / .45)) * .9
      ctx.strokeStyle = rgba(ICE, ringA); ctx.lineWidth = 1.6; ctx.beginPath(); ctx.arc(cx, cy, radius, 0, TAU); ctx.stroke()
      ctx.strokeStyle = rgba(CYAN, ringA * .5); ctx.lineWidth = 10 * (1 - hole * .6); ctx.beginPath(); ctx.arc(cx, cy, radius + 2, 0, TAU); ctx.stroke()
      ctx.globalCompositeOperation = 'source-over'
    }
  }

  return {resize, draw, dispose() { ctx.clearRect(0, 0, canvas.width, canvas.height); tinted.clear() }}
}
