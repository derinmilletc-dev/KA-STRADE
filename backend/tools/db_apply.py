"""Eksik 3 veritabanı güncellemesini uygular. Binance ödeme / ücretsiz deneme güncellemeleri UYGULANMAZ.

Kullanım (PowerShell, backend klasöründe):  python tools/db_apply.py
Adres gizli girişle istenir. Her güncelleme kendi işleminde (BEGIN/COMMIT) çalışır; hata olursa durur.
"""
import asyncio
import getpass

import asyncpg

MIGRATIONS = [
    ('20261009_001_email_verification_v2.sql', "BEGIN;\nSELECT pg_advisory_xact_lock(hashtext('email-verification-v2-migration'));\nLOCK TABLE commercial_auth_users IN SHARE ROW EXCLUSIVE MODE;\nCREATE TABLE IF NOT EXISTS commercial_feature_migrations (\n    name TEXT PRIMARY KEY,\n    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()\n);\nWITH first_run AS (\n    INSERT INTO commercial_feature_migrations (name)\n    VALUES ('email-verification-v2')\n    ON CONFLICT (name) DO NOTHING\n    RETURNING applied_at\n)\nUPDATE commercial_auth_users AS users\nSET security = users.security || jsonb_build_object(\n    'email_verified', TRUE,\n    'email_verified_at', COALESCE(users.security->>'email_verified_at', first_run.applied_at::text)\n)\nFROM first_run\nWHERE NULLIF(users.security->>'email', '') IS NOT NULL\n  AND NULLIF(users.security->>'closed_at', '') IS NULL;\nCOMMIT;\n"),
    ('20261014_001_original_live_risk_audit.sql', "BEGIN;\n\nALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;\nALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK (action IN (\n 'ROLE_CHANGED','PERMISSION_GRANTED','PERMISSION_REVOKED','customer.viewed','customer.subscription.viewed','customer.payments.viewed',\n 'support.case.viewed','support.case.taken','support.case.released','support.case.status_changed','support.note.added',\n 'approval.requested','approval.approved','approval.rejected','approval.cancelled','approval.expired','approval.stale','approval.executed','approval.failed',\n 'customer.events.viewed','campaign.created','campaign.updated','campaign.test_sent','campaign.send_requested',\n 'campaign.send_started','campaign.cancelled','campaign.completed','payment.binance.approved','payment.binance.rejected',\n 'original_live.risk_changed'));\n\nCOMMIT;\n"),
    ('20261015_002_chart_ai_retry.sql', "-- PREPARE ONLY. Manual application requires separate approval.\n-- Apply after 20261015_001_chart_ai.sql; no runtime schema installation.\nBEGIN;\nSELECT pg_advisory_xact_lock(hashtextextended('chart-ai-schema-v1',0));\n\nALTER TABLE chart_ai_requests\n    ADD COLUMN IF NOT EXISTS generation_attempts SMALLINT NOT NULL DEFAULT 0,\n    ADD COLUMN IF NOT EXISTS accounted_microusd BIGINT NOT NULL DEFAULT 0;\n\nUPDATE chart_ai_requests SET generation_attempts=1\n    WHERE generation_attempts=0 AND provider_started_at IS NOT NULL;\nUPDATE chart_ai_requests SET accounted_microusd=cost_microusd\n    WHERE accounted_microusd=0 AND cost_microusd IS NOT NULL AND cost_microusd>0;\n\nDO $$\nBEGIN\n    IF NOT EXISTS (SELECT 1 FROM pg_constraint\n                   WHERE conrelid='chart_ai_requests'::regclass AND conname='chart_ai_retry_accounting') THEN\n        ALTER TABLE chart_ai_requests ADD CONSTRAINT chart_ai_retry_accounting CHECK (\n            generation_attempts BETWEEN 0 AND 2\n            AND accounted_microusd BETWEEN 0 AND 20000\n            AND ((provider_started_at IS NULL AND generation_attempts=0 AND accounted_microusd=0)\n                 OR (provider_started_at IS NOT NULL AND generation_attempts BETWEEN 1 AND 2))\n            AND (cost_microusd IS NULL OR cost_microusd=accounted_microusd)\n        );\n    END IF;\nEND $$;\nCOMMIT;\n"),
]


async def main() -> None:
    print("Uygulanacak güncellemeler:")
    for name, _ in MIGRATIONS:
        print("  -", name)
    print("Not: e-posta doğrulama güncellemesi mevcut tüm hesapları 'doğrulanmış' işaretler (tek seferlik, güvenli).")
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
        for name, sql in MIGRATIONS:
            try:
                await conn.execute(sql)
            except Exception as error:
                print(f"DURDU: {name} uygulanamadı ({type(error).__name__}). Sonrakiler çalıştırılmadı.")
                return
            print(f"TAMAM: {name}")
        print("Bitti. Şimdi python tools/db_check.py ile tekrar kontrol edebilirsin.")
    finally:
        await conn.close()


try:
    asyncio.run(main())
except Exception as error:
    print(f"Hata ({type(error).__name__}).")
