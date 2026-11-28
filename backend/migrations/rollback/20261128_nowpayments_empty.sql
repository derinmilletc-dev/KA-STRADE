-- NOWPayments tablolarını kaldırır. YALNIZ tablolar boşken kullanın: ödeme geçmişi ve verilen üyelik dönemleri silinir.
BEGIN;
DO $$
BEGIN
    IF to_regclass('public.nowpayments_payments') IS NOT NULL
       AND EXISTS (SELECT 1 FROM nowpayments_payments) THEN
        RAISE EXCEPTION 'nowpayments_payments boş değil; geri alma durduruldu' USING ERRCODE = '55000';
    END IF;
END $$;
DROP TRIGGER IF EXISTS nowpayments_payment_erasure ON commercial_erased_users;
DROP FUNCTION IF EXISTS nowpayments_erase();
DROP TABLE IF EXISTS nowpayments_periods;
DROP TABLE IF EXISTS nowpayments_payments;
COMMIT;
