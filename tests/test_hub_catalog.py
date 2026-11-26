"""Published videos reach the hub whatever their source, and "New" means new to the hub."""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import admin_store as store  # noqa: E402
import lite_site  # noqa: E402
import public_catalog as public  # noqa: E402

OWN = {"id": "own", "title": "tester", "added": "2024-01-02", "category": ["Imports"],
       "source_url": "https://www.tiktok.com/@owner/video/7300000000000000001", "url": "/media/tt-7300000000000000001"}
YT = {"id": "yt", "title": "YouTube only", "added": "2025-01-01", "youtube_id": "abcdefghijk"}
BOTH = {"id": "both", "title": "Both", "added": "2025-01-01", "youtube_id": "bcdefghijkl"}


class HubCatalogTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(store, "STATE", root / "data").start()
        patch.object(store, "CATALOG", root / "data/videos.json").start()
        patch.dict(os.environ, {"PI_PUBLIC_URL": ""}).start()
        store.STATE.mkdir()
        store.CATALOG.write_text(json.dumps({"videos": [YT]}))
        store.init()

    def test_new_videos_get_published_stamp_once(self):
        cat, rev = store.load_catalog()
        cat["videos"].append(dict(OWN))
        store.save_catalog(cat, rev)
        cat, rev = store.load_catalog()
        own = next(v for v in cat["videos"] if v["id"] == "own")
        yt = next(v for v in cat["videos"] if v["id"] == "yt")
        self.assertRegex(own["published"], r"^\d{4}-\d\d-\d\dT")
        self.assertNotIn("published", yt)  # existing videos keep their place
        stamp = own["published"]
        cat["videos"][0]["title"] = "Edited"
        store.save_catalog(cat, rev)
        own = next(v for v in store.load_catalog()[0]["videos"] if v["id"] == "own")
        self.assertEqual(own["published"], stamp)

    def test_public_export_keeps_every_source(self):
        data = public.export({"videos": [dict(OWN, published="2026-11-20T10:00:00Z"), YT, BOTH]})
        by_id = {v["id"]: v for v in data["videos"]}
        self.assertEqual(set(by_id), {"own", "yt", "both"})
        self.assertEqual(by_id["own"]["published"], "2026-11-20T10:00:00Z")
        self.assertEqual(by_id["own"]["tiktok_url"], OWN["source_url"])
        self.assertNotIn("limited_unavailable", by_id["own"])
        self.assertEqual(by_id["both"]["youtube_id"], "bcdefghijkl")
        own_without_link = public.export({"videos": [{"id": "x", "title": "x", "url": "/media/tt-1"}]})["videos"][0]
        self.assertTrue(own_without_link["limited_unavailable"])  # listed, marked unavailable, never dropped

    def test_lite_catalog_lists_own_copy_videos(self):
        data = lite_site.limited_catalog({"videos": [OWN, YT, BOTH]})
        self.assertEqual([v["id"] for v in data["videos"]], ["own", "yt", "both"])
        self.assertNotIn("url", data["videos"][0])


class ServerPageTests(TestCase):
    def setUp(self):
        import server
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        self.base = f"http://127.0.0.1:{self.http.server_port}"

    def test_unknown_page_gets_styled_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.base + "/no-such-page")
        self.assertEqual(ctx.exception.code, 404)
        self.assertIn("text/html", ctx.exception.headers["Content-Type"])
        self.assertIn("<html", ctx.exception.read().decode().lower())

    def test_public_pages_carry_release_line(self):
        with urllib.request.urlopen(self.base + "/videos.html") as response:
            body = response.read().decode()
        self.assertIn('id="release-marker"', body)
        self.assertIn(store.RELEASE, body)


if __name__ == "__main__":
    main()
