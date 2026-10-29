-- Smart Trade tutar kuralını geri alır. 39.90 tutarlı bir ödeme talebi varsa BAŞARISIZ olur (veri silinmez).
BEGIN;
ALTER TABLE binance_manual_payment_requests DROP CONSTRAINT IF EXISTS binance_manual_amount_plans;
ALTER TABLE binance_manual_payment_requests ADD CONSTRAINT binance_manual_payment_requests_amount_check CHECK (amount = 119.90);
COMMIT;
