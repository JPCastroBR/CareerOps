"""Tests for dashboard file-editor helpers (allowlist + validation)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from careerops import dashboard as dash  # noqa: E402
from careerops import telemetry as tel  # noqa: E402


class SafeFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_base = dash.BASE_DIR
        dash.BASE_DIR = Path(self._tmp.name)

    def tearDown(self):
        dash.BASE_DIR = self._old_base
        self._tmp.cleanup()

    def test_allows_allowed_files(self):
        self.assertEqual(dash.safe_file("config.json").name, "config.json")
        self.assertEqual(dash.safe_file("profile.yaml").name, "profile.yaml")

    def test_rejects_traversal_and_unknown(self):
        for bad in ["../etc/passwd", "/etc/passwd", "secrets/google-sa.json", ".env", ""]:
            self.assertIsNone(dash.safe_file(bad), bad)


class ValidateContentTests(unittest.TestCase):
    def test_json(self):
        self.assertEqual(dash.validate_content("config.json", '{"a": 1}'), (True, ""))
        ok, err = dash.validate_content("config.json", "{invalid")
        self.assertFalse(ok)
        self.assertIn("JSON", err)
        ok, _ = dash.validate_content("config.json", "[1, 2]")
        self.assertFalse(ok)

    def test_yaml(self):
        self.assertEqual(dash.validate_content("profile.yaml", "a: 1\nb: [2,3]"), (True, ""))
        ok, err = dash.validate_content("profile.yaml", "a: [unclosed")
        self.assertFalse(ok)
        self.assertIn("YAML", err)

    def test_text_any(self):
        self.assertEqual(dash.validate_content("data/inhire_seed.txt", "abc\n"), (True, ""))


class WriteBackupTests(unittest.TestCase):
    def test_write_and_backup(self):
        with tempfile.TemporaryDirectory() as td:
            tel.TELEMETRY_DIR = Path(td) / "telemetry"
            target = Path(td) / "config.json"
            target.write_text('{"old": true}', encoding="utf-8")
            dash.write_allowed_file(target, '{"new": true}')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"new": true}')
            backup = tel.TELEMETRY_DIR / "backups" / "config.json.bak"
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), '{"old": true}')


if __name__ == "__main__":
    unittest.main()


class GuidedHelpersTests(unittest.TestCase):
    def test_get_set_path(self):
        doc = {"a": {"b": {"c": 1}}}
        self.assertEqual(dash.get_path(doc, "a.b.c"), 1)
        self.assertIsNone(dash.get_path(doc, "a.x"))
        dash.set_path(doc, "a.b.d", 2)
        self.assertEqual(doc["a"]["b"]["d"], 2)
        dash.set_path(doc, "a.b.c", None)
        self.assertNotIn("c", doc["a"]["b"])
        dash.set_path(doc, "n.m", "x")
        self.assertEqual(doc["n"]["m"], "x")

    def test_render_doc(self):
        import json as _json
        from pathlib import Path as _P
        doc = {"b": 1, "a": [1, 2]}
        js = dash.render_doc(doc, _P("x.json"))
        self.assertEqual(_json.loads(js), doc)
        yml = dash.render_doc(doc, _P("x.yaml"))
        self.assertIn("b: 1", yml)

    def test_load_doc(self):
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as td:
            j = _P(td) / "c.json"
            j.write_text('{"x": 1}', encoding="utf-8")
            self.assertEqual(dash.load_doc(j), {"x": 1})
            y = _P(td) / "p.yaml"
            y.write_text("a: 2\n", encoding="utf-8")
            self.assertEqual(dash.load_doc(y), {"a": 2})
