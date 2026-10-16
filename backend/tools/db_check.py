"""SALT OKUNUR veritabanı kontrolü: hangi güncellemeler uygulanmış? Hiçbir şeyi değiştirmez.

Kullanım (PowerShell, backend klasöründe):  python tools/db_check.py
Veritabanı adresi komut satırına yazılmaz; çalışınca gizli girişle istenir.
"""
import asyncio
import getpass
import re

import asyncpg

TABLES = [
    "commercial_feature_migrations", "moderator_permissions", "audit_log", "support_cases", "support_notes",
    "approval_requests", "notification_outbox", "customer_events", "campaigns", "campaign_recipients",
    "email_preferences", "campaign_delivery_budget", "campaign_test_limits", "binance_manual_payment_requests",
    "binance_manual_periods", "binance_notification_reads", "member_free_trials", "chart_ai_requests",
    "chart_ai_quota_periods", "chart_ai_acknowledgements", "chart_ai_monthly_budget",
]


async def main() -> None:
    dsn = getpass.getpass("Veritabanı adresi (yazarken görünmez): ").strip()
    conn = await asyncpg.connect(dsn, timeout=20)
    try:
        await conn.execute("SET default_transaction_read_only = on")
        print("\n== TABLOLAR ==")
        for name in TABLES:
            exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{name}")
            print(f"{'VAR ' if exists else 'YOK '} {name}")
        print("\n== KISITLAR (sadece ad ve içerikteki işlem adları) ==")
        rows = await conn.fetch(
            """SELECT c.conrelid::regclass::text AS tablo, c.conname, pg_get_constraintdef(c.oid) AS tanim
               FROM pg_constraint c
               WHERE c.contype='c' AND c.conrelid::regclass::text IN
                 ('audit_log','moderator_permissions','notification_outbox','approval_requests')
               ORDER BY 1,2""")
        for row in rows:
            words = sorted(set(re.findall(r"'([A-Z_\.a-z0-9]+)'", row["tanim"])))
            print(f"{row['tablo']}.{row['conname']}: {len(words)} değer")
            if row["tablo"] in ("audit_log", "moderator_permissions"):
                print("   " + ", ".join(words))
    finally:
        await conn.close()


asyncio.run(main())
