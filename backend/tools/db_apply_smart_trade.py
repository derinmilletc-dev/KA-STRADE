"""Smart Trade (39.90 USDT) için ödeme tutarı kuralını günceller (tek seferlik).

Kullanım (PowerShell, backend klasöründe):  python tools/db_apply_smart_trade.py
- Veritabanı adresi gizli girişle istenir, ekrana/komut satırına yazılmaz.
- Önce salt okunur kontrol yapar, sonra 'evet' onayı ister.
- TEK işlemde çalışır: hata olursa hiçbir şey değişmez. Mevcut ödeme kayıtlarına dokunulmaz.
- İçerik: migrations/20261029_001_smart_trade_amount.sql
"""
import asyncio
import getpass
from pathlib import Path

import asyncpg

SQL = (Path(__file__).resolve().parents[1] / "migrations" / "20261029_001_smart_trade_amount.sql").read_text(encoding="utf-8")


async def amount_rules(conn) -> list[str]:
    rows = await conn.fetch(
        """SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint
           WHERE conrelid = 'public.binance_manual_payment_requests'::regclass AND contype = 'c'
             AND pg_get_constraintdef(oid) ~ 'amount'""",
    )
    return [f"{row['conname']}: {row['definition']}" for row in rows]


async def main() -> None:
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
        print("\n== KONTROL (salt okunur) ==")
        if not await conn.fetchval("SELECT to_regclass('public.binance_manual_payment_requests') IS NOT NULL"):
            print("DURDU: Binance ödeme tablosu yok. Önce tools/db_apply_binance.py çalışmalı. Hiçbir şey değişmedi.")
            return
        rules = await amount_rules(conn)
        for rule in rules:
            print("Mevcut tutar kuralı ->", rule)
        if any("binance_manual_amount_plans" in rule for rule in rules):
            print("\nSmart Trade tutar kuralı zaten kurulu, yapılacak bir şey yok. Hiçbir şey değişmedi.")
            return
        if input("\nTutar kuralı 119.90 YA DA 39.90 olacak şekilde güncellenecek. Devam için tam olarak 'evet' yaz: ").strip().lower() != "evet":
            print("İptal edildi, hiçbir şey değişmedi.")
            return
        try:
            await conn.execute(SQL)
        except Exception as error:
            try:
                await conn.execute("ROLLBACK")
            except Exception:
                pass
            print(f"DURDU: güncelleme uygulanamadı ({type(error).__name__}: {str(error)[:160]}). Hiçbir şey değişmedi.")
            return
        print("\nYeni tutar kuralı ->")
        for rule in await amount_rules(conn):
            print("  ", rule)
        print("\nTAMAM. Şimdi sunucuyu güncelle (update.sh) ve siteyi yenile.")
    finally:
        await conn.close()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nİptal edildi.")
except Exception as error:
    print(f"Hata ({type(error).__name__}).")
