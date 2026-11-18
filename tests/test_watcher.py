import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import admin_store as store
import tiktok_watch as watch
import watch_provider
import media_worker as worker


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.temp.name)
        patch.object(store, "STATE", root).start()
        patch.object(store, "CATALOG", root / "videos.json").start()
        store.CATALOG.write_text('{"videos":[]}')
        patch.dict(os.environ, {"YOU2MP3_API_KEY": "simulated-only"}).start()
        patch("durable_state.checkpoint", return_value=True).start()
        patch("lite_site.generate").start()
        patch.object(watch.shutil, "disk_usage", return_value=type("Usage", (), {"free": 20 * watch.GB})()).start()
        store.init()
        patch.object(worker, "api", return_value={}).start()

    def post(self, n=123456789):
        return dict(id=str(n), url=f"https://www.tiktok.com/@wokearewe1/video/{n}",
                    caption="My clip #truth", hashtags=["truth"], posted_at="2020-01-01T00:00:00Z")

    def page(self, posts, more=False, cursor=None):
        return dict(posts=posts, has_more=more, next_cursor=cursor)

    def test_discovery_dedup_restart_metadata(self):
        with patch.object(watch_provider.PROVIDER, "page", return_value=self.page([self.post()], True, "next")):
            watch.check()
            watch.check()
        store.init()
        with store.db() as c:
            rows = c.execute("SELECT * FROM jobs").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "My clip")
        self.assertEqual(json.loads(rows[0]["tags"]), ["truth"])
        self.assertEqual(rows[0]["source_date"], "2020-01-01T00:00:00Z")
        self.assertEqual(rows[0]["add_end_card"], 1)
        self.assertEqual(rows[0]["auto_publish"], 0)
        self.assertEqual(watch.settings()["cursor"], "next")

    def test_bad_page_retains_cursor_no_partial_queue(self):
        bad = self.post(987654321); bad.pop("posted_at")
        with patch.object(watch_provider.PROVIDER, "page", return_value=self.page([self.post(), bad], True, "next")):
            watch.check()
        self.assertIsNone(watch.settings()["cursor"])
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 0)

    def test_history_and_unsupported(self):
        watch.check()
        self.assertEqual(watch.settings()["error"], "watching not available yet")
        with patch.object(watch_provider.PROVIDER, "page", return_value=self.page([self.post()])):
            watch.check(older=True)
        self.assertTrue(watch.settings()["history_done"])
        with self.assertRaises(ValueError): watch.request(older=True)

    def test_disk_limit_and_persistent_hourly_limit(self):
        with patch.object(watch.shutil, "disk_usage", return_value=type("Usage", (), {"free": watch.GB})()):
            with self.assertRaises(watch.Paused): watch.admit({"id": "one"})
        watch.configure({"hourly_limit": 1})
        watch.admit({"id": "one"})
        store.init()
        watch.admit({"id": "one"})
        with self.assertRaises(watch.Paused): watch.admit({"id": "two"})

    def test_auto_publish_and_end_card_payload(self):
        jid = store.enqueue(self.post()["url"], title="Clip", automatic=True, auto_publish=True,
                            add_end_card=False, source_date="2020-01-01T00:00:00Z")["id"]
        calls = []
        def api(path, payload=None, *args, **kwargs):
            calls.append((path, payload))
            if path == "/videos": return {"id": 1}
            if path == "/videos/1": return {"status": "done", "outputFilename": "clip.mp4"}
            return {}
        def copy(job, filename):
            with store.db() as c:
                c.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?)", (job["source_key"], "verified", None, 1, "hash", 1, 1))
        with patch.object(worker, "api", side_effect=api), patch.object(worker, "copy_media", side_effect=copy), \
             patch.object(watch_provider.PROVIDER, "end_card_payload", side_effect=lambda p, enabled: dict(p, simulated_end_card=enabled)):
            worker.process(store.get_job(jid))
        self.assertEqual(store.get_job(jid)["status"], "published")
        self.assertEqual(store.load_catalog()[0]["videos"][0]["added"], "2020-01-01T00:00:00Z")
        self.assertFalse(json.loads(store.get_job(jid)["payload"])["simulated_end_card"])
        self.assertEqual(store.enqueue(self.post()["url"])["status"], "duplicate skipped")

    def test_review_and_unsupported_end_card(self):
        jid = store.enqueue(self.post()["url"], add_end_card=True)["id"]
        worker.process(store.get_job(jid))
        self.assertEqual(store.get_job(jid)["status"], "blocked")
        self.assertIsNone(store.get_job(jid)["payload"])

    def test_auth_error_not_unsupported(self):
        with patch.object(worker, "api", side_effect=worker.UpstreamError(401, "Connection key expired or invalid")):
            watch.check()
        self.assertEqual(watch.settings()["status"], "failed")
        self.assertIn("invalid", watch.settings()["error"])

    def test_owner_and_config_validation(self):
        with self.assertRaises(ValueError): watch.configure({"profile": "http://localhost/"})
        with self.assertRaises(ValueError): watch.configure({"min_free_gb": 0})
        watch.configure({"profile": "https://www.tiktok.com/@otherowner"})
        with self.assertRaises(ValueError): store.source(self.post()["url"])

    def test_review_first_final_output_and_idempotent_retry(self):
        jid = store.enqueue(self.post()["url"], title="Ready", add_end_card=None)["id"]
        def api(path, payload=None, *args, **kwargs):
            if path == "/videos": return {"id": 2}
            if path == "/videos/2": return {"status": "done", "outputFilename": "clip.mp4",
                                          "endCards": [{"status": "processing"}]}
            return {}
        with patch.object(worker, "api", side_effect=api) as calls, patch.object(worker, "copy_media") as copy:
            worker.process(store.get_job(jid))
            original = store.get_job(jid)
            worker.process(original)
            self.assertEqual(sum(c.args[0] == "/videos" for c in calls.call_args_list), 1)
            copy.assert_not_called()
        self.assertEqual(store.get_job(jid)["payload"], original["payload"])
        self.assertEqual(store.get_job(jid)["status"], "processing")

    def test_real_snapshot_restores_policy_cursor_and_admission(self):
        import durable_state
        # Use the real checkpoint function despite the normal fixture mock.
        patch.stopall()
        root = Path(self.temp.name)
        with patch.object(store, "STATE", root), patch.object(store, "CATALOG", root / "videos.json"), \
             patch.dict(os.environ, {"STORAGE_BACKEND": "disk", "MIRROR_DIR": ""}):
            watch.update(cursor="persisted", hourly_limit=2)
            with store.db() as c:
                c.execute("INSERT INTO import_admissions VALUES('saved',1)")
            self.assertTrue(durable_state.checkpoint(required=True))
            store.CATALOG.unlink()
            (root / "admin.sqlite").unlink()
            durable_state.restore_if_needed()
            self.assertEqual(watch.settings()["cursor"], "persisted")
            with store.db() as c:
                self.assertEqual(c.execute("SELECT job_id FROM import_admissions").fetchone()[0], "saved")

    def test_stream_guard_stops_before_writing(self):
        job = {"source_key": "tt:123456789"}
        class Response:
            headers = {}
            def iter_content(self, size): yield b"some video bytes"
            def close(self): pass
        with patch.object(worker, "api", return_value=Response()), \
             patch.object(watch, "disk_guard", side_effect=[20 * watch.GB, watch.Paused("disk full")]):
            with self.assertRaises(watch.Paused): worker.copy_media(job, "clip.mp4")
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM media").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
