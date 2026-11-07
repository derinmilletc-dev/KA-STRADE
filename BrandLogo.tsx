// Sitenin tek logosu: ana sayfadaki KaisTrade logosu (yeşil halka içinde nabız + "kaistrade"). Her yerde bu kullanılır;
// yazıyla ya da ikonla taklit edilmiş logo kullanılmaz. Yükseklik verilir, genişlik orana göre ayarlanır.
export default function BrandLogo({height = 28, className = ''}: {height?: number; className?: string}) {
  return <img className={`ktBrandLogo ${className}`.trim()} src="/kaistrade-logo-trim.png" alt="KaisTrade" width={Math.round(height * 896 / 216)} height={height}
    style={{height, width: 'auto', display: 'block', maxWidth: '100%'}} draggable={false}/>
}
