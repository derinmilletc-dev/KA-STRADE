import { useCallback, useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, ExternalLink, Images, Play } from 'lucide-react'
import { API_BASE } from './api'
import './instagram-showcase.css'

/** Giriş ekranı (yalnız masaüstü): resmi KAISTRADE Instagram hesabının gerçek gönderilerini gösteren telefon.
 * İçerik yalnız sunucudaki /public/instagram/feed uç noktasından gelir; sahte gönderi veya sayı gösterilmez. */

export const INSTAGRAM_PROFILE_URL = 'https://www.instagram.com/kaistrade_/'
const FEED_URL = `${API_BASE}/public/instagram/feed`

type MediaType = 'IMAGE' | 'VIDEO' | 'CAROUSEL_ALBUM'
type Child = {id: string; media_type: 'IMAGE' | 'VIDEO'; media_url: string | null; thumbnail_url: string | null}
export type ShowcaseMedia = {
  id: string; media_type: MediaType; media_url: string | null; thumbnail_url: string | null
  permalink: string; caption: string | null; timestamp: string | null; children?: Child[]
}
type Profile = {username: string; name: string | null; profile_picture_url: string | null; biography: string | null; media_count: number | null; profile_url: string}
export type ShowcaseFeed = {
  configured: boolean; available: boolean; stale: boolean; username: string; profile_url: string
  profile: Profile | null; media: ShowcaseMedia[]; next: string | null
}

function safeProfileUrl(value: string | undefined): string {
  return value && /^https:\/\/www\.instagram\.com\/[A-Za-z0-9._]{1,30}\/$/.test(value) ? value : INSTAGRAM_PROFILE_URL
}

async function loadFeed(after: string | null, signal: AbortSignal): Promise<ShowcaseFeed> {
  const response = await fetch(after ? `${FEED_URL}?after=${encodeURIComponent(after)}` : FEED_URL, {signal, credentials: 'omit'})
  if (!response.ok) throw new Error('feed')
  const body = await response.json() as ShowcaseFeed
  if (!body || !Array.isArray(body.media)) throw new Error('feed')
  return body
}

const tileImage = (media: ShowcaseMedia) => media.media_type === 'VIDEO'
  ? media.thumbnail_url
  : media.media_url || media.thumbnail_url || media.children?.[0]?.thumbnail_url || media.children?.[0]?.media_url || null

const formatCount = (value: number) => new Intl.NumberFormat('tr-TR').format(value)
const formatDate = (value: string | null) => {
  if (!value) return ''
  const date = new Date(value.replace(/\+0000$/, 'Z'))
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('tr-TR', {day: 'numeric', month: 'long', year: 'numeric'})
}
const typeLabel: Record<MediaType, string> = {IMAGE: 'Fotoğraf', VIDEO: 'Video', CAROUSEL_ALBUM: 'Çoklu gönderi'}

function InstagramGlyph() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="0.6" fill="currentColor"/></svg>
}

function ViewOnInstagram({href, compact = false}: {href: string; compact?: boolean}) {
  return <a className={`igsAction${compact ? ' isCompact' : ''}`} href={href} target="_blank" rel="noopener noreferrer">
    Instagram'da Görüntüle<ExternalLink aria-hidden="true"/>
  </a>
}

function ProfileHeader({profile, fallbackUrl, stale}: {profile: Profile; fallbackUrl: string; stale: boolean}) {
  const [avatarFailed, setAvatarFailed] = useState(false)
  return <header className="igsProfile">
    <div className="igsProfileTop">
      <span className="igsAvatar">{profile.profile_picture_url && !avatarFailed
        ? <img src={profile.profile_picture_url} alt="" referrerPolicy="no-referrer" onError={() => setAvatarFailed(true)}/>
        : <img src="/kaistrade-logo.png" alt="" className="igsAvatarLogo"/>}</span>
      <div className="igsIdentity">
        <b>@{profile.username}</b>
        {profile.name && <span>{profile.name}</span>}
        {profile.media_count !== null && <small>{formatCount(profile.media_count)} gönderi</small>}
      </div>
    </div>
    {profile.biography && <p className="igsBio">{profile.biography}</p>}
    <ViewOnInstagram href={safeProfileUrl(profile.profile_url) || fallbackUrl}/>
    {stale && <small className="igsStale" role="status">İçerik şu an güncellenemedi; son gönderiler gösteriliyor.</small>}
  </header>
}

