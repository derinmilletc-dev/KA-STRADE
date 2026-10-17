-- KaisTrade: Binance manuel ödeme tablolarını CANLI veritabanına tek seferde kurar.
-- 20261011_001/002/003 dosyalarının içeriğini kullanır; ancak sonradan eklenen
-- audit (original_live.risk_changed) ve bildirim (chart_ai.budget_warning) kurallarını SİLMEZ:
-- mevcut kurallar korunur, yalnız Binance için gerekenler eklenir.
-- Tek transaction: herhangi bir adım hata verirse hiçbir şey değişmez.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('binance-manual-install'));
LOCK TABLE audit_log, notification_outbox IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE
    action_expr TEXT; target_expr TEXT; kind_expr TEXT; payload_expr TEXT;
BEGIN
    IF to_regclass('public.binance_manual_payment_requests') IS NOT NULL
       OR to_regclass('public.binance_manual_periods') IS NOT NULL
       OR to_regclass('public.binance_notification_reads') IS NOT NULL THEN
        RAISE EXCEPTION 'Binance tablolari zaten (kismen) kurulu; kurulum durduruldu.' USING ERRCODE='55000';
    END IF;
    IF to_regclass('public.commercial_auth_users') IS NULL OR to_regclass('public.commercial_erased_users') IS NULL
       OR to_regclass('public.subscriptions') IS NULL OR to_regclass('public.audit_log') IS NULL
       OR to_regclass('public.notification_outbox') IS NULL
       OR to_regprocedure('notification_outbox_reject_delete()') IS NULL THEN
        RAISE EXCEPTION 'On kosul tablolari eksik; kurulum durduruldu.' USING ERRCODE='55000';
    END IF;

    SELECT pg_get_expr(conbin,conrelid) INTO action_expr FROM pg_constraint
        WHERE conrelid='audit_log'::regclass AND conname='audit_log_action_check';
    SELECT pg_get_expr(conbin,conrelid) INTO target_expr FROM pg_constraint
        WHERE conrelid='audit_log'::regclass AND conname='audit_log_target_type_check';
    SELECT pg_get_expr(conbin,conrelid) INTO kind_expr FROM pg_constraint
        WHERE conrelid='notification_outbox'::regclass AND conname='outbox_kind_priority';
    SELECT pg_get_expr(conbin,conrelid) INTO payload_expr FROM pg_constraint
        WHERE conrelid='notification_outbox'::regclass AND conname='outbox_payload_contract';
    IF action_expr IS NULL OR target_expr IS NULL OR kind_expr IS NULL OR payload_expr IS NULL THEN
        RAISE EXCEPTION 'Mevcut audit/bildirim kurallari bulunamadi.' USING ERRCODE='55000';
    END IF;

    IF strpos(action_expr, quote_literal('payment.binance.approved')) = 0 THEN
        ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;
        EXECUTE format('ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK ((%s) OR action IN (''payment.binance.approved'',''payment.binance.rejected''))', action_expr);
    ELSIF strpos(action_expr, quote_literal('payment.binance.rejected')) = 0 THEN
        ALTER TABLE audit_log DROP CONSTRAINT audit_log_action_check;
        EXECUTE format('ALTER TABLE audit_log ADD CONSTRAINT audit_log_action_check CHECK ((%s) OR action = ''payment.binance.rejected'')', action_expr);
    END IF;
    IF strpos(target_expr, quote_literal('BINANCE_PAYMENT')) = 0 THEN
        ALTER TABLE audit_log DROP CONSTRAINT audit_log_target_type_check;
        EXECUTE format('ALTER TABLE audit_log ADD CONSTRAINT audit_log_target_type_check CHECK ((%s) OR target_type = ''BINANCE_PAYMENT'')', target_expr);
    END IF;
    IF strpos(kind_expr, quote_literal('payment.binance.bell')) = 0 THEN
        ALTER TABLE notification_outbox DROP CONSTRAINT outbox_kind_priority;
        EXECUTE format('ALTER TABLE notification_outbox ADD CONSTRAINT outbox_kind_priority CHECK ((%s) OR (kind IN (''payment.binance.email'',''payment.binance.bell'') AND priority=0))', kind_expr);
    END IF;
    IF strpos(payload_expr, quote_literal('payment.binance.bell')) = 0 THEN
        ALTER TABLE notification_outbox DROP CONSTRAINT outbox_payload_contract;
        EXECUTE format('ALTER TABLE notification_outbox ADD CONSTRAINT outbox_payload_contract CHECK ((%s) OR (jsonb_typeof(payload)=''object'' AND (kind IN (''payment.binance.email'',''payment.binance.bell'') AND payload ?& ARRAY[''payment_id'',''event'']
  AND payload-ARRAY[''payment_id'',''event'']=''{}''::jsonb
  AND jsonb_typeof(payload->''payment_id'')=''string'' AND payload->>''payment_id'' ~ ''^[a-f0-9]{32}$''
  AND jsonb_typeof(payload->''event'')=''string'' AND payload->>''event'' IN (''pending'',''approved'',''rejected'',''renewal''))))', payload_expr);
    END IF;
