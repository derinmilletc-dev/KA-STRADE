-- Ücretsiz deneme üyelerinin günlük (UTC) canlı yeni pozisyon sayacı. Kişi kimliği yerine SHA-256 özeti tutulur.
-- Elle uygulanır: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f backend/migrations/20261128_003_live_trial_entries.sql
-- Birden fazla kez çalıştırmak güvenlidir. Tablo yoksa deneme üyesi canlı emir açamaz (güvenli taraf), ücretli üyeler etkilenmez.
BEGIN;
CREATE TABLE IF NOT EXISTS live_trial_entries (
    user_hash TEXT NOT NULL CHECK (user_hash ~ '^[a-f0-9]{64}$'),
    day DATE NOT NULL,
    count INTEGER NOT NULL CHECK (count BETWEEN 0 AND 100),
    PRIMARY KEY (user_hash, day)
);
COMMIT;