function Tile({media, onOpen}: {media: ShowcaseMedia; onOpen: (media: ShowcaseMedia) => void}) {
  const [failed, setFailed] = useState(false)
  const image = tileImage(media)
  return <button type="button" className="igsTile" onClick={() => onOpen(media)} aria-label={`${typeLabel[media.media_type]} gönderisini aç`}>
    {image && !failed ? <img src={image} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)}/> : <span className="igsTileEmpty"><InstagramGlyph/></span>}
    {media.media_type === 'VIDEO' && <i className="igsTileBadge" aria-hidden="true"><Play/></i>}
    {media.media_type === 'CAROUSEL_ALBUM' && <i className="igsTileBadge" aria-hidden="true"><Images/></i>}
  </button>
}

function MediaView({item}: {item: {media_type: 'IMAGE' | 'VIDEO'; media_url: string | null; thumbnail_url: string | null}}) {
  const [failed, setFailed] = useState(false)
  if (item.media_type === 'VIDEO' && item.media_url && !failed) {
    return <video className="igsMedia" src={item.media_url} poster={item.thumbnail_url || undefined} controls playsInline preload="metadata" onError={() => setFailed(true)}/>
  }
  const image = item.media_type === 'VIDEO' ? item.thumbnail_url : item.media_url || item.thumbnail_url
  if (image && !(failed && item.media_type === 'IMAGE')) {
    return <div className="igsMediaWrap">
      <img className="igsMedia" src={image} alt="" referrerPolicy="no-referrer" onError={() => setFailed(true)}/>
      {item.media_type === 'VIDEO' && <span className="igsMediaNote">Video burada oynatılamıyor; Instagram'da izleyebilirsin.</span>}
    </div>
  }
  return <div className="igsMediaMissing"><InstagramGlyph/><span>Medya şu an yüklenemedi.</span></div>
}

function MediaDetail({media, onClose}: {media: ShowcaseMedia; onClose: () => void}) {
  const items = media.media_type === 'CAROUSEL_ALBUM' && media.children?.length ? media.children
    : [{id: media.id, media_type: media.media_type === 'VIDEO' ? 'VIDEO' as const : 'IMAGE' as const, media_url: media.media_url, thumbnail_url: media.thumbnail_url}]
  const [index, setIndex] = useState(0)
  const close = useRef<HTMLButtonElement>(null)
  useEffect(() => { close.current?.focus() }, [])
  const current = items[Math.min(index, items.length - 1)]
  return <section className="igsDetail" role="dialog" aria-modal="false" aria-label="Gönderi ayrıntısı" onKeyDown={event => {
    if (event.key === 'Escape') onClose()
    if (event.key === 'ArrowRight') setIndex(value => Math.min(items.length - 1, value + 1))
    if (event.key === 'ArrowLeft') setIndex(value => Math.max(0, value - 1))
  }}>
    <header className="igsDetailHead">
      <button ref={close} type="button" className="igsBack" onClick={onClose} aria-label="Gönderilere dön"><ChevronLeft aria-hidden="true"/></button>
      <span>{typeLabel[media.media_type]}</span>
    </header>
    <div className="igsDetailScroll">
      <div className="igsStage">
        <MediaView key={current.id} item={current}/>
        {items.length > 1 && <>
          <button type="button" className="igsNav isPrev" onClick={() => setIndex(value => Math.max(0, value - 1))} disabled={index === 0} aria-label="Önceki"><ChevronLeft aria-hidden="true"/></button>
          <button type="button" className="igsNav isNext" onClick={() => setIndex(value => Math.min(items.length - 1, value + 1))} disabled={index === items.length - 1} aria-label="Sonraki"><ChevronRight aria-hidden="true"/></button>
          <div className="igsDots" aria-label={`${index + 1} / ${items.length}`}>{items.map((child, position) => <i key={child.id} className={position === index ? 'isActive' : ''}/>)}</div>
        </>}
      </div>
      <div className="igsDetailBody">
        {media.timestamp && <time dateTime={media.timestamp}>{formatDate(media.timestamp)}</time>}
        {media.caption && <p className="igsCaption">{media.caption}</p>}
        <a className="igsAction isCompact" href={media.permalink} target="_blank" rel="noopener noreferrer">Gönderiyi Instagram'da aç<ExternalLink aria-hidden="true"/></a>
      </div>
    </div>
  </section>
}

function LoadingState() {
  return <div className="igsLoading" aria-busy="true" aria-label="Instagram gönderileri yükleniyor">
    <div className="igsSkelProfile"><i className="igsSkelAvatar"/><span><i/><i/></span></div>
    <i className="igsSkelLine"/>
    <div className="igsGrid">{Array.from({length: 9}, (_, index) => <i key={index} className="igsSkelTile"/>)}</div>
  </div>
}

function FallbackState({href}: {href: string}) {
  return <div className="igsFallback" role="status">
    <span className="igsFallbackIcon"><InstagramGlyph/></span>
    <b>KAISTRADE Instagram'da</b>
    <p>Duyurular, yenilikler ve platform güncellemeleri için resmi hesabımızı ziyaret et.</p>
    <ViewOnInstagram href={href}/>
  </div>
}

export function InstagramShowcaseScreen() {
  const [feed, setFeed] = useState<ShowcaseFeed | null>(null)
  const [media, setMedia] = useState<ShowcaseMedia[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'fallback'>('loading')
  const [loadingMore, setLoadingMore] = useState(false)
  const [open, setOpen] = useState<ShowcaseMedia | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const controller = useRef<AbortController | null>(null)
  const scrollTop = useRef(0)

  useEffect(() => {
    const abort = new AbortController()
    controller.current = abort
    loadFeed(null, abort.signal).then(body => {
      setFeed(body)
      if (body.available && body.profile) {
        setMedia(body.media)
        setNext(body.next)
        setState('ready')
      } else setState('fallback')
    }).catch(() => { if (!abort.signal.aborted) setState('fallback') })
    return () => abort.abort()
  }, [])

  const loadMore = useCallback(() => {
    if (!next || loadingMore || !controller.current) return
    setLoadingMore(true)
    loadFeed(next, controller.current.signal).then(body => {
      setMedia(current => {
        const seen = new Set(current.map(item => item.id))
        return [...current, ...body.media.filter(item => !seen.has(item.id))]
      })
      setNext(body.next)
    }).catch(() => setNext(null)).finally(() => setLoadingMore(false))
  }, [next, loadingMore])

  useEffect(() => {
    if (state !== 'ready' || !next || open || !sentinel.current || !scroller.current || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) loadMore() },
      {root: scroller.current, rootMargin: '0px 0px 160px 0px'})
    observer.observe(sentinel.current)
    return () => observer.disconnect()
  }, [state, next, open, loadMore])

  const profileHref = safeProfileUrl(feed?.profile_url)
  const openMedia = (item: ShowcaseMedia) => { scrollTop.current = scroller.current?.scrollTop || 0; setOpen(item) }
  const closeMedia = () => { setOpen(null); requestAnimationFrame(() => { if (scroller.current) scroller.current.scrollTop = scrollTop.current }) }

  return <div className="igsScreen">
    <div className="igsStatusBar" aria-hidden="true"><i className="igsNotch"/></div>
    {state === 'loading' && <LoadingState/>}
    {state === 'fallback' && <FallbackState href={profileHref}/>}
    {state === 'ready' && feed?.profile && <div ref={scroller} className="igsScroll" hidden={Boolean(open)} tabIndex={0} aria-label="KAISTRADE Instagram gönderileri">
      <ProfileHeader profile={feed.profile} fallbackUrl={profileHref} stale={feed.stale}/>
      {media.length ? <div className="igsGrid">{media.map(item => <Tile key={item.id} media={item} onOpen={openMedia}/>)}</div>
        : <p className="igsEmpty">Henüz gönderi yok.</p>}
      <div ref={sentinel} className="igsSentinel" aria-hidden="true"/>
      {loadingMore && <div className="igsMore" role="status">Yükleniyor…</div>}
      {!next && media.length > 0 && <div className="igsEnd"><ViewOnInstagram href={profileHref} compact/></div>}
    </div>}
    {open && <MediaDetail media={open} onClose={closeMedia}/>}
  </div>
}

export default function InstagramShowcasePhone() {
  return <aside className="igsShowcase" aria-label="KAISTRADE Instagram önizlemesi">
    <div className="igsGlow" aria-hidden="true"/>
    <div className="igsPhone">
      <span className="igsSideButton isPower" aria-hidden="true"/>
      <span className="igsSideButton isVolume" aria-hidden="true"/>
      <div className="igsBezel"><InstagramShowcaseScreen/></div>
    </div>
  </aside>
}
