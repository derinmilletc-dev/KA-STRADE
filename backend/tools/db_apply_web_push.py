"""Telefon bildirimi için tek tabloyu (push_subscriptions) oluşturur. Başka hiçbir şeye dokunmaz.

Kullanım (PowerShell, backend klasöründe):  python tools/db_apply_web_push.py
Adres gizli girişle istenir. Güvenle tekrar çalıştırılabilir (tablo varsa değiştirmez).
"""
import asyncio
import getpass

import asyncpg

NAME = "20261017_002_web_push.sql"
SQL = "-- PREPARE ONLY. Manual application requires separate approval.\n-- Phone/browser push subscriptions for LIVE trade notifications. Rollback: DROP TABLE push_subscriptions;\nBEGIN;\nSELECT pg_advisory_xact_lock(hashtextextended('web-push-schema-v1',0));\n\nCREATE TABLE IF NOT EXISTS push_subscriptions (\n    endpoint      TEXT PRIMARY KEY CHECK (length(endpoint) BETWEEN 20 AND 2048),\n    user_id       TEXT NOT NULL CHECK (length(user_id) BETWEEN 1 AND 128),\n    p256dh        TEXT NOT NULL CHECK (length(p256dh) BETWEEN 20 AND 256),\n    auth          TEXT NOT NULL CHECK (length(auth) BETWEEN 8 AND 128),\n    kinds         TEXT[] NOT NULL DEFAULT '{}',\n    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),\n    last_sent_at  TIMESTAMPTZ,\n    failures      SMALLINT NOT NULL DEFAULT 0\n);\nCREATE INDEX IF NOT EXISTS push_subscriptions_user_idx ON push_subscriptions (user_id);\n\nCOMMIT;\n"


async def main() -> None:
    print("Uygulanacak güncelleme:", NAME)
    print("Yaptığı tek şey: push_subscriptions adında yeni, boş bir tablo eklemek.")
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
        exists = await conn.fetchval("SELECT to_regclass('public.push_subscriptions') IS NOT NULL")
        print("TAMAM: tablo hazır." if exists else "UYARI: tablo görünmüyor, bana haber ver.")
    finally:
        await conn.close()


try:
    asyncio.run(main())
except Exception as error:
    print(f"Hata ({type(error).__name__}).")
