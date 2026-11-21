from test_watcher import WatchTests
from unittest.mock import patch
import json
import time
import hashlib
import os
import back_catalog as back
import admin_store as store
import tiktok_watch as watch
import disk_storage


class BackCatalogueTests(WatchTests):
    def test_run_restart_pause_duplicates_missing_date_and_disk(self):
        posts = [self.post(123456789), self.post(123456790)]
        posts[1]["posted_at"] = None
        page = self.page(posts, False, "2")
        with patch.object(back.watch_provider.PROVIDER, "page", return_value=page):
            back.action("start", {"add_end_card": False})
            back.tick()
            self.assertEqual(back.status()["run"]["scanned"], 2)
            store.init()  # same persistent data, as on process restart
            self.assertEqual(back.status()["run"]["scanned"], 2)
            back.action("pause", {})
            with store.db() as c:
                jobs = [dict(r) for r in c.execute("SELECT * FROM jobs")]
            self.assertFalse(back.allowed(jobs[0]))
            self.assertIsNone(jobs[1]["source_date"])
            self.assertFalse(jobs[1]["add_end_card"])
            back.action("resume", {})
            with patch.object(watch, "disk_guard", side_effect=watch.Paused("Low disk")):
                back.tick()
            self.assertEqual(back.current()["state"], "disk paused")
            r=back.current();r["next_check"]=0;back.save(r)
            back.tick()
            self.assertEqual(back.current()["state"], "running")
            for job in jobs:
                store.update_job(job["id"], status="imported")
            r=back.current();r["next_check"]=0;back.save(r);back.tick()
            self.assertEqual(back.current()["state"], "done")
            back.action("start", {"add_end_card": True});back.tick()
            with store.db() as c:
                self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0], 2)

    def test_match_publish_undo_and_shared_switch(self):
        cat,rev=store.load_catalog()
        cat["videos"]=[dict(id="existing",title="The truth about our world",youtube_id="abcdefghijk")]
        store.save_catalog(cat,rev)
        j=store.enqueue(self.post()["url"],title="The truth about our world!",automatic=True)
        with store.db() as c:
            c.execute("INSERT INTO media VALUES(?,?,?,?,?,?,?)",("tt:123456789","test.mp4",None,1,"x",1,time.time()))
        store.update_job(j["id"],status="imported")
        watch.configure({"auto_publish":True})
        self.assertTrue(store.get_job(j["id"])["auto_publish"])
        store.publish(j["id"])
        self.assertEqual(len(store.load_catalog()[0]["videos"]),1)
        self.assertEqual(store.hydrated()[2],1)
        back.undo(j["id"])
        self.assertEqual(store.hydrated()[2],0)
        self.assertFalse(store.get_job(j["id"])["automatic"])
        store.publish(j["id"])
        self.assertEqual(len(store.load_catalog()[0]["videos"]),2)
        self.assertTrue(store.load_catalog()[0]["videos"][1]["added"])

    def test_retry_and_mirror_outage(self):
        with patch.object(back.watch_provider.PROVIDER,"page",return_value=self.page([self.post()],False,"1")):
            back.action("start",{"add_end_card":True});back.tick()
        with store.db() as c:
            c.execute("UPDATE jobs SET status='failed',updated=0")
        r=back.current();r["next_check"]=0;back.save(r);back.tick()
        with store.db() as c:
            self.assertEqual(c.execute("SELECT status FROM jobs").fetchone()[0],"queued")
        source=store.STATE/"sample";source.write_bytes(b"sample")
        with patch.dict(os.environ,{"MIRROR_DIR":str(store.STATE/"missing")}):
            disk_storage.DiskStorage().upload_from_filename("sample.mp4",source)
            self.assertTrue((store.STATE/"objects/sample.mp4").exists())
            disk_storage.reconcile_mirror()
            self.assertEqual(disk_storage.mirror_status()["state"],"unavailable")
