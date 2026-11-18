from test_watcher import WatchTests
from unittest.mock import patch
import admin_store as store
import tiktok_watch as watch
import watch_provider
import media_worker as worker


class ContractTests(WatchTests):
    def wire(self, posts, after=0, more=False):
        return {"posts":[dict(cursor=after+i+1,postId=p["id"],url=p["url"],
                             caption=p["caption"],hashtags=p["hashtags"],postedAt=p["posted_at"],
                             thumbnail=None,firstSeenAt="2026-11-18T00:00:00Z") for i,p in enumerate(posts)],
                "nextCursor":after+len(posts),"hasMore":more,
                "status":{"profile":"https://www.tiktok.com/@wokearewe1","enabled":True,
                          "postCount":313,"completeness":"best-effort","warning":"Observed posts only"}}

    def test_live_wire_format_and_end_card(self):
        with patch.object(worker,"api",return_value=self.wire([self.post()])) as api:
            page=watch_provider.PROVIDER.page(watch.settings()["profile"],None,25)
        api.assert_called_once_with("/tiktok-watch/posts?after=0&limit=25")
        self.assertEqual(page["inventory_count"],313)
        self.assertEqual(page["posts"][0]["posted_at"],"2020-01-01T00:00:00Z")
        for enabled in (True,False):
            self.assertEqual(watch_provider.PROVIDER.end_card_payload({},enabled),{"addEndCard":enabled})
        self.assertFalse(watch_provider.PROVIDER.final_ready({"endCards":[{"status":"processing"}]}))
        self.assertTrue(watch_provider.PROVIDER.final_ready({"endCards":[{"status":"added"}]}))

    def test_existing_history_never_auto_imported(self):
        watch.update(watching_since=1700000000)
        with patch.object(worker,"api",return_value=self.wire([self.post()],more=True)):
            watch.check()
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0],0)
        self.assertEqual(watch.settings()["inventory_count"],313)
        self.assertEqual(watch.settings()["cursor"],"1")
        with patch.object(worker,"api",return_value=self.wire([self.post()])):
            watch.check(older=True)
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0],1)

    def test_new_posts_and_null_metadata(self):
        watch.update(watching_since=1700000000)
        new=self.post();new["posted_at"]="2026-11-18T00:00:00Z"
        unknown=self.post(987654321);unknown["posted_at"]=None;unknown["caption"]=None
        with patch.object(worker,"api",return_value=self.wire([new,unknown])):
            watch.check()
        with store.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM jobs").fetchone()[0],1)
        store.init()
        self.assertEqual(watch.settings()["watching_since"],1700000000)

    def test_wrong_profile_and_non_monotonic_cursor(self):
        value=self.wire([self.post()])
        value["posts"][0]["cursor"]=0
        with patch.object(worker,"api",return_value=value):
            with self.assertRaises(ValueError): watch_provider.PROVIDER.page(watch.settings()["profile"],None,25)


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(ContractTests(name) for name in (
        "test_live_wire_format_and_end_card", "test_existing_history_never_auto_imported",
        "test_new_posts_and_null_metadata", "test_wrong_profile_and_non_monotonic_cursor"))
