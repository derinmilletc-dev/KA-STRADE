import {KeyRound, LifeBuoy, LockKeyhole, ShieldCheck, Smartphone} from 'lucide-react'
import './two-factor-status.css'

/** 2FA etkin hesabın durum ekranı. Kapatma seçeneği yoktur: 2FA her hesap için zorunludur. */
export function TwoFactorStatusHeader() {
  return <div className="tfaHead">
    <span className="tfaHeadIcon" aria-hidden="true"><ShieldCheck/></span>
    <div><h2 id="account-dialog-title">İki aşamalı doğrulama</h2><p>Hesap güvenliği</p></div>
  </div>
}

export default function TwoFactorStatus({onDone}: {onDone: () => void}) {
  return <div className="tfaStatus" data-testid="two-factor-status">
    <section className="tfaHero" aria-label="2FA durumu">
      <span className="tfaShield" aria-hidden="true"><ShieldCheck/></span>
      <span className="tfaPill"><i aria-hidden="true"/>Etkin</span>
      <h3>Hesabın koruma altında</h3>
      <p>Her girişte parolanın yanında doğrulama uygulamandaki 6 haneli kod istenir.</p>
    </section>

    <ul className="tfaList">
      <li><span aria-hidden="true"><Smartphone/></span><div><b>Doğrulama uygulaması</b><small>Kodlar 30 saniyede bir yenilenir</small></div><em data-tone="ok">Bağlı</em></li>
      <li><span aria-hidden="true"><KeyRound/></span><div><b>Yedek kodlar</b><small>Kurulumda verilen 10 kod; her biri tek kullanımlık. Güvenli bir yerde sakla.</small></div></li>
      <li><span aria-hidden="true"><LockKeyhole/></span><div><b>Zorunlu koruma</b><small>2FA her KaisTrade hesabında zorunludur ve kapatılamaz.</small></div><em>Zorunlu</em></li>
    </ul>

    <p className="tfaHelp"><LifeBuoy aria-hidden="true"/><span>Telefonunu kaybedersen yedek kodlarından biriyle giriş yap. Yedek kodların da yoksa <a href="/support">Destek</a> ekibinden yardım iste.</span></p>

    <button type="button" className="tfaDone" onClick={onDone}>Tamam</button>
  </div>
}
