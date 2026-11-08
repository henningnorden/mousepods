# SPDX-License-Identifier: Apache-2.0
"""UI language resolution and string tables.

Run:  python -m unittest tests.test_i18n -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import i18n  # noqa: E402


class ResolveLangTests(unittest.TestCase):
    def test_resolve_lang_setting_wins(self):
        self.assertEqual(i18n.resolve_lang("fr", "de_DE.UTF-8"), "fr")

    def test_resolve_lang_auto_from_env(self):
        self.assertEqual(i18n.resolve_lang("auto", "de_AT.UTF-8"), "de")

    def test_resolve_lang_unsupported_env(self):
        self.assertEqual(i18n.resolve_lang("auto", "pt_BR.UTF-8"), "en")

    def test_resolve_lang_c_posix_none(self):
        for env in ("C", "POSIX", "", None):
            self.assertEqual(i18n.resolve_lang("auto", env), "en", env)

    def test_resolve_lang_unknown_setting(self):
        self.assertEqual(i18n.resolve_lang("xx", "de_DE"), "de")

    def test_resolve_lang_none_setting(self):
        self.assertEqual(i18n.resolve_lang(None, "fr_FR.UTF-8"), "fr")


class TrFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(self.tmp, "en.json"), "w", encoding="utf-8") as f:
            json.dump({"k": "Hello", "p": "{hz} Hz"}, f)
        with open(os.path.join(self.tmp, "de.json"), "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.saved = i18n.STRINGS_DIR
        i18n.STRINGS_DIR = self.tmp
        i18n.clear_cache()

    def tearDown(self):
        i18n.STRINGS_DIR = self.saved
        i18n.clear_cache()
        shutil.rmtree(self.tmp)

    def test_tr_falls_back_to_english(self):
        self.assertEqual(i18n.tr("k", "de"), "Hello")
        self.assertEqual(i18n.tr("nope", "de"), "nope")
        self.assertEqual(i18n.tr("p", "en", hz=99), "99 Hz")

    def test_tr_missing_placeholder_keeps_text(self):
        self.assertEqual(i18n.tr("p", "en"), "{hz} Hz")

    def test_malformed_translation_does_not_raise(self):
        import json as _j
        with open(os.path.join(self.tmp, "de.json"), "w", encoding="utf-8") as f:
            _j.dump({"p": "{0} oder {hz:.1f} oder {", "k": "Hallo"}, f)
        i18n.clear_cache()
        self.assertEqual(i18n.tr("p", "de", hz=3), "{0} oder {hz:.1f} oder {")

    def test_missing_language_file_falls_back(self):
        self.assertEqual(i18n.tr("k", "fr"), "Hello")


class TablesTests(unittest.TestCase):
    def test_tables_have_same_keys(self):
        keys = {}
        for lang in i18n.SUPPORTED:
            with open(os.path.join(ROOT, "ui", "i18n", f"{lang}.json"), encoding="utf-8") as f:
                keys[lang] = set(json.load(f))
        self.assertEqual(keys["en"], keys["de"])
        self.assertEqual(keys["en"], keys["fr"])

    def test_payload_shape(self):
        p = i18n.i18n_payload({"ui_lang": "de"}, None)
        self.assertEqual(p["lang"], "de")
        self.assertEqual(p["setting"], "de")
        self.assertEqual(p["strings"]["lang.de"], "Deutsch")

    def test_env_lang_order(self):
        env = {"LANG": "fr_FR.UTF-8", "LC_MESSAGES": "de_DE.UTF-8"}
        self.assertEqual(i18n.env_lang(env), "de_DE.UTF-8")
        self.assertEqual(i18n.env_lang({"LC_ALL": "en_US", **env}), "en_US")
        self.assertIsNone(i18n.env_lang({}))

    def test_env_lang_prefers_language_list(self):
        self.assertEqual(i18n.env_lang({"LANGUAGE": "de:en", "LANG": "en_US.UTF-8"}), "de")
        self.assertEqual(i18n.env_lang({"LANGUAGE": "", "LANG": "fr_FR.UTF-8"}), "fr_FR.UTF-8")


class SettingFromFileTests(unittest.TestCase):
    def test_reads_ui_lang_from_mousepods_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w") as f:
                json.dump({"ui_lang": "de", "mode": "souris"}, f)
            self.assertEqual(i18n.setting_from_file(p), "de")

    def test_missing_or_broken_file_is_auto(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(i18n.setting_from_file(os.path.join(d, "nope.json")), "auto")
            p = os.path.join(d, "bad.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(i18n.setting_from_file(p), "auto")

    def test_garmin_standalone_uses_mousepods_config(self):
        with open(os.path.join(ROOT, "garmin", "garmin_mouse.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("i18n.setting_from_file(", src)


class LanguageSwitchTests(unittest.TestCase):
    def test_i18n_payload_without_setting_is_auto(self):
        p = i18n.i18n_payload({}, "de_DE.UTF-8")
        self.assertEqual((p["setting"], p["lang"]), ("auto", "de"))

    def test_i18n_payload_explicit_setting(self):
        p = i18n.i18n_payload({"ui_lang": "fr"}, "de_DE.UTF-8")
        self.assertEqual(p["lang"], "fr")

    def test_ui_has_language_switch(self):
        with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('data-key="ui_lang"', html)
        block = html.split('data-key="ui_lang"', 1)[1].split("</div>", 1)[0]
        for v in ("auto", "en", "de", "fr"):
            self.assertIn(f'data-v="{v}"', block)

    def test_ui_reloads_translations_on_switch(self):
        with open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertRegex(html, r'ui_lang[^\n]*loadI18n\(')


if __name__ == "__main__":
    unittest.main()
