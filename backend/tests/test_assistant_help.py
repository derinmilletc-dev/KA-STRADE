import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from decimal import Decimal

from app import assistant_help, binance_demo, binance_manual_payments, subscription_core, v25_execution
from app.analyst_credits import CreditConfig


class AssistantHelpTests(unittest.TestCase):
    def setUp(self):
        assistant_help.load_articles.cache_clear()
        self.addCleanup(assistant_help.load_articles.cache_clear)

    def test_language_parity_valid_sources_and_no_embedded_numeric_copy(self):
        tr = assistant_help.load_articles("tr")
        en = assistant_help.load_articles("en")
        self.assertEqual({row.id for row in tr}, {row.id for row in en})
        expected = {
            "api-connection", "secret-safety", "environments", "membership", "plans", "billing",
            "analyst", "premium", "master-trade", "analyst-scanner", "workspaces", "gates",
            "final-decision", "confidence", "opportunity", "smart-score", "mtf", "trigger",
            "invalidation", "risk-reward", "directions", "levels", "locked", "arm", "consent",
            "dry-run", "scanner-candidate", "exposure", "protection", "reconciliation",
            "account-mode", "fingerprint", "risk-warning", "support", "site-map",
            "live-guide", "live-screen", "live-status", "live-scan", "margin-mode", "risk-target",
            "live-session", "live-positions", "live-notify", "connection-tab", "how-bot-works", "kais-ai", "margin-who",
            "plan-compare", "trial", "payment", "ai-limits",
            "dca-bot", "grid-bot", "farmer", "studio-quick", "coinbase", "profit-protection", "language-notify", "maintenance", "iphone-fullscreen",
        }
        self.assertEqual({row.id for row in tr}, expected)
        root = Path(__file__).resolve().parents[2]
        for row in (*tr, *en):
            with self.subTest(article=row.id):
                self.assertNotRegex(row.title + row.content, r"\d")
                for source in row.code_sources:
                    self.assertTrue(root.joinpath(*source.split("/")).is_file(), source)
        self.assertNotEqual(tr[0].content, en[0].content)

    def test_all_numbers_render_from_runtime_sources_even_after_templates_cached(self):
        config = CreditConfig(total=140, cost=14, window_hours=13, cache_minutes=8)
        assistant_help.entries(CreditConfig(), "en")
        for language in ("tr", "en"):
            with patch.dict(binance_manual_payments.PLAN_AMOUNTS, {"MASTER_MODE": Decimal("177.25"), "SMART_TRADE": Decimal("44.5")}), \
                 patch.object(subscription_core, "TRIAL_DAYS", 9), \
                 patch.object(binance_demo, "ARM_SECONDS", 11 * 60), \
                 patch.object(v25_execution, "LIVE_CONSENT_SECONDS", 17 * 3600), \
                 patch.object(v25_execution, "LIVE_ARM_SECONDS", 19 * 3600), \
                 patch.object(v25_execution, "LIVE_AUTO_SESSION_SECONDS", 23 * 60), \
                 patch.object(v25_execution, "LIVE_CONSENT_GRACE_SECONDS", 7 * 60):
                rows = {row["id"]: row for row in assistant_help.entries(config, language)}
            for value in ("140", "14", "13", "8"):
                self.assertIn(value, rows["analyst"]["content"])
            for value in ("177.25", "44.50", "9"):
                self.assertIn(value, rows["plans"]["content"])
            for value in ("177.25", "44.50"):
                self.assertIn(value, rows["payment"]["content"])
            for value in ("11", "17", "19", "23", "7"):
                self.assertIn(value, rows["gates"]["content"])
            for row in rows.values():
                self.assertNotRegex(row["content"], r"\{[A-Z_]+\}")
            changed = {row["id"]: row for row in assistant_help.entries(replace(config, total=160), language)}
            self.assertIn("160", changed["analyst"]["content"])

    def test_language_scoped_ranked_search_and_turkish_normalization(self):
        cases = (
            ("tr", "API anahtarını doğrulama", "api-connection"),
            ("tr", "ŞİFREMİ unuttum", "membership"),
            ("tr", "Smart Score", "smart-score"),
            ("tr", "kredilerim", "analyst"),
            ("tr", "secretim", "secret-safety"),
            ("en", "refund cancel renewal", "billing"),
            ("tr", "Binance Pay ile nasıl ödeme yaparım", "payment"),
            ("tr", "deneme süresinde canlı işlem açabilir miyim", "trial"),
            ("tr", "smart trade ile master mode farkı", "plan-compare"),
            ("tr", "grafik ai kaç hak", "ai-limits"),
            ("en", "How do I pay with Binance Pay", "payment"),
            ("en", "free trial", "trial"),
            ("en", "Trigger Monitor", "trigger"),
            ("en", "One-way Hedge", "account-mode"),
            ("en", "Final Decision", "final-decision"),
            ("en", "Risk Reward", "risk-reward"),
        )
        for language, query, expected in cases:
            with self.subTest(query=query):
                results = assistant_help.search(query, language, CreditConfig())
                self.assertEqual(results[0]["id"], expected)
                self.assertLessEqual(len(results), assistant_help.HELP_RESULT_LIMIT)
                self.assertTrue(all(row["language"] == language for row in results))
                self.assertEqual(results, assistant_help.search(query, language, CreditConfig()))
        for query in ("", "   ", "unmatchedxyz", "the and where"):
            self.assertEqual(assistant_help.search(query, "en", CreditConfig()), [])
        self.assertEqual(assistant_help.search("kredilerim", "en", CreditConfig()), [])
        self.assertEqual(assistant_help.search("registration", "tr", CreditConfig()), [])

    def test_missing_malformed_or_invalid_articles_fail_explicitly_without_content_logging(self):
        valid = {"id": "help", "title": "Help", "content": "Safe", "keywords": ["help"], "code_sources": ["README.md"]}
        invalid = ("not-json", "[]", json.dumps([valid, valid]),
                   json.dumps([{**valid, "content": "{UNKNOWN}"}]),
                   json.dumps([{**valid, "content": "{ANALYSIS_COST.__class__}"}]),
                   json.dumps([{**valid, "keywords": []}]))
        with tempfile.TemporaryDirectory() as directory, patch.object(assistant_help, "KB_DIRECTORY", Path(directory)):
            with self.assertLogs(assistant_help.logger, level="ERROR"), self.assertRaises(assistant_help.KnowledgeBaseError):
                assistant_help.search("help", "en", CreditConfig())
            for raw in invalid:
                with self.subTest(raw=raw), patch.object(Path, "read_text", return_value=raw), \
                     self.assertLogs(assistant_help.logger, level="ERROR") as logs:
                    assistant_help.load_articles.cache_clear()
                    with self.assertRaises(assistant_help.KnowledgeBaseError):
                        assistant_help.search("help", "en", CreditConfig())
                    self.assertNotIn(raw, "\n".join(logs.output))

    def test_no_legacy_features_advertised_as_available(self):
        for language in ("tr", "en"):
            rows = {row["id"]: row for row in assistant_help.entries(CreditConfig(), language)}
            text = " ".join(row["content"] for row in rows.values())
            # Grid işlemleri artık Smart Trade ve Master Mode planlarında satılan bir özelliktir.
            self.assertNotRegex(text, r"(?i)CommercialHub|lisans|license|ajan|\bagent\b|2FA|24/7|7/24")
            # Müşteriye iç/teknik kavram sızmaz.
            self.assertNotRegex(text, r"(?i)backend|stripe|kodda|in code|endpoint|deployment|idempotency|bilgi taban|knowledge base|sunucu kasas|server vault")
            self.assertIn("kapalı", rows["environments"]["content"]) if language == "tr" else self.assertIn("disabled", rows["environments"]["content"])
            self.assertIn("verified=false", rows["protection"]["content"])
            self.assertIn("gece yarısı sıfırlanmaz", rows["analyst"]["content"]) if language == "tr" else self.assertIn("not at midnight", rows["analyst"]["content"])
            self.assertIn("RUN DEMO TEST", rows["api-connection"]["content"])


if __name__ == "__main__":
    unittest.main()