END $$;

-- ===== 20261011_001_binance_manual_payments =====
CREATE TABLE binance_manual_payment_requests (
    id TEXT PRIMARY KEY CHECK (id ~ '^[a-f0-9]{32}$'),
    user_id TEXT NOT NULL,
    reference_code TEXT NOT NULL CONSTRAINT binance_manual_reference_unique UNIQUE
        CHECK (reference_code ~ '^KT-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{6}$'),
    amount NUMERIC(12,2) NOT NULL DEFAULT 119.90 CHECK (amount = 119.90),
    currency TEXT NOT NULL DEFAULT 'USDT' CHECK (currency = 'USDT'),
    method TEXT NOT NULL DEFAULT 'binance_manual' CHECK (method = 'binance_manual'),
    status TEXT NOT NULL DEFAULT 'created' CHECK (status IN ('created','pending','approved','rejected','expired')),
    transaction_number TEXT CONSTRAINT binance_manual_transaction_unique UNIQUE
        CHECK (transaction_number IS NULL OR transaction_number ~ '^[A-Za-z0-9_-]{1,128}$'),
    transaction_fingerprint TEXT CONSTRAINT binance_manual_fingerprint_unique UNIQUE
        CHECK (transaction_fingerprint IS NULL OR transaction_fingerprint ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ,
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    decision_note TEXT CHECK (decision_note IS NULL OR char_length(btrim(decision_note)) BETWEEN 1 AND 500),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    erased_at TIMESTAMPTZ,
    CHECK (expires_at = created_at + INTERVAL '48 hours'),
    CHECK ((transaction_number IS NULL AND transaction_fingerprint IS NULL)
        OR (transaction_fingerprint IS NOT NULL AND (transaction_number IS NOT NULL OR erased_at IS NOT NULL))),
    CHECK ((status IN ('created','expired') AND transaction_fingerprint IS NULL AND submitted_at IS NULL)
        OR (status IN ('pending','approved','rejected') AND transaction_fingerprint IS NOT NULL
            AND submitted_at IS NOT NULL AND submitted_at < expires_at)),
    CHECK ((status IN ('approved','rejected') AND decided_by IS NOT NULL AND decided_at IS NOT NULL)
        OR (status NOT IN ('approved','rejected') AND decided_by IS NULL AND decided_at IS NULL AND decision_note IS NULL)),
    CHECK (status <> 'rejected' OR decision_note IS NOT NULL OR erased_at IS NOT NULL)
);
CREATE UNIQUE INDEX binance_manual_one_open_user ON binance_manual_payment_requests(user_id)
    WHERE status IN ('created','pending') AND erased_at IS NULL;
CREATE INDEX binance_manual_expiry ON binance_manual_payment_requests(expires_at,id)
    WHERE status = 'created' AND erased_at IS NULL;
CREATE INDEX binance_manual_user_history ON binance_manual_payment_requests(user_id,created_at DESC,id);

CREATE FUNCTION binance_manual_payment_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'created' OR NEW.version <> 1 OR NEW.transaction_number IS NOT NULL
           OR NEW.transaction_fingerprint IS NOT NULL OR NEW.submitted_at IS NOT NULL
           OR NEW.decided_by IS NOT NULL OR NEW.decided_at IS NOT NULL
           OR NEW.decision_note IS NOT NULL OR NEW.erased_at IS NOT NULL
           OR NOT EXISTS (SELECT 1 FROM commercial_auth_users WHERE user_id = NEW.user_id
               AND security->'active' = 'true'::jsonb AND security->'email_verified' = 'true'::jsonb)
           OR EXISTS (SELECT 1 FROM commercial_erased_users WHERE user_hash =
               encode(sha256(convert_to(NEW.user_id,'UTF8')),'hex')) THEN
            RAISE EXCEPTION 'Invalid manual payment creation' USING ERRCODE = '55000';
        END IF;
        NEW.created_at := clock_timestamp();
        NEW.expires_at := NEW.created_at + INTERVAL '48 hours';
        RETURN NEW;
    END IF;
    IF pg_trigger_depth() > 1 AND NEW.erased_at IS NOT NULL AND OLD.erased_at IS NULL
       AND NEW.user_id = 'ERASED' AND NEW.transaction_number IS NULL AND NEW.decision_note IS NULL
       AND (to_jsonb(NEW) - ARRAY['user_id','transaction_number','decision_note','erased_at'])
           = (to_jsonb(OLD) - ARRAY['user_id','transaction_number','decision_note','erased_at'])
       AND EXISTS (SELECT 1 FROM commercial_erased_users WHERE user_hash =
           encode(sha256(convert_to(OLD.user_id,'UTF8')),'hex')) THEN
        RETURN NEW;
    END IF;
    IF OLD.erased_at IS NOT NULL OR
       (to_jsonb(NEW) - ARRAY['status','transaction_number','transaction_fingerprint','submitted_at','version'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['status','transaction_number','transaction_fingerprint','submitted_at','version'])
       OR NEW.version <> OLD.version + 1 OR OLD.status <> 'created' THEN
        RAISE EXCEPTION 'Manual payment identity or terminal state is immutable' USING ERRCODE = '55000';
    END IF;
    -- OWNER decisions and their atomic grant/audit integration are deliberately not enabled yet.
    IF NEW.status = 'pending' THEN
        IF clock_timestamp() >= OLD.expires_at OR NEW.transaction_number IS NULL
           OR NEW.transaction_fingerprint IS DISTINCT FROM
               encode(sha256(convert_to('binance_manual:' || NEW.transaction_number,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'Invalid or late manual payment submission' USING ERRCODE = '55000';
        END IF;
        NEW.submitted_at := clock_timestamp();
    ELSIF NEW.status = 'expired' THEN
        IF clock_timestamp() < OLD.expires_at OR NEW.transaction_number IS NOT NULL
           OR NEW.transaction_fingerprint IS NOT NULL OR NEW.submitted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Manual payment is not due for expiry' USING ERRCODE = '55000';
        END IF;
    ELSE
        RAISE EXCEPTION 'Manual payment transition is not enabled' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE FUNCTION binance_manual_payment_reject_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Manual payment history cannot be deleted' USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;
CREATE FUNCTION binance_manual_payment_erase() RETURNS trigger AS $$
BEGIN
    UPDATE binance_manual_payment_requests SET user_id = 'ERASED', transaction_number = NULL,
      decision_note = NULL, erased_at = clock_timestamp()
      WHERE encode(sha256(convert_to(user_id,'UTF8')),'hex') = NEW.user_hash AND erased_at IS NULL;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER binance_manual_payment_insert_update BEFORE INSERT OR UPDATE ON binance_manual_payment_requests
    FOR EACH ROW EXECUTE FUNCTION binance_manual_payment_guard();
CREATE TRIGGER binance_manual_payment_no_delete BEFORE DELETE OR TRUNCATE ON binance_manual_payment_requests
    FOR EACH STATEMENT EXECUTE FUNCTION binance_manual_payment_reject_delete();
CREATE TRIGGER binance_manual_payment_erasure AFTER INSERT ON commercial_erased_users
    FOR EACH ROW EXECUTE FUNCTION binance_manual_payment_erase();
ALTER TABLE binance_manual_payment_requests ENABLE ALWAYS TRIGGER binance_manual_payment_insert_update;
ALTER TABLE binance_manual_payment_requests ENABLE ALWAYS TRIGGER binance_manual_payment_no_delete;
ALTER TABLE commercial_erased_users ENABLE ALWAYS TRIGGER binance_manual_payment_erasure;

-- ===== 20261011_002_binance_manual_entitlements (audit kuralı yukarıda korunarak eklendi) =====
CREATE TABLE binance_manual_periods (
    payment_request_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (period_end = period_start + INTERVAL '720 hours')
);
CREATE INDEX binance_manual_period_user ON binance_manual_periods(user_id,period_end DESC)
    WHERE revoked_at IS NULL;

CREATE OR REPLACE FUNCTION binance_manual_payment_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'created' OR NEW.version <> 1 OR NEW.transaction_number IS NOT NULL
           OR NEW.transaction_fingerprint IS NOT NULL OR NEW.submitted_at IS NOT NULL
           OR NEW.decided_by IS NOT NULL OR NEW.decided_at IS NOT NULL
           OR NEW.decision_note IS NOT NULL OR NEW.erased_at IS NOT NULL
           OR NOT EXISTS (SELECT 1 FROM commercial_auth_users WHERE user_id = NEW.user_id
               AND security->'active' = 'true'::jsonb AND security->'email_verified' = 'true'::jsonb)
           OR EXISTS (SELECT 1 FROM commercial_erased_users WHERE user_hash =
               encode(sha256(convert_to(NEW.user_id,'UTF8')),'hex')) THEN
            RAISE EXCEPTION 'Invalid manual payment creation' USING ERRCODE = '55000';
        END IF;
        NEW.created_at := clock_timestamp();
        NEW.expires_at := NEW.created_at + INTERVAL '48 hours';
        RETURN NEW;
    END IF;
    IF pg_trigger_depth() > 1 AND NEW.erased_at IS NOT NULL AND OLD.erased_at IS NULL
       AND NEW.user_id = 'ERASED' AND NEW.transaction_number IS NULL AND NEW.decision_note IS NULL
       AND (to_jsonb(NEW) - ARRAY['user_id','transaction_number','decision_note','erased_at'])
           = (to_jsonb(OLD) - ARRAY['user_id','transaction_number','decision_note','erased_at'])
       AND EXISTS (SELECT 1 FROM commercial_erased_users WHERE user_hash =
           encode(sha256(convert_to(OLD.user_id,'UTF8')),'hex')) THEN
        RETURN NEW;
    END IF;
    IF OLD.erased_at IS NOT NULL OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'Invalid payment version or erased request' USING ERRCODE = '55000';
    END IF;
    IF OLD.status = 'pending' AND NEW.status IN ('approved','rejected') THEN
        IF (to_jsonb(NEW) - ARRAY['status','decided_by','decided_at','decision_note','version'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['status','decided_by','decided_at','decision_note','version'])
           OR NOT EXISTS (SELECT 1 FROM commercial_auth_users WHERE user_id=NEW.decided_by
               AND security->>'role'='OWNER' AND security->'active'='true'::jsonb)
           OR NEW.decided_at IS NULL
           OR (NEW.status='rejected' AND COALESCE(char_length(btrim(NEW.decision_note)),0)=0) THEN
            RAISE EXCEPTION 'Invalid OWNER payment decision' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;
    IF (to_jsonb(NEW) - ARRAY['status','transaction_number','transaction_fingerprint','submitted_at','version'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['status','transaction_number','transaction_fingerprint','submitted_at','version'])
       OR OLD.status <> 'created' THEN
        RAISE EXCEPTION 'Manual payment identity or terminal state is immutable' USING ERRCODE = '55000';
    END IF;
    IF NEW.status = 'pending' THEN
        IF clock_timestamp() >= OLD.expires_at OR NEW.transaction_number IS NULL
           OR NEW.transaction_fingerprint IS DISTINCT FROM
               encode(sha256(convert_to('binance_manual:' || NEW.transaction_number,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'Invalid or late manual payment submission' USING ERRCODE = '55000';
        END IF;
        NEW.submitted_at := clock_timestamp();
    ELSIF NEW.status = 'expired' THEN
        IF clock_timestamp() < OLD.expires_at OR NEW.transaction_number IS NOT NULL
           OR NEW.transaction_fingerprint IS NOT NULL OR NEW.submitted_at IS NOT NULL THEN
            RAISE EXCEPTION 'Manual payment is not due for expiry' USING ERRCODE = '55000';
        END IF;
    ELSE
        RAISE EXCEPTION 'Invalid manual payment transition' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE FUNCTION binance_manual_period_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.revoked_at IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM binance_manual_payment_requests WHERE id=NEW.payment_request_id
              AND user_id=NEW.user_id AND status='approved' AND erased_at IS NULL) THEN
            RAISE EXCEPTION 'Approved payment required for period' USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
    END IF;
    IF pg_trigger_depth() > 1 AND NEW.user_id='ERASED' AND NEW.revoked_at IS NOT NULL
       AND (to_jsonb(NEW) - ARRAY['user_id','revoked_at']) =
           (to_jsonb(OLD) - ARRAY['user_id','revoked_at'])
       AND EXISTS (SELECT 1 FROM commercial_erased_users WHERE user_hash =
           encode(sha256(convert_to(OLD.user_id,'UTF8')),'hex')) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Payment periods are immutable' USING ERRCODE = '55000';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER binance_manual_period_insert_update BEFORE INSERT OR UPDATE ON binance_manual_periods
    FOR EACH ROW EXECUTE FUNCTION binance_manual_period_guard();
CREATE TRIGGER binance_manual_period_no_delete BEFORE DELETE OR TRUNCATE ON binance_manual_periods
    FOR EACH STATEMENT EXECUTE FUNCTION binance_manual_payment_reject_delete();

CREATE FUNCTION binance_manual_decision_complete() RETURNS trigger AS $$
DECLARE payment RECORD;
BEGIN
    IF TG_TABLE_NAME='binance_manual_periods' THEN
        SELECT * INTO payment FROM binance_manual_payment_requests WHERE id=NEW.payment_request_id;
    ELSE
        SELECT * INTO payment FROM binance_manual_payment_requests WHERE id=NEW.id;
    END IF;
    IF payment.erased_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF payment.status IN ('approved','rejected') AND (
        NOT EXISTS (SELECT 1 FROM audit_log WHERE target_type='BINANCE_PAYMENT' AND target_id=payment.id
            AND actor_role='OWNER' AND actor_user_id=payment.decided_by
            AND action='payment.binance.' || payment.status)
        OR (payment.status='approved' AND NOT EXISTS (
            SELECT 1 FROM binance_manual_periods WHERE payment_request_id=payment.id
              AND user_id=payment.user_id AND revoked_at IS NULL))
        OR (payment.status='rejected' AND EXISTS (
            SELECT 1 FROM binance_manual_periods WHERE payment_request_id=payment.id))) THEN
        RAISE EXCEPTION 'Decision requires atomic audit and matching period' USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER binance_manual_payment_complete AFTER INSERT OR UPDATE ON binance_manual_payment_requests
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION binance_manual_decision_complete();
CREATE CONSTRAINT TRIGGER binance_manual_period_complete AFTER INSERT ON binance_manual_periods
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION binance_manual_decision_complete();

CREATE FUNCTION binance_manual_period_erase() RETURNS trigger AS $$
BEGIN
    UPDATE binance_manual_periods SET user_id='ERASED',revoked_at=clock_timestamp()
      WHERE encode(sha256(convert_to(user_id,'UTF8')),'hex')=NEW.user_hash;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER binance_manual_period_erasure AFTER INSERT ON commercial_erased_users
    FOR EACH ROW EXECUTE FUNCTION binance_manual_period_erase();
ALTER TABLE binance_manual_periods ENABLE ALWAYS TRIGGER binance_manual_period_insert_update;
ALTER TABLE binance_manual_periods ENABLE ALWAYS TRIGGER binance_manual_period_no_delete;
ALTER TABLE binance_manual_periods ENABLE ALWAYS TRIGGER binance_manual_period_complete;
ALTER TABLE binance_manual_payment_requests ENABLE ALWAYS TRIGGER binance_manual_payment_complete;
ALTER TABLE commercial_erased_users ENABLE ALWAYS TRIGGER binance_manual_period_erasure;

-- ===== 20261011_003_binance_notifications (bildirim kuralı yukarıda korunarak eklendi) =====
CREATE TABLE binance_notification_reads (
 outbox_id TEXT PRIMARY KEY,
 read_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION binance_notification_read_guard() RETURNS trigger AS $$
BEGIN
 IF TG_OP <> 'INSERT' THEN
  RAISE EXCEPTION 'Notification receipts are immutable' USING ERRCODE='55000';
 END IF;
 IF NOT EXISTS (SELECT 1 FROM notification_outbox WHERE id=NEW.outbox_id
  AND kind='payment.binance.bell' AND status='sent' AND recipient_user_id <> 'ERASED') THEN
  RAISE EXCEPTION 'Delivered bell required' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER binance_notification_read_insert_update BEFORE INSERT OR UPDATE ON binance_notification_reads
 FOR EACH ROW EXECUTE FUNCTION binance_notification_read_guard();
CREATE TRIGGER binance_notification_read_no_delete BEFORE DELETE OR TRUNCATE ON binance_notification_reads
 FOR EACH STATEMENT EXECUTE FUNCTION notification_outbox_reject_delete();
ALTER TABLE binance_notification_reads ENABLE ALWAYS TRIGGER binance_notification_read_insert_update;
ALTER TABLE binance_notification_reads ENABLE ALWAYS TRIGGER binance_notification_read_no_delete;

COMMIT;
