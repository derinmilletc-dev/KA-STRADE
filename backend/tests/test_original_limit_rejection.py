import unittest

from app import original_v2_live as ov
from app.v25_execution import LiveExchangeError


class LimitRejectionTests(unittest.TestCase):
    def test_pure_limit_rejections_do_not_lock(self):
        total = LiveExchangeError("Toplam teminat kullanımı sınırı aşılırdı.", http_status=423, check_key="total_margin")
        daily = LiveExchangeError("Günlük zarar bütçesi bu girişle aşılırdı; yeni pozisyon açılmadı.", http_status=423, check_key="total_open_risk")
        per_trade = LiveExchangeError("Original V2 stop kaybı işlem başı risk bütçesini aşıyor.", http_status=423, check_key="risk_budget")
        self.assertEqual(ov.limit_rejection_kind(total), "account")
        self.assertEqual(ov.limit_rejection_kind(daily), "account")
        self.assertEqual(ov.limit_rejection_kind(per_trade), "trade")

    def test_real_failures_still_fail_closed(self):
        unknown = LiveExchangeError("Toplam teminat kullanımı sınırı aşılırdı.", http_status=423, check_key="total_margin", unknown_execution=True)
        self.assertIsNone(ov.limit_rejection_kind(unknown))
        self.assertIsNone(ov.limit_rejection_kind(LiveExchangeError("Binance cevap vermedi", http_status=502)))
        self.assertIsNone(ov.limit_rejection_kind(LiveExchangeError("Günlük gerçekleşen zarar doğrulanamadı; yeni giriş kapalı.", http_status=423, check_key="daily_loss")))
        self.assertIsNone(ov.limit_rejection_kind(RuntimeError("boom")))


if __name__ == "__main__":
    unittest.main()
