import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).parents[1] / "app"
spec = importlib.util.spec_from_file_location("server_i18n", APP_DIR / "server_i18n.py")
assert spec and spec.loader
si = importlib.util.module_from_spec(spec)
spec.loader.exec_module(si)

PUSH_AND_API_TEXT = (
    "Emir açıldı", "Pozisyon kapandı", "Stop / koruma sorunu", "Bağlantı sorunu", "İzin süresi / kilit", "Acil durum",
    "Telefon bildirimi çalışıyor.", "Telefon bildirimi henüz hazır değil.",
    "Bu hesapta çok fazla cihaz kayıtlı. Eski bir cihazı kapat.",
    "Bu hesapta kayıtlı cihaz bulunamadı veya gönderilemedi.",
    "BTCUSDT tracked pozisyon kapandı; net işlem PnL 3.20 USDT (funding hariç).",
    "ETHUSDT Stop doğrulandı; TP emirleri borsada kuruldu.",
    "24 saatlik canlı izin sona erdi; 15 dakika içinde yeniden onay gerekli. Açık pozisyon korunuyor.",
    "Binance marjin çağrısı bildirdi; V25 yeni girişleri kilitledi. Hesabı derhal kontrol edin.",
    "Yeni işlem girişleri bakım modu nedeniyle geçici olarak durduruldu. Açık pozisyonlar ve risk koruması etkilenmez.",
    "SİSTEM BAKIMDA",
)


class ServerTranslationTests(unittest.TestCase):
    def test_every_push_and_maintenance_text_has_an_english_version(self):
        for text in PUSH_AND_API_TEXT:
            with self.subTest(text=text):
                english = si.to_english(text)
                self.assertNotEqual(english, text)
                self.assertNotRegex(english, "[çğıöşüÇĞİÖŞÜ]")

    def test_dynamic_parts_survive_translation(self):
        self.assertEqual(si.to_english("SOLUSDT canlı emir gönderildi (abc123)."), "SOLUSDT live order sent (abc123).")

    def test_only_english_accounts_are_translated(self):
        self.assertEqual(si.localize("Pozisyon kapandı", "en"), "Position closed")
        self.assertEqual(si.localize("Pozisyon kapandı", "tr"), "Pozisyon kapandı")
        self.assertEqual(si.localize("Pozisyon kapandı", None), "Pozisyon kapandı")
        self.assertEqual(si.localize("Pozisyon kapandı", "de"), "Pozisyon kapandı")

    def test_unknown_or_empty_text_is_returned_unchanged(self):
        for text in ("", "   ", "12345", "Bu cümle sözlükte yok, olduğu gibi kalmalı."):
            self.assertEqual(si.to_english(text), text)

    def test_missing_dictionary_never_breaks_a_notification(self):
        original = si._DICT_PATH
        try:
            si._DICT_PATH = Path(tempfile.gettempdir()) / "yok-i18n-en.json"
            si._dictionary.cache_clear()
            self.assertEqual(si.to_english("Pozisyon kapandı"), "Pozisyon kapandı")
            broken = Path(tempfile.mkdtemp()) / "i18n-en.json"
            broken.write_text("{bozuk", encoding="utf-8")
            si._DICT_PATH = broken
            si._dictionary.cache_clear()
            self.assertEqual(si.to_english("Pozisyon kapandı"), "Pozisyon kapandı")
        finally:
            si._DICT_PATH = original
            si._dictionary.cache_clear()
        self.assertEqual(si.to_english("Pozisyon kapandı"), "Position closed")

    def test_dictionary_file_is_well_formed(self):
        data = json.loads(original_dictionary().read_text(encoding="utf-8"))
        for source, target in data["p"]:
            self.assertEqual(source.count("{}") + source.count("{#}"), target.count("{}") + target.count("{#}"), source)


def original_dictionary() -> Path:
    return Path(__file__).parents[2] / "i18n-en.json"


class AccountLanguageTests(unittest.TestCase):
    class Pool:
        def __init__(self, payload=None, fail=False):
            self.payload, self.fail = payload, fail

        async def fetchrow(self, sql, user_id):
            if self.fail:
                raise OSError("db down")
            return None if self.payload is None else {"payload": self.payload}

    def language(self, pool, user_id="u"):
        return asyncio.run(si.account_language(pool, user_id))

    def test_reads_the_saved_language(self):
        self.assertEqual(self.language(self.Pool({"language": "en"})), "en")
        self.assertEqual(self.language(self.Pool(json.dumps({"language": "en"}))), "en")
        self.assertEqual(self.language(self.Pool({"language": "tr"})), "tr")

    def test_unknown_missing_or_unreadable_falls_back_to_turkish(self):
        self.assertEqual(self.language(self.Pool({"language": "de"})), "tr")
        self.assertEqual(self.language(self.Pool({})), "tr")
        self.assertEqual(self.language(self.Pool(None)), "tr")
        self.assertEqual(self.language(self.Pool(fail=True)), "tr")
        self.assertEqual(self.language(None), "tr")
        self.assertEqual(self.language(self.Pool({"language": "en"}), user_id=""), "tr")


if __name__ == "__main__":
    unittest.main()
