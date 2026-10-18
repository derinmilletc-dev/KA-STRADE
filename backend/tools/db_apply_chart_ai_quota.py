"""Kais AI kota kurallarını günceller (ücretsiz üye 3 hak, deneme üyesi günde 7, premium haftada 35). Tablo/veri silmez.

Kullanım (PowerShell, backend klasöründe):  python tools/db_apply_chart_ai_quota.py
Adres gizli girişle istenir. Güvenle tekrar çalıştırılabilir.
"""
import asyncio
import getpass
from pathlib import Path

import asyncpg

NAME = "20261018_001_chart_ai_quota_tiers.sql"
SQL = (Path(__file__).resolve().parents[1] / "migrations" / NAME).read_text(encoding="utf-8")


async def main() -> None:
    print("Uygulanacak güncelleme:", NAME)
    print("Yaptığı tek şey: Kais AI kota tablosundaki kuralları genişletmek (ücretsiz üye 3, deneme günde 7, premium haftada 35).")
    print("Hiçbir kayıt silinmez; bu haftaki premium haklar 20'den 35'e çıkar.")
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
            "SELECT count(*) = 2 FROM pg_constraint WHERE conrelid = 'chart_ai_quota_periods'::regclass "
            "AND conname IN ('chart_ai_quota_access_kinds', 'chart_ai_quota_tiers')"
        )
        print("TAMAM: kota kuralları güncellendi." if ready else "UYARI: kurallar görünmüyor, bana haber ver.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
