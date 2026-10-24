/** Kais "yaşayan dijital evren" arka plan motoru (Canvas 2D, bağımlılıksız, yalnız dekoratif).
 *  Katmanlar (arkadan öne): geniş turkuaz hale + yavaş ışık kümeleri → uzak sinir ağı ve uzak (bulanık) parçacıklar → orta sinir ağı ve orta parçacıklar
 *  → ağ üzerinde gezen enerji parçacıkları → merkeze akan, aralıklı ince veri akışları → perspektifli dijital zemin → ön plan (iri, yumuşak) parçacıklar.
 *  Durum (dinliyor/düşünüyor/konuşuyor/hata) ve ses seviyesi dışarıdan GERÇEK uygulama durumundan verilir; burada durum uydurulmaz.
 *  Merkezi hologram (konum ve yarıçap) çizimde dışarıdan alınır: ağ ve akışlar hologramla yarışmaz, ona yaklaştıkça hafifçe parlar ve gözden önce söner. */

export type UniversePhase = 'listening' | 'thinking' | 'speaking' | 'error'
export type UniverseCore = {x: number; y: number; r: number}
export type UniverseOptions = {
  getPhase: () => UniversePhase
  getLevel: () => number
  getParallax: () => [number, number]
  getCore: () => UniverseCore | null
  lite: boolean
  reduced: boolean
}

type RGB = [number, number, number]
const TAU = Math.PI * 2
const NORMAL = {soft: [101, 240, 222], ice: [181, 255, 255], cyan: [53, 221, 226], petrol: [24, 118, 150]} as Record<string, RGB>
const ALARM = {soft: [240, 140, 148], ice: [255, 205, 210], cyan: [240, 107, 117], petrol: [140, 50, 62]} as Record<string, RGB>

const seeded = (seed: number) => { let state = seed >>> 0; return () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 4294967296 } }
const clamp01 = (v: number) => Math.max(0, Math.min(1, v))
const smooth = (a: number, b: number, v: number) => { const k = clamp01((v - a) / (b - a)); return k * k * (3 - 2 * k) }

const PHASE: Record<UniversePhase, {glow: number; net: number; rate: number; flow: number; err: number}> = {
  listening: {glow: .85, net: .8, rate: 1, flow: 1, err: 0},
  thinking: {glow: 1, net: 1, rate: 1.9, flow: 1.6, err: 0},
  speaking: {glow: 1, net: .95, rate: 1.4, flow: 1.3, err: 0},
  error: {glow: .3, net: .28, rate: .12, flow: .25, err: 1},
}

type Sprites = Record<'soft' | 'ice' | 'cyan' | 'petrol' | 'bokeh', HTMLCanvasElement>

