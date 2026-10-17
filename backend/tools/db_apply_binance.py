"""Binance manuel ödeme tablolarını canlı veritabanına TEK SEFERDE kurar.

Kullanım (PowerShell, backend klasöründe):  python tools/db_apply_binance.py
Veritabanı adresi komut satırına yazılmaz; çalışınca gizli girişle istenir.

- migrations/20261017_001_binance_manual_install.sql dosyasını uygular.
- Mevcut audit/bildirim kurallarını (Original risk denetimi, Chart AI bütçe uyarısı) korur.
- Tek transaction: bir adım bile hata verirse hiçbir şey değişmez.
- Tablolar zaten varsa hiçbir şey yapmadan durur.
"""
import asyncio
import getpass
from pathlib import Path

import asyncpg

SQL_FILE = Path(__file__).parents[1] / "migrations" / "20261017_001_binance_manual_install.sql"
TABLES = ["binance_manual_payment_requests", "binance_manual_periods", "binance_notification_reads"]
REQUIRED = ["commercial_auth_users", "commercial_erased_users", "subscriptions", "audit_log", "notification_outbox"]


async def table_state(conn) -> dict[str, bool]:
    return {name: await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{name}") for name in TABLES + REQUIRED}


async def main() -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
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
        state = await table_state(conn)
        missing = [name for name in REQUIRED if not state[name]]
        if missing:
            print("Gerekli tablolar eksik, kurulum yapılmadı:", ", ".join(missing))
            return
        present = [name for name in TABLES if state[name]]
        if len(present) == len(TABLES):
            print("Binance ödeme tabloları ZATEN KURULU. Hiçbir şey değişmedi.")
            return
        if present:
            print("Binance tabloları yarım kurulmuş görünüyor:", ", ".join(present))
            print("Güvenlik için durduruldu, hiçbir şey değişmedi. Bu durumu bildir.")
            return
        print("Kurulacak: Binance ödeme talepleri, üyelik dönemleri, bildirim okundu kayıtları.")
        print("Mevcut kayıtlar silinmez, mevcut kurallar korunur.")
        if input("Devam için tam olarak 'evet' yaz: ").strip().lower() != "evet":
            print("İptal edildi, hiçbir şey değişmedi.")
            return
        try:
            await conn.execute(sql)
        except Exception as error:
            print(f"DURDU: kurulum uygulanamadı ({type(error).__name__}). Hiçbir şey değişmedi.")
            return
        state = await table_state(conn)
        if all(state[name] for name in TABLES):
            print("TAMAM: Binance ödeme tabloları kuruldu.")
        else:
            print("Uyarı: kurulum bitti ama tablolar doğrulanamadı. Bu durumu bildir.")
    finally:
        await conn.close()


try:
    asyncio.run(main())
except Exception as error:
    print(f"Hata ({type(error).__name__}).")
