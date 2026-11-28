-- NOWPayments (kripto) otomatik ödeme tabloları. Mevcut Binance elle ödeme tablolarına DOKUNMAZ.
-- Elle uygulanır: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/20261128_001_nowpayments.sql
-- Tek işlem: herhangi bir adım hata verirse hiçbir şey değişmez. Birden fazla kez çalıştırmak güvenlidir.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('nowpayments-migration'));

CREATE TABLE IF NOT EXISTS nowpayments_payments (
    id TEXT PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),          -- NOWPayments'a order_id olarak gider
    user_id TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('MASTER_MODE','SMART_TRADE')),
    price_amount NUMERIC(12,2) NOT NULL CHECK (price_amount IN (119.90, 39.90)),
    price_currency TEXT NOT NULL DEFAULT 'usd' CHECK (price_currency = 'usd'),
    status TEXT NOT NULL DEFAULT 'created' CHECK (status IN (
        'created','waiting','confirming','confirmed','sending','partially_paid',
        'finished','failed','refunded','expired','mismatch')),
    invoice_id TEXT,
    invoice_url TEXT,
    last_payment_id TEXT,
    pay_currency TEXT,
    note TEXT CHECK (note IS NULL OR char_length(note) <= 500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    erased_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS nowpayments_invoice_unique ON nowpayments_payments(invoice_id) WHERE invoice_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS nowpayments_user_history ON nowpayments_payments(user_id, created_at DESC, id);

CREATE TABLE IF NOT EXISTS nowpayments_periods (
    payment_id TEXT PRIMARY KEY REFERENCES nowpayments_payments(id),   -- bir ödeme yalnız bir kez dönem verir (tekrar koruması)
    user_id TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (period_end = period_start + INTERVAL '720 hours')
);
CREATE INDEX IF NOT EXISTS nowpayments_period_user ON nowpayments_periods(user_id, period_end DESC) WHERE revoked_at IS NULL;

-- Hesap silinirse ödeme kayıtları kimliksizleştirilir (Binance elle ödeme tablosundaki davranışla aynı).
CREATE OR REPLACE FUNCTION nowpayments_erase() RETURNS trigger AS $$
BEGIN
    UPDATE nowpayments_periods SET user_id = 'ERASED'
      WHERE encode(sha256(convert_to(user_id,'UTF8')),'hex') = NEW.user_hash;
    UPDATE nowpayments_payments SET user_id = 'ERASED', erased_at = clock_timestamp()
      WHERE encode(sha256(convert_to(user_id,'UTF8')),'hex') = NEW.user_hash AND erased_at IS NULL;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS nowpayments_payment_erasure ON commercial_erased_users;
CREATE TRIGGER nowpayments_payment_erasure AFTER INSERT ON commercial_erased_users
    FOR EACH ROW EXECUTE FUNCTION nowpayments_erase();
ALTER TABLE commercial_erased_users ENABLE ALWAYS TRIGGER nowpayments_payment_erasure;
COMMIT;
