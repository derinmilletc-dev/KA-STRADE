-- Kais AI kotaları: ücretsiz üye (ömür boyu 3), deneme üyesi (günde 15). Mevcut satırlar geçerli kalır.
-- Geri alma gerekmez: yeni kural eskisinin üst kümesidir.
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('chart-ai-schema-v1',0));

DO $$
DECLARE item RECORD;
BEGIN
    IF to_regclass('public.chart_ai_quota_periods') IS NULL THEN
        RAISE EXCEPTION 'chart_ai_quota_periods tablosu bulunamadi' USING ERRCODE='55000';
    END IF;
    FOR item IN
        SELECT conname FROM pg_constraint
        WHERE conrelid='chart_ai_quota_periods'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%access_type%'
    LOOP
        EXECUTE format('ALTER TABLE chart_ai_quota_periods DROP CONSTRAINT %I', item.conname);
    END LOOP;
END $$;

ALTER TABLE chart_ai_quota_periods ADD CONSTRAINT chart_ai_quota_access_kinds
    CHECK (access_type IN ('trial','subscriber','owner','free'));

ALTER TABLE chart_ai_quota_periods ADD CONSTRAINT chart_ai_quota_tiers CHECK (
    -- eski deneme satırları (toplam 3 hak, 7 gün)
    (access_type='trial' AND period_key='trial' AND quota_limit=3
        AND period_start IS NOT NULL AND period_end IS NOT NULL
        AND period_end=period_start + INTERVAL '168 hours')
    -- yeni deneme: günde 15 hak, gün Türkiye saatiyle yenilenir
    OR (access_type='trial' AND period_key ~ '^trial-day:[0-9]{4}-[0-9]{2}-[0-9]{2}$' AND quota_limit=15
        AND period_start IS NOT NULL AND period_end IS NOT NULL
        AND period_end=period_start + INTERVAL '24 hours')
    OR (access_type='subscriber' AND quota_limit IS NOT NULL AND quota_limit=20
        AND period_start IS NOT NULL AND period_end IS NOT NULL
        AND period_end=period_start + INTERVAL '168 hours'
        AND extract(isodow FROM period_start AT TIME ZONE 'UTC')=1
        AND period_start AT TIME ZONE 'UTC'=date_trunc('day',period_start AT TIME ZONE 'UTC')
        AND period_key='week:' || to_char(period_start AT TIME ZONE 'UTC','YYYY-MM-DD'))
    OR (access_type='owner' AND period_key='owner' AND quota_limit IS NULL
        AND period_start IS NULL AND period_end IS NULL)
    -- ücretsiz üye: ömür boyu toplam 3 hak
    OR (access_type='free' AND period_key='free' AND quota_limit=3
        AND period_start IS NULL AND period_end IS NULL)
);
COMMIT;
