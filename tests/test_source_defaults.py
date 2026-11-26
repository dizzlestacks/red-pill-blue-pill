"""Default categories per source, and categories chosen while reviewing an import."""
import json
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import admin_store as store  # noqa: E402


class SourceDefaultTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(tmp.name)
        patch.object(store, "STATE", root / "data").start()
        patch.object(store, "CATALOG", root / "data/videos.json").start()
        store.STATE.mkdir()
        store.CATALOG.write_text(json.dumps({"videos": []}))
        store.init()

    def test_clean_categories_merges_case_and_spaces(self):
        self.assertEqual(store.clean_categories([" End  Times ", "end times", "A,B", ""]), ["End Times", "A B"])
        with self.assertRaises(ValueError):
            store.clean_categories("End Times")

    def test_set_and_clear_defaults(self):
        self.assertEqual(store.set_source_default("tiktok", ["My TikToks", "my tiktoks"]), ["My TikToks"])
        store.set_source_default("yt:UC123", ["Sermons"])
        self.assertEqual(store.default_categories(["tiktok", "yt:UC123"]), ["My TikToks", "Sermons"])
        store.set_source_default("tiktok", [])
        self.assertNotIn("tiktok", store.source_defaults())
        with self.assertRaises(ValueError):
            store.set_source_default("../etc", ["x"])

    def test_tiktok_import_gets_defaults_and_review_choice_is_published(self):
        store.set_source_default("tiktok", ["My TikToks"])
        job = store.enqueue("https://www.tiktok.com/@wokearewe1/video/7300000000000000042", title="Hello")
        self.assertEqual(store.job_categories(store.get_job(job["id"])), ["My TikToks"])
        store.update_job(job["id"], category=json.dumps(["My TikToks", "Bible Study"]))
        self.assertEqual(store.job_categories(store.get_job(job["id"])), ["My TikToks", "Bible Study"])

    def test_explicit_category_wins_over_default(self):
        store.set_source_default("tiktok", ["My TikToks"])
        job = store.enqueue("https://www.tiktok.com/@wokearewe1/video/7300000000000000043", category=["Other"])
        self.assertEqual(store.job_categories(store.get_job(job["id"])), ["Other"])

    def test_bad_stored_category_is_ignored(self):
        self.assertEqual(store.job_categories({"category": "not json"}), [])


if __name__ == "__main__":
    main()
