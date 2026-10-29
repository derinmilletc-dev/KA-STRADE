-- Smart Trade (40 USDT, yalnız elle işlem) planı: ödeme talebi tutarı 119.90 (Master Mode) YA DA 40.00 (Smart Trade) olabilir.
-- Plan ayrı bir sütunda tutulmaz; ödenen tutardan çıkar (uygulama: binance_manual_payments.plan_for_amount).
-- Tek işlem: herhangi bir adım hata verirse hiçbir şey değişmez. Mevcut satırlar (hepsi 119.90) etkilenmez.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('smart-trade-amount-migration'));
DO $$
DECLARE old_name TEXT;
BEGIN
    IF to_regclass('public.binance_manual_payment_requests') IS NULL THEN
        RAISE EXCEPTION 'binance_manual_payment_requests tablosu yok; önce Binance manuel ödeme migration''ı uygulanmalı' USING ERRCODE='55000';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='binance_manual_payment_requests'::regclass AND conname='binance_manual_amount_plans') THEN
        RAISE EXCEPTION 'Smart Trade tutar kuralı zaten uygulanmış' USING ERRCODE='55000';
    END IF;
    -- Eski tek-tutar kuralı: adı otomatik verilmiş olabilir; tanımına bakarak bul.
    FOR old_name IN
        SELECT conname FROM pg_constraint
        WHERE conrelid='binance_manual_payment_requests'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) ~ '^CHECK \(\(?amount = 119\.90\)?\)$'
    LOOP
        EXECUTE format('ALTER TABLE binance_manual_payment_requests DROP CONSTRAINT %I', old_name);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='binance_manual_payment_requests'::regclass AND contype='c'
               AND pg_get_constraintdef(oid) ~ 'amount = 119\.90') THEN
        RAISE EXCEPTION 'Eski tutar kuralı bulunamadı veya kaldırılamadı' USING ERRCODE='55000';
    END IF;
    ALTER TABLE binance_manual_payment_requests
        ADD CONSTRAINT binance_manual_amount_plans CHECK (amount IN (119.90, 40.00));
END $$;
COMMIT;
