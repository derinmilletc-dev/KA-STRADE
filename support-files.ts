export const MAX_FILES = 3
export const MAX_BYTES = 2 * 1024 * 1024
export const ACCEPT = 'image/png,image/jpeg,image/webp,application/pdf'
const ALLOWED = new Set(ACCEPT.split(','))

export type Picked = {key: string; file: File; preview: string | null}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => { URL.revokeObjectURL(url); resolve(img) }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Görsel okunamadı.')) }
    img.src = url
  })
}

// Büyük ekran görüntülerini göndermeden önce küçültür (en fazla 1600 px, JPEG).
async function shrink(file: File): Promise<File> {
  if (file.size <= 400 * 1024) return file
  const img = await loadImage(file)
  const scale = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
  canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
  const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.84))
  if (!blob || blob.size >= file.size) return file
  return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', {type: 'image/jpeg'})
}

export async function prepare(file: File): Promise<Picked> {
  if (!ALLOWED.has(file.type)) throw new Error('Yalnızca PNG, JPG, WEBP ve PDF eklenebilir.')
  const ready = file.type === 'application/pdf' ? file : await shrink(file)
  if (ready.size > MAX_BYTES) throw new Error(`“${file.name}” çok büyük. En fazla 2 MB olabilir.`)
  return {key: `${Date.now()}-${Math.random().toString(36).slice(2)}`, file: ready, preview: ready.type.startsWith('image/') ? URL.createObjectURL(ready) : null}
}

export function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
    reader.onerror = () => reject(new Error('Dosya okunamadı.'))
    reader.readAsDataURL(file)
  })
}