function buildSprites(palette: Record<string, RGB>): Sprites {
  const make = (rgb: RGB, size: number, stops: [number, number][]) => {
    const c = document.createElement('canvas'); c.width = c.height = size
    const g = c.getContext('2d')
    if (g) {
      const r = size / 2, gr = g.createRadialGradient(r, r, 0, r, r, r)
      stops.forEach(([at, a]) => gr.addColorStop(at, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`))
      g.fillStyle = gr; g.fillRect(0, 0, size, size)
    }
    return c
  }
  return {
    soft: make(palette.soft, 64, [[0, 1], [.3, .35], [1, 0]]),
    ice: make(palette.ice, 64, [[0, 1], [.2, .5], [1, 0]]),
    cyan: make(palette.cyan, 128, [[0, 1], [.4, .3], [1, 0]]),
    petrol: make(palette.petrol, 128, [[0, .9], [.5, .3], [1, 0]]),
    bokeh: make(palette.soft, 128, [[0, .1], [.78, .16], [.92, .55], [1, 0]]),
  }
}

type Node = {bx: number; by: number; ph: number; sw: number}
type NetLayer = {nodes: Node[]; links: [number, number][]; adj: number[][]; depth: number; alpha: number; rate: number[]; phase: number[]}

export function createUniverse(canvas: HTMLCanvasElement, options: UniverseOptions) {
  const context = canvas.getContext('2d')
  if (!context) return null
  const ctx: CanvasRenderingContext2D = context
  const lite = options.lite
  const rnd = seeded(4172026)
  const sprites = {normal: buildSprites(NORMAL), alarm: buildSprites(ALARM)}

  // ---- Sinir ağı katmanları: sabit, ekranı kaplayan, hafif kaydırılmış ızgara düğümleri; komşu düğümler bağlanır ----
  const ASPECT = 1.78
  const makeLayer = (cols: number, rows: number, depth: number, alpha: number): NetLayer => {
    const nodes: Node[] = []
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) nodes.push({bx: (c + .5 + (rnd() - .5) * .75) / cols, by: (r + .5 + (rnd() - .5) * .75) / rows, ph: rnd() * TAU, sw: .03 + rnd() * .05})
    const links: [number, number][] = [], adj: number[][] = nodes.map(() => [])
    const reach = (1.55 * Math.max(ASPECT / cols, 1 / rows)) ** 2
    nodes.forEach((a, i) => {
      nodes.map((b, j) => ({j, d: ((a.bx - b.bx) * ASPECT) ** 2 + (a.by - b.by) ** 2})).filter(x => x.j > i && x.d < reach)
        .sort((x, y) => x.d - y.d).slice(0, 3).forEach(x => { if (rnd() > .22) { links.push([i, x.j]); adj[i].push(x.j); adj[x.j].push(i) } })
    })
    return {nodes, links, adj, depth, alpha, rate: links.map(() => .18 + rnd() * .3), phase: links.map(() => rnd() * TAU)}
  }
  const layers = [makeLayer(lite ? 10 : 22, lite ? 6 : 12, .3, .55), makeLayer(lite ? 6 : 12, lite ? 4 : 7, .65, .85)]
  // Ağ üzerinde gezen enerji parçacıkları: bir bağlantı boyunca ilerler, ucuna varınca komşu bir bağlantıya geçer
  const walkers = Array.from({length: lite ? 7 : 20}, () => {
    const layer = Math.floor(rnd() * 2), from = Math.floor(rnd() * layers[layer].nodes.length)
    return {layer, from, to: layers[layer].adj[from][0] ?? from, u: rnd(), speed: .12 + rnd() * .16}
  })

  // ---- Parçacık katmanları: uzak (küçük, bulanık), orta, ön (iri, yumuşak bokeh) ----
  const mkParticles = (count: number, size: [number, number], speed: [number, number], alpha: [number, number]) =>
    Array.from({length: count}, () => ({x: rnd(), y: rnd(), size: size[0] + rnd() * (size[1] - size[0]), speed: speed[0] + rnd() * (speed[1] - speed[0]), alpha: alpha[0] + rnd() * (alpha[1] - alpha[0]), ph: rnd() * TAU, tw: .3 + rnd() * .9, sway: .08 + rnd() * .2, hot: rnd() < .25}))
  const far = mkParticles(lite ? 28 : 70, [7, 15], [.003, .008], [.1, .26])
  const mid = mkParticles(lite ? 16 : 42, [5, 11], [.006, .015], [.2, .45])
  const near = mkParticles(lite ? 3 : 8, [46, 120], [.01, .022], [.04, .1])
  const sharp = mkParticles(lite ? 2 : 4, [14, 24], [.012, .028], [.35, .6])
  const CLUSTERS = Array.from({length: lite ? 3 : 5}, (_, i) => ({ax: .12 + rnd() * .16, ay: .08 + rnd() * .1, fx: .03 + rnd() * .04, fy: .025 + rnd() * .04, ph: rnd() * TAU, bx: .15 + (i / 4) * .7, by: .25 + rnd() * .5, size: .28 + rnd() * .22, a: .045 + rnd() * .04, petrol: i % 2 === 0}))

  // ---- Veri akışları: kenarlardan merkeze doğru kıvrılan, aralıklı ince çizgiler ----
  type Stream = {x0: number; y0: number; cxp: number; cyp: number; x1: number; y1: number; born: number; dur: number; strands: number; gap: number; tail: number}
  const streams: Stream[] = []
  let nextStream = 1.2

  const live = {glow: .85, net: .8, rate: 1, flow: 1, err: 0, level: 0}
  let width = 0, height = 0, dpr = 1
  let last = 0
  let core: UniverseCore = {x: 0, y: 0, r: 200}
  let coreCheck = -9

  function resize() {
    const cssW = canvas.clientWidth || canvas.getBoundingClientRect().width || 1280
    const cssH = canvas.clientHeight || canvas.getBoundingClientRect().height || 720
    dpr = Math.min(window.devicePixelRatio || 1, lite ? 1.25 : 1.5)
    width = cssW; height = cssH
    const pw = Math.max(2, Math.round(cssW * dpr)), ph = Math.max(2, Math.round(cssH * dpr))
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph }
    coreCheck = -9
  }

  function frame(now: number, still = false) {
    if (!width) resize()
    const dt = Math.min(.08, last ? (now - last) / 1000 : .016); last = now
    const t = still ? 14 : now / 1000
    const phase = options.getPhase()
    const goal = PHASE[phase]
    const k = 1 - Math.exp(-dt / .6)
    live.glow += (goal.glow - live.glow) * k; live.net += (goal.net - live.net) * k; live.rate += (goal.rate - live.rate) * k
    live.flow += (goal.flow - live.flow) * k; live.err += (goal.err - live.err) * k
    live.level += (options.getLevel() - live.level) * (1 - Math.exp(-dt / .18))
    if (t - coreCheck > .5) { coreCheck = t; const c = options.getCore(); if (c) core = c; else core = {x: width / 2, y: height * .4, r: Math.min(width, height) * .3} }
    const [px, py] = options.getParallax()
    const sp = live.err > .5 ? sprites.alarm : sprites.normal
    const W = width, H = height, cx = core.x, cy = core.y, R = core.r
    const glow = live.glow, boost = 1 + live.level * .6

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, W, H)
    ctx.globalCompositeOperation = 'lighter'
    ctx.lineCap = 'round'
    const sprite = (img: HTMLCanvasElement, x: number, y: number, size: number, alpha: number) => {
      if (alpha <= .004) return
      ctx.globalAlpha = Math.min(1, alpha); ctx.drawImage(img, x - size / 2, y - size / 2, size, size)
    }
    const rgb = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${Math.max(0, Math.min(1, a)).toFixed(3)})`
    const pal = live.err > .5 ? ALARM : NORMAL
    // merkeze yakın ağ/akış sönük, uzaklaştıkça tam
    const away = (x: number, y: number) => .12 + .88 * smooth(1.15, 2.7, Math.hypot(x - cx, y - cy) / R)
    const par = (depth: number): [number, number] => [px * W * .012 * depth, py * H * .01 * depth]

    // 1) Ambiyans: hologramın arkasında geniş yumuşak turkuaz hale + yavaş hareket eden derin ışık kümeleri
    const halo = ctx.createRadialGradient(cx, cy, R * .3, cx, cy, R * 3.6)
    halo.addColorStop(0, rgb(pal.cyan, .2 * glow)); halo.addColorStop(.35, rgb(pal.petrol, .14 * glow)); halo.addColorStop(1, rgb(pal.petrol, 0))
    ctx.globalAlpha = 1; ctx.fillStyle = halo; ctx.fillRect(Math.max(0, cx - R * 3.6), Math.max(0, cy - R * 3.6), R * 7.2, R * 7.2)
    CLUSTERS.forEach(c => {
      const x = (c.bx + Math.sin(t * c.fx + c.ph) * c.ax) * W, y = (c.by + Math.cos(t * c.fy + c.ph) * c.ay) * H
      sprite(c.petrol ? sp.petrol : sp.cyan, x, y, Math.max(W, H) * c.size * 1.6, c.a * glow * (1 + live.level * .4))
    })

    // 2) Katmanlı sinir ağı + parçacıklar
    const drawNetwork = (layer: NetLayer) => {
      const [ox, oy] = par(layer.depth)
      const pos = layer.nodes.map(n => ({x: (n.bx + Math.sin(t * n.sw + n.ph) * .012) * W + ox, y: (n.by + Math.cos(t * n.sw * .8 + n.ph) * .014) * H + oy}))
      const baseAlpha = .13 * layer.alpha * live.net * glow
      // sabit ince bağlantılar (tek yol)
      ctx.globalAlpha = 1
      ctx.strokeStyle = rgb(pal.soft, baseAlpha); ctx.lineWidth = (layer.depth < .5 ? .8 : 1)
      ctx.beginPath()
      layer.links.forEach(([a, b]) => {
        const pa = pos[a], pb = pos[b]
        if (away((pa.x + pb.x) / 2, (pa.y + pb.y) / 2) < .3) return
        ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y)
      })
      ctx.stroke()
      // yavaşça ışıklanan bağlantılar
      layer.links.forEach(([a, b], i) => {
        const shimmer = Math.pow(Math.max(0, Math.sin(t * layer.rate[i] * live.rate + layer.phase[i])), 6)
        if (shimmer < .06) return
        const pa = pos[a], pb = pos[b]
        const f = away((pa.x + pb.x) / 2, (pa.y + pb.y) / 2)
        ctx.strokeStyle = rgb(pal.ice, shimmer * .3 * layer.alpha * live.net * glow * f * boost); ctx.lineWidth = 1
        ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke()
      })
      // düğümler
      layer.nodes.forEach((n, i) => {
        const f = away(pos[i].x, pos[i].y)
        const tw = .55 + .45 * Math.sin(t * .5 + n.ph * 3)
        sprite(sp.soft, pos[i].x, pos[i].y, (layer.depth < .5 ? 7 : 11) * (1 + tw * .3), (.3 + tw * .3) * layer.alpha * live.net * glow * f)
      })
      return pos
    }
    const drawParticles = (list: ReturnType<typeof mkParticles>, depth: number, kind: 'far' | 'mid' | 'near' | 'sharp') => {
      const [ox, oy] = par(depth)
      for (const p of list) {
        const y = ((p.y - t * p.speed * live.rate) % 1 + 1) % 1
        const x = p.x + Math.sin(t * p.sway + p.ph) * .012
        const fadeEdge = Math.min(1, y * 8, (1 - y) * 8)
        const tw = .65 + .35 * Math.sin(t * p.tw + p.ph)
        const sx = x * W + ox, sy = y * H + oy
        const f = kind === 'near' || kind === 'sharp' ? 1 : away(sx, sy) * .8 + .2
        const a = p.alpha * tw * fadeEdge * glow * f
        if (kind === 'far') sprite(sp.petrol, sx, sy, p.size * 2.2, a * 1.1)
        else if (kind === 'mid') { sprite(sp.soft, sx, sy, p.size * 2, a); if (p.hot) sprite(sp.ice, sx, sy, p.size * .7, a * 1.4) }
        else if (kind === 'near') sprite(sp.bokeh, sx, sy, p.size, a * 2.2)
        else sprite(sp.ice, sx, sy, p.size, a * .9)
      }
    }
    drawParticles(far, .3, 'far')
    const posFar = drawNetwork(layers[0])
    drawParticles(mid, .65, 'mid')
    const posMid = drawNetwork(layers[1])
    const posByLayer = [posFar, posMid]

    // 3) Ağ üzerinde gezen enerji parçacıkları
    walkers.forEach(w => {
      const layer = layers[w.layer], pos = posByLayer[w.layer]
      if (!options.reduced && !still) {
        w.u += dt * w.speed * live.rate * (1 + live.level * .8)
        if (w.u >= 1) {
          const prev = w.from; w.from = w.to; w.u = 0
          const options2 = layer.adj[w.from].filter(n => n !== prev)
          const list = options2.length ? options2 : layer.adj[w.from]
          if (list.length) {
            // dinlerken biraz merkeze doğru, diğer durumlarda serbest
            w.to = phase === 'listening' && rnd() < .55
              ? list.reduce((best, n) => Math.hypot(pos[n].x - cx, pos[n].y - cy) < Math.hypot(pos[best].x - cx, pos[best].y - cy) ? n : best, list[0])
              : list[Math.floor(rnd() * list.length)]
          }
        }
      }
      const a = pos[w.from], b = pos[w.to]
      if (!a || !b) return
      const x = a.x + (b.x - a.x) * w.u, y = a.y + (b.y - a.y) * w.u
      const f = away(x, y)
      const strength = Math.sin(w.u * Math.PI) * live.net * glow * f * boost
      ctx.strokeStyle = rgb(pal.ice, .35 * strength); ctx.lineWidth = 1.4
      ctx.beginPath(); ctx.moveTo(x - (b.x - a.x) * .12, y - (b.y - a.y) * .12); ctx.lineTo(x, y); ctx.stroke()
      sprite(sp.ice, x, y, 16, .85 * strength)
    })

    // 4) Dijital zemin: perspektifli ince ızgara, enerji çekirdeğine doğru uzanır
    const vpY = cy + R * .9
    if (vpY < H - 8) {
      const bottom = H, depthH = bottom - vpY
      const K = lite ? 8 : 14, scroll = still ? 0 : (t * .035) % 1
      for (let kx = 0; kx <= K; kx++) {
        const f = (kx + scroll) / K
        if (f > 1) continue
        const y = vpY + depthH * Math.pow(f, 2.2)
        const a = .3 * Math.pow(f, .75) * glow * (1 + live.level * .4)
        const g = ctx.createLinearGradient(0, 0, W, 0)
        g.addColorStop(0, rgb(pal.cyan, 0)); g.addColorStop(.5, rgb(pal.cyan, a)); g.addColorStop(1, rgb(pal.cyan, 0))
        ctx.globalAlpha = 1; ctx.strokeStyle = g; ctx.lineWidth = 1
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke()
      }
      const N = lite ? 9 : 17
      for (let i = 0; i < N; i++) {
        const u = i / (N - 1) - .5
        const ex = cx + u * W * 2.3, ey = bottom
        const g = ctx.createLinearGradient(cx, vpY, ex, ey)
        const a = .24 * (1 - Math.abs(u) * .8) * glow
        g.addColorStop(0, rgb(pal.cyan, 0)); g.addColorStop(.25, rgb(pal.cyan, a * .5)); g.addColorStop(1, rgb(pal.cyan, a))
        ctx.strokeStyle = g; ctx.lineWidth = 1
        ctx.beginPath(); ctx.moveTo(cx, vpY); ctx.lineTo(ex, ey); ctx.stroke()
      }
      // ufuk çizgisinde yumuşak ışık
      ctx.save(); ctx.translate(cx, vpY); ctx.scale(1, .08); sprite(sp.cyan, 0, 0, W * .9, .22 * glow); ctx.restore()
    }

    // 5) Veri akışları: aralıklı, doğal; merkeze yaklaşırken parlar, gözden önce söner
    if (!options.reduced && !still) {
      const maxStreams = lite ? 3 : 7
      if (t > nextStream && streams.length < maxStreams && phase !== 'error') {
        const theta = rnd() * TAU, dx = Math.cos(theta), dy = Math.sin(theta)
        // merkezden dışarı ışın ile ekran kenarı kesişimi
        const tx = dx > 0 ? (W + 20 - cx) / dx : dx < 0 ? (-20 - cx) / dx : Infinity
        const ty = dy > 0 ? (H + 20 - cy) / dy : dy < 0 ? (-20 - cy) / dy : Infinity
        const dist = Math.min(tx, ty)
        const x0 = cx + dx * dist, y0 = cy + dy * dist
        const th2 = theta + (rnd() - .5) * .5
        const x1 = cx + Math.cos(th2) * R * 1.3, y1 = cy + Math.sin(th2) * R * 1.3
        const curve = (rnd() < .5 ? -1 : 1) * (.08 + rnd() * .16)
        const mx = (x0 + x1) / 2 + -(y1 - y0) * curve, my = (y0 + y1) / 2 + (x1 - x0) * curve
        streams.push({x0, y0, cxp: mx, cyp: my, x1, y1, born: t, dur: 3.2 + rnd() * 2.4, strands: 1 + Math.floor(rnd() * 3), gap: 3 + rnd() * 3, tail: .22 + rnd() * .2})
        nextStream = t + (1.1 + rnd() * 2.6) / (live.flow * Math.max(.2, live.rate))
      }
    }
    for (let i = streams.length - 1; i >= 0; i--) {
      const s = streams[i], raw = (t - s.born) / s.dur
      if (raw >= 1.3) { streams.splice(i, 1); continue }
      const head = Math.min(1, raw), fadeIn = smooth(0, .12, raw), fadeOut = 1 - smooth(.88, 1.2, raw)
      const bez = (v: number, off: number): [number, number] => {
        const a = (1 - v) * (1 - v), b = 2 * (1 - v) * v, c = v * v
        const x = a * s.x0 + b * s.cxp + c * s.x1, y = a * s.y0 + b * s.cyp + c * s.y1
        const nx = -(s.y1 - s.y0), ny = s.x1 - s.x0, nl = Math.hypot(nx, ny) || 1
        const spread = off * (1 - v) * 5
        return [x + nx / nl * spread, y + ny / nl * spread]
      }
      for (let st = 0; st < s.strands; st++) {
        const off = st - (s.strands - 1) / 2
        const SEG = lite ? 8 : 14
        let [lx, ly] = bez(Math.max(0, head - s.tail), off)
        for (let q = 1; q <= SEG; q++) {
          const v = Math.max(0, head - s.tail) + (Math.min(head, 1) - Math.max(0, head - s.tail)) * (q / SEG)
          const [x, y] = bez(v, off)
          const alongTail = q / SEG
          const alpha = (.07 + .5 * Math.pow(v, 1.6)) * alongTail * alongTail * fadeIn * fadeOut * glow * boost * live.flow * 1.25
          ctx.globalAlpha = 1; ctx.strokeStyle = rgb(pal.ice, alpha); ctx.lineWidth = .9 + (st === 0 ? .4 : 0)
          ctx.beginPath(); ctx.moveTo(lx, ly); ctx.lineTo(x, y); ctx.stroke()
          lx = x; ly = y
        }
        if (st === 0) { const [hx, hy] = bez(Math.min(head, 1), off); sprite(sp.ice, hx, hy, 16 + 12 * head, (.3 + .7 * head) * fadeIn * fadeOut * glow) }
      }
    }

    // 6) Ön plan: iri yumuşak bokeh parçacıkları + birkaç belirgin parçacık
    drawParticles(near, 1.5, 'near')
    drawParticles(sharp, 1.2, 'sharp')

    ctx.globalAlpha = 1
    ctx.globalCompositeOperation = 'source-over'
  }

  let handle = 0, running = false
  // Arka plan yavaş hareket eder: ~36 kare/sn yeterli; çekirdek ve göz tam hızda çizilmeye devam eder (CPU/GPU payı korunur)
  let drawn = 0
  const loop = (now: number) => { if (!running) return; if (now - drawn >= 27) { drawn = now; frame(now) } handle = requestAnimationFrame(loop) }
  return {
    resize,
    start() { if (running) return; running = true; last = 0; if (options.reduced) { resize(); frame(performance.now(), true); running = false; return } handle = requestAnimationFrame(loop) },
    stop() { running = false; cancelAnimationFrame(handle) },
    redraw() { resize(); frame(performance.now(), true) },
    dispose() { running = false; cancelAnimationFrame(handle); ctx.clearRect(0, 0, canvas.width, canvas.height) },
  }
}
