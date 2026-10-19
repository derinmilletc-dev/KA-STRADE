-- Rollback for 20261019_001_payment_recommendations.sql: removes only the suggestion table.
-- The extra allowed audit action is harmless and is left in place so existing audit rows stay valid.
BEGIN;
DROP TABLE IF EXISTS binance_manual_payment_recommendations;
COMMIT;
