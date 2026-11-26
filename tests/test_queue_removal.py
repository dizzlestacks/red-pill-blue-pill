"""Removing queue items, plain card messages, YouTube imports and YouTube watching."""
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
import media_worker as worker
import queue_actions
import youtube_watch

TT = "https://www.tiktok.com/@wokearewe1/video/123456789"
YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class QueueRemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.temp.name)
        patch.object(store, "STATE", root).start()
        patch.object(store, "CATALOG", root / "videos.json").start()
        store.CATALOG.write_text('{"videos":[]}')
        patch.dict(os.environ, {"YOU2MP3_API_KEY": "simulated-only", "STORAGE_BACKEND": "disk"}).start()
        patch("durable_state.checkpoint", return_value=True).start()
        patch("lite_site.generate").start()
        patch("nas_library.wake").start()
        patch.object(watch.shutil, "disk_usage", return_value=type("Usage", (), {"free": 20 * watch.GB})()).start()
        store.init()
        self.calls = []

    def job(self, jid):
        return store.get_job(jid)

    def saved_copy(self, key):
        client = worker.storage_client()
        path = client.path("videos/test.mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"video")
        with store.db() as c:
            c.execute("INSERT INTO media(source_key,object_key,poster_key,size,sha256,duration,created) VALUES(?,?,?,?,?,?,?)",
                      (key, "videos/test.mp4", None, 5, "x", 1, 1))
        return path

    def test_remove_unpublished_deletes_file_cancels_and_blocks_watchers(self):
        jid = store.enqueue(TT, automatic=True)["id"]
        store.update_job(jid, status="processing", upstream_id=42)
        path = self.saved_copy("tt:123456789")
        queue_actions.remove(jid)
        with self.assertRaises(LookupError):
            store.get_job(jid)
        self.assertFalse(path.exists())
        with store.db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM media WHERE source_key='tt:123456789'").fetchone())
            self.assertEqual(c.execute("SELECT path FROM pending_cancels").fetchone()[0], "/videos/42/cancel")
        # The watcher can't bring it back...
        self.assertEqual(store.enqueue(TT, automatic=True)["status"], "removed by owner")
        # ...but adding it by hand can.
        self.assertEqual(store.enqueue(TT)["status"], "queued")

    def test_cancel_runs_later_and_tolerates_uncertain_upstream(self):
        jid = store.enqueue(TT)["id"]
        store.update_job(jid, status="blocked", upstream_id=7, reason="anything")
        queue_actions.remove(jid)
        with patch.object(worker, "api", side_effect=worker.UpstreamError(0, "offline")):
            queue_actions.run_cancels()
        with store.db() as c:
            row = c.execute("SELECT * FROM pending_cancels").fetchone()
            self.assertEqual(row["tries"], 1)
            c.execute("UPDATE pending_cancels SET next_try=0")
        with patch.object(worker, "api", side_effect=worker.UpstreamError(409, "conflict")):
            queue_actions.run_cancels()
        with store.db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM pending_cancels").fetchone())

    def test_removing_published_keeps_hub_copy(self):
        jid = store.enqueue(TT)["id"]
        path = self.saved_copy("tt:123456789")
        store.update_job(jid, status="imported", title="Clip")
        with store.db() as c:
            c.execute("UPDATE media SET play_status='ready', play_key=object_key, check_status='ok'")
        store.publish(jid)
        keys, _ = queue_actions.site_keys()
        self.assertIn("stays in your hub", queue_actions.describe_removal(self.job(jid), True, {"x": 1}))
        queue_actions.remove(jid)
        self.assertTrue(path.exists())
        self.assertEqual(len(store.load_catalog()[0]["videos"]), 1)

    def test_remove_all_need_help(self):
        a = store.enqueue(TT)["id"]
        b = store.enqueue("https://www.tiktok.com/@wokearewe1/video/123456780")["id"]
        c = store.enqueue("https://www.tiktok.com/@wokearewe1/video/123456781")["id"]
        store.update_job(a, status="failed", reason="x")
        store.update_job(b, status="blocked", reason="y")
        self.assertEqual(queue_actions.remove_all("needhelp")["removed"], 2)
        self.assertEqual(self.job(c)["status"], "queued")
        with self.assertRaises(ValueError):
            queue_actions.remove_all("active")

    def test_cards_are_plain_and_consistent(self):
        jid = store.enqueue(YT)["id"]
        store.update_job(jid, status="blocked", reason="This connection is TikTok-only and cannot fetch YouTube.")
        card = queue_actions.card(self.job(jid), None, False, False)
        self.assertEqual(card["label"], "Needs setup")
        self.assertIn("YouTube copies are not switched on for this connection yet", card["line"])
        self.assertIn("Not downloaded yet", card["line"])
        self.assertEqual(card["actions"], ["addhub", "retry", "remove"])
        self.assertTrue(card["thumbnail"].startswith("https://i.ytimg.com/vi/dQw4w9WgXcQ/"))
        store.update_job(jid, status="blocked", reason="Upstream request outcome is uncertain or conflicts; same request retained")
        card = queue_actions.card(self.job(jid), None, False, False)
        self.assertNotIn("Upstream", card["line"])
        self.assertIn("Retry", card["line"])
        for status in ("queued", "processing", "failed", "blocked", "imported", "published", "problem"):
            store.update_job(jid, status=status, reason="")
            self.assertIn("remove", queue_actions.card(self.job(jid), None, False, False)["actions"])

    def test_uncertain_is_checked_then_offers_fresh_retry(self):
        jid = store.enqueue(TT)["id"]
        with patch.object(worker, "api", side_effect=worker.UpstreamError(409, "x")), \
             patch.object(worker, "storage_status", return_value=(True, "")):
            for _ in range(3):
                worker.process(self.job(jid))
                self.assertEqual(self.job(jid)["reason"], queue_actions.CHECKING)
            worker.process(self.job(jid))
        self.assertEqual(self.job(jid)["status"], "failed")
        old = self.job(jid)["idempotency"]
        queue_actions.retry(self.job(jid))
        self.assertNotEqual(self.job(jid)["idempotency"], old)
        self.assertEqual(self.job(jid)["status"], "queued")

    def test_manual_youtube_uses_youtube_import_api(self):
        jid = store.enqueue(YT)["id"]
        calls = []

        def api(path, payload=None, *a, **k):
            calls.append((path, payload))
            if path == "/youtube-watch/imports":
                return {"id": 9, "created": True, "duplicate": False, "import": {"id": 9, "status": "pending"}}
            if path == "/youtube-watch/imports/9":
                return {"import": {"id": 9, "status": "done", "transferReady": True,
                                   "output": {"fileUrl": "/api/v1/files/abc.mp4"}}}
            raise AssertionError(path)
        saved = []
        with patch.object(worker, "api", side_effect=api), patch.object(worker, "storage_status", return_value=(True, "")), \
             patch.object(worker, "save_copy", side_effect=lambda job, filename=None, path=None: saved.append(path)):
            worker.process(self.job(jid))
        self.assertEqual(calls[0][0], "/youtube-watch/imports")
        self.assertEqual(calls[0][1]["mode"], "manual")
        self.assertIs(calls[0][1]["addEndCard"], False)
        self.assertEqual(saved, ["/files/abc.mp4"])
        self.assertEqual(self.job(jid)["upstream_kind"], "youtube-import")

    def test_youtube_not_switched_on_and_add_to_hub(self):
        jid = store.enqueue(YT)["id"]
        with patch.object(worker, "api", side_effect=worker.UpstreamError(403, "x")), \
             patch.object(worker, "storage_status", return_value=(True, "")):
            worker.process(self.job(jid))
        self.assertEqual(self.job(jid)["reason"], queue_actions.YT_OFF)
        queue_actions.add_to_hub(self.job(jid))
        videos = store.load_catalog()[0]["videos"]
        self.assertEqual(videos[0]["youtube_id"], "dQw4w9WgXcQ")
        keys, ids = queue_actions.site_keys()
        card = queue_actions.card(self.job(jid), None, "yt:dQw4w9WgXcQ" in keys, True)
        self.assertNotIn("addhub", card["actions"])
        self.assertIn("plays from YouTube", card["line"])

    def test_file_links_stay_on_you2mp3(self):
        self.assertEqual(worker.file_path("https://you2mp3.music/api/v1/files/a.mp4"), "/files/a.mp4")
        for bad in ("https://evil.example/api/v1/files/a.mp4", "/api/v1/files/../x", "/api/v1/other"):
            with self.assertRaises(ValueError):
                worker.file_path(bad)

    def test_youtube_watch_baseline_add_decline_and_auto(self):
        def video(n, src="s1"):
            return {"videoId": f"abcdefghij{n}", "title": f"Video {n}", "channel": "Chan", "sources": [{"id": src}],
                    "publishedAt": "2026-11-01T00:00:00Z", "cursor": n}
        pages = [{"videos": [video(0)], "hasMore": False, "nextCursor": 1, "sources": [{"id": "s1", "title": "Chan"}]},
                 {"videos": [video(1), video(2)], "hasMore": False, "nextCursor": 3}]

        def api(path, payload=None, *a, **k):
            if path == "/youtube-watch":
                return {"status": "ok", "sources": [{"id": "s1", "title": "Chan"}]}
            if path.startswith("/youtube-watch/videos"):
                return pages.pop(0)
            raise AssertionError(path)
        with patch.object(worker, "api", side_effect=api):
            youtube_watch.check()
            self.assertEqual(youtube_watch.status("earlier")["videos"][0]["videoId"], "abcdefghij0")
            youtube_watch.set_auto("s1", True)
            youtube_watch.update(next_check=0)
            youtube_watch.check()
        # Auto-added (not on the baseline pass): in the hub and a watched copy is requested.
        added = youtube_watch.status("added")["videos"]
        self.assertEqual({v["videoId"] for v in added}, {"abcdefghij1", "abcdefghij2"})
        with store.db() as c:
            meta = json.loads(c.execute("SELECT metadata FROM jobs WHERE source_key='yt:abcdefghij1'").fetchone()[0])
            job = dict(c.execute("SELECT * FROM jobs WHERE source_key='yt:abcdefghij1'").fetchone())
        self.assertTrue(meta["youtube_watch"])
        self.assertEqual(worker.youtube_payload(job)["mode"], "watched")
        youtube_watch.decline("abcdefghij0")
        self.assertEqual(youtube_watch.status("declined")["videos"][0]["videoId"], "abcdefghij0")


if __name__ == "__main__":
    unittest.main()
