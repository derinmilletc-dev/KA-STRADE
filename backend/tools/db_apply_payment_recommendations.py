"""Moderatör "Onaya gönder" özelliği için veritabanı güncellemesi. Hiçbir kayıt silinmez, erişim vermez.

Kullanım (PowerShell, backend klasöründe):  python tools/db_apply_payment_recommendations.py
Adres gizli girişle istenir. Güvenle tekrar çalıştırılabilir.
"""
import asyncio
import getpass
from pathlib import Path

import asyncpg

NAME = "20261019_001_payment_recommendations.sql"
SQL = (Path(__file__).resolve().parents[1] / "migrations" / NAME).read_text(encoding="utf-8")


async def main() -> None:
    print("Uygulanacak güncelleme:", NAME)
    print("Yaptığı tek şey: moderatör önerilerini tutacak küçük bir tablo eklemek ve denetim kaydına yeni bir eylem türü tanımlamak.")
    print("Hiçbir kayıt silinmez ve kimseye erişim verilmez.")
    if input("Devam için tam olarak 'evet' yaz: ").strip().lower() != "evet":
        print("İptal edildi, hiçbir şey değişmedi.")
        return
    dsn = getpass.getpass("Veritabanı adresi (yazarken görünmez): ").strip()
    if dsn.count("://") != 1 or not dsn.startswith(("postgres://", "postgresql://")):
        print("Adres hatalı görünüyor (bir kez, postgresql:// ile başlayarak yapıştır). Hiçbir şey değişmedi.")
        return
    try:
        conn = await asyncpg.connect(dsn, timeout=20)
    except Exception as error:  # hata metni adres/şifre içerebilir; yalnızca türü yazılır
        print(f"Bağlanılamadı ({type(error).__name__}). Hiçbir şey değişmedi.")
        return
    try:
        try:
            await conn.execute(SQL)
        except Exception as error:
            print(f"DURDU: güncelleme uygulanamadı ({type(error).__name__}). Hiçbir şey değişmedi.")
            return
        ready = await conn.fetchval(
            "SELECT to_regclass('public.binance_manual_payment_recommendations') IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM pg_constraint WHERE conrelid = 'audit_log'::regclass AND conname = 'audit_log_action_check' "
            "AND pg_get_expr(conbin, conrelid) LIKE '%payment.binance.recommended%')"
        )
        print("TAMAM: moderatör önerisi altyapısı hazır." if ready else "UYARI: beklenen tablo/kural görünmüyor, bana haber ver.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
