import { useEffect, useRef } from 'react'

type Flake = {x: number; y: number; vy: number; r: number; a: number; phase: number; sway: number}
type Rest = {x: number; y: number; r: number; a: number; born: number; life: number}
type Surface = {left: number; right: number; top: number}

/** Kayıtlı yazı/alan kenarlarına konabilen yüzeyler: başlık, slogan, logo, alanlar ve düğmeler. */
const TARGETS: {selector: string; inset: number; dy: number}[] = [
  {selector: '.authMobileIdentity img', inset: 6, dy: 3},
  {selector: '.authCardHead h2', inset: 4, dy: 0},
  {selector: '.authCardLead', inset: 4, dy: 3},
  {selector: '.authCardLogin form input:not([type="checkbox"])', inset: 16, dy: 0},
  {selector: '.authCardLogin .authSubmit', inset: 16, dy: 0},
  {selector: '.authCardLogin .authGoogle', inset: 16, dy: 0},
  {selector: '.authCardLogin .authInstagramFollow', inset: 16, dy: 0},
]
const MAX_FLAKES = 68
const MAX_RESTING = 28
const LAND_CHANCE = 0.09

function sprite(size: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  const px = Math.ceil(size * 6)
  canvas.width = canvas.height = px
  const ctx = canvas.getContext('2d')!
  const gradient = ctx.createRadialGradient(px / 2, px / 2, 0, px / 2, px / 2, px / 2)
  gradient.addColorStop(0, 'rgba(255,255,255,1)')
  gradient.addColorStop(0.28, 'rgba(240,248,255,.85)')
  gradient.addColorStop(1, 'rgba(225,238,255,0)')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, px, px)
  return canvas
}

/** Yavaş, doğal kar: taneler yazıların ve alanların üstüne konup yaklaşık 2 saniye sonra erir. */
export default function SnowFall() {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas || (typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    let width = 0, height = 0
    const resize = () => {
      width = window.innerWidth; height = window.innerHeight
      canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    resize()
    const sprites = [sprite(2), sprite(3.4), sprite(5)]
    const flakes: Flake[] = []
    const resting: Rest[] = []
    let surfaces: Surface[] = []
    const measure = () => {
      const next: Surface[] = []
      for (const target of TARGETS) {
        document.querySelectorAll(target.selector).forEach(element => {
          const rect = element.getBoundingClientRect()
          if (rect.width < 24 || rect.bottom < 0 || rect.top > height) return
          next.push({left: rect.left + target.inset, right: rect.right - target.inset, top: rect.top + target.dy})
        })
      }
      surfaces = next
    }
    const spawn = (anywhere: boolean): Flake => {
      const layer = Math.random()
      const r = layer < 0.55 ? 0.9 + Math.random() * 0.7 : layer < 0.88 ? 1.5 + Math.random() * 0.9 : 2.4 + Math.random() * 1.1
      return {x: Math.random() * (width + 40) - 20, y: anywhere ? Math.random() * height : -10 - Math.random() * 40, vy: 16 + r * 11 + Math.random() * 10, r, a: 0.45 + Math.random() * 0.45, phase: Math.random() * Math.PI * 2, sway: 6 + Math.random() * 14}
    }
    for (let i = 0; i < MAX_FLAKES; i += 1) flakes.push(spawn(true))
    measure()
    const measureTimer = window.setInterval(measure, 600)
    window.addEventListener('resize', resize)
    window.addEventListener('resize', measure)
    let last = performance.now(), frame = 0
    const draw = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05); last = now
      ctx.clearRect(0, 0, width, height)
      const wind = Math.sin(now / 5200) * 9
      for (const flake of flakes) {
        const prevY = flake.y
        flake.y += flake.vy * dt
        flake.x += (wind + Math.sin(now / 1300 + flake.phase) * flake.sway) * dt
        if (flake.r > 1.3 && resting.length < MAX_RESTING) {
          for (const surface of surfaces) {
            if (prevY < surface.top && flake.y >= surface.top && flake.x > surface.left && flake.x < surface.right && Math.random() < LAND_CHANCE) {
              resting.push({x: flake.x, y: surface.top - flake.r * 0.35, r: flake.r * (0.85 + Math.random() * 0.3), a: Math.min(1, flake.a + 0.2), born: now, life: 1500 + Math.random() * 900})
              flake.y = height + 50
              break
            }
          }
        }
        if (flake.y > height + 12 || flake.x < -30 || flake.x > width + 30) { Object.assign(flake, spawn(false)); continue }
        const image = flake.r < 1.5 ? sprites[0] : flake.r < 2.4 ? sprites[1] : sprites[2]
        const size = flake.r * 6
        ctx.globalAlpha = flake.a
        ctx.drawImage(image, flake.x - size / 2, flake.y - size / 2, size, size)
      }
      for (let i = resting.length - 1; i >= 0; i -= 1) {
        const rest = resting[i]
        const age = now - rest.born
        if (age > rest.life + 700) { resting.splice(i, 1); continue }
        const melt = age > rest.life ? 1 - (age - rest.life) / 700 : 1
        const size = rest.r * 6 * (0.55 + 0.45 * melt)
        ctx.globalAlpha = rest.a * melt
        ctx.drawImage(sprites[1], rest.x - size / 2, rest.y - size * 0.42, size, size * 0.8)
      }
      ctx.globalAlpha = 1
      frame = requestAnimationFrame(draw)
    }
    const start = () => { last = performance.now(); frame = requestAnimationFrame(draw) }
    const onVisibility = () => { cancelAnimationFrame(frame); if (!document.hidden) start() }
    document.addEventListener('visibilitychange', onVisibility)
    start()
    return () => { cancelAnimationFrame(frame); window.clearInterval(measureTimer); window.removeEventListener('resize', resize); window.removeEventListener('resize', measure); document.removeEventListener('visibilitychange', onVisibility) }
  }, [])
  return <canvas ref={ref} className="ktSnow" aria-hidden="true" style={{position:'fixed',inset:0,width:'100%',height:'100%',pointerEvents:'none',zIndex:5}}/>
}
