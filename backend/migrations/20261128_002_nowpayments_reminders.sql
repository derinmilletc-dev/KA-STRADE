-- Kripto üyelik bitiş hatırlatması: her ödeme dönemi için en fazla bir e-posta (tekrar koruması).
-- Elle uygulanır: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/20261128_002_nowpayments_reminders.sql
-- Tek işlem ve birden fazla kez çalıştırmak güvenlidir. Tablo yoksa hatırlatma gönderilmez, ödeme/üyelik etkilenmez.
BEGIN;
CREATE TABLE IF NOT EXISTS nowpayments_reminders (
    payment_id TEXT PRIMARY KEY REFERENCES nowpayments_payments(id),
    sent_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
COMMIT;
