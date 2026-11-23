import unittest
from unittest.mock import patch
from test_watcher import WatchTests
import admin_store as store
import manual_add


class ManualTests(unittest.TestCase):
    setUp = WatchTests.setUp
    post = WatchTests.post

    def test_mixed_without_copy(self):
        result = manual_add.add({"urls": ["https://youtu.be/abcdefghijk", self.post()["url"]], "keep_copy": False})
        self.assertEqual(len(result["results"]), 2)
        cat, _ = store.load_catalog()
        self.assertEqual(len(cat["videos"]), 2)
        self.assertTrue(all(v["needs_title"] for v in cat["videos"]))
        with store.db() as c:
            self.assertEqual(c.execute("select count(*) from jobs").fetchone()[0], 0)

    def test_duplicate_preserves_metadata_and_one_job(self):
        body = {"urls": ["https://youtu.be/abcdefghijk"], "keep_copy": True,
                "metadata": {"title": "My title", "tags": ["test"], "category": ["Truth"], "pinned": True, "notes": "Keep this"}}
        manual_add.add(body)
        body["metadata"]["title"] = "Must not replace"
        result = manual_add.add(body)
        self.assertEqual(result["results"][0]["status"], "duplicate skipped")
        cat, _ = store.load_catalog()
        self.assertEqual(cat["videos"][0]["title"], "My title")
        self.assertTrue(cat["videos"][0]["pinned"])
        with store.db() as c:
            self.assertEqual(c.execute("select count(*) from jobs").fetchone()[0], 1)

    def test_short_link_and_no_end_card(self):
        with patch("tiktok_links.resolve", return_value=self.post()["url"]) as resolve:
            manual_add.add({"urls": ["https://vt.tiktok.com/example/"], "keep_copy": True})
            resolve.assert_called_once()
        with store.db() as c:
            self.assertEqual(c.execute("select add_end_card from jobs").fetchone()[0], 0)

    def test_invalid_batch_changes_nothing(self):
        with self.assertRaises(ValueError):
            manual_add.add({"urls": ["https://youtu.be/abcdefghijk", "https://example.com/not-supported"]})
        self.assertEqual(store.load_catalog()[0]["videos"], [])
