-- Günlük deneme sayacı tablosunu kaldırır (yalnız sayaçları siler; üyelik ve ödemeler etkilenmez).
BEGIN;
DROP TABLE IF EXISTS live_trial_entries;
COMMIT;
