import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lite_site


class NetlifyRoutesTests(unittest.TestCase):
    def test_admin_routes(self):
        config = tomllib.loads((ROOT / "netlify.toml").read_text())
        rules = {r["from"]: r for r in config["redirects"]}
        for path in ("/admin", "/admin/", "/editor.html", "/editor"):
            rule = rules[path]
            self.assertEqual(rule["to"], "https://woke.you2mp3.music/admin")
            self.assertEqual(rule["status"], 302)
            self.assertTrue(rule["force"])

    def test_lite_contains_branded_404_not_admin(self):
        with tempfile.TemporaryDirectory() as folder:
            output = lite_site.generate(json.loads((ROOT / "videos.json").read_text()), folder)
            page = (output / "404.html").read_text()
            self.assertIn('href="/videos.html"', page)
            self.assertIn("WOKEAREWE", page)
            self.assertIn("Page not found", page)
            self.assertNotIn('src="lite-data.js"', page)
            for name in ("admin.html", "editor.html", "server.py", ".private"):
                self.assertFalse((output / name).exists())
