-- PREPARE ONLY. Manual application requires separate approval.
-- Phone/browser push subscriptions for LIVE trade notifications. Rollback: DROP TABLE push_subscriptions;
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('web-push-schema-v1',0));

CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint      TEXT PRIMARY KEY CHECK (length(endpoint) BETWEEN 20 AND 2048),
    user_id       TEXT NOT NULL CHECK (length(user_id) BETWEEN 1 AND 128),
    p256dh        TEXT NOT NULL CHECK (length(p256dh) BETWEEN 20 AND 256),
    auth          TEXT NOT NULL CHECK (length(auth) BETWEEN 8 AND 128),
    kinds         TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    last_sent_at  TIMESTAMPTZ,
    failures      SMALLINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS push_subscriptions_user_idx ON push_subscriptions (user_id);

COMMIT;
