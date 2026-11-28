-- Hatırlatma kayıt tablosunu kaldırır (yalnız gönderim kayıtlarını siler; üyelik ve ödemeler etkilenmez).
BEGIN;
DROP TABLE IF EXISTS nowpayments_reminders;
COMMIT;
