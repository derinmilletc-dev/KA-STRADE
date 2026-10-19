-- Moderators can send a pending Binance payment to the OWNER for approval. This records the suggestion only:
-- it never creates a membership period or grants access (OWNER approval stays the only grant path).
-- The audit_log action check is EXTENDED in place (never replaced by a fixed list), so actions added to the live
-- database by earlier updates are preserved. Safe to run again.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('binance-payment-recommendations-migration'));

DO $$
DECLARE action_def TEXT;
BEGIN
    SELECT pg_get_expr(conbin, conrelid) INTO action_def FROM pg_constraint
        WHERE conrelid = 'audit_log'::regclass AND conname = 'audit_log_action_check';
    IF action_def IS NULL THEN
        RAISE EXCEPTION 'audit_log action check not found' USING ERRCODE = '55000';
    END IF;
    IF strpos(action_def, quote_literal('payment.binance.recommended')) = 0 THEN
        ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;
        EXECUTE format('ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK ((%s) OR action = ''payment.binance.recommended'')', action_def);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS binance_manual_payment_recommendations (
    payment_request_id TEXT PRIMARY KEY CHECK (payment_request_id ~ '^[a-f0-9]{32}$'),
    recommended_by TEXT NOT NULL,
    note TEXT CHECK (note IS NULL OR char_length(btrim(note)) BETWEEN 1 AND 500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

COMMIT;
