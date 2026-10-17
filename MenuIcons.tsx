import type {SVGProps} from 'react'

const base: SVGProps<SVGSVGElement> = {xmlns: 'http://www.w3.org/2000/svg', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true}

/** Analist: büyüteç içinde çizgi grafik. Sitenin diğer çizgi ikonlarıyla aynı kalınlık ve renkte. */
export function AnalystIcon(props: SVGProps<SVGSVGElement>) {
  return <svg {...base} {...props}><circle cx="10.5" cy="10.5" r="7"/><path d="m21 21-5.2-5.2"/><path d="m6.5 12.5 2.6-3 2.2 2 2.6-3.5"/><circle cx="6.5" cy="12.5" r=".6"/><circle cx="13.9" cy="8" r=".6"/></svg>
}

/** Destek: mikrofonlu kulaklık. */
export function SupportIcon(props: SVGProps<SVGSVGElement>) {
  return <svg {...base} {...props}><path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="3" y="13" width="4" height="6" rx="1.5"/><rect x="17" y="13" width="4" height="6" rx="1.5"/><path d="M19 19v.5a2.5 2.5 0 0 1-2.5 2.5H13"/><circle cx="12" cy="22" r=".6"/></svg>
}
