from test_watcher import WatchTests
import admin_store as store


class OverviewTests(WatchTests):
    def test_counts_and_needs_title(self):
        cat,rev=store.load_catalog()
        cat["videos"]=[{"id":"test","title":"","youtube_id":"abcdefghijk"}]
        store.save_catalog(cat,rev)
        j=store.enqueue(self.post()["url"])
        store.update_job(j["id"],status="imported")
        data=store.overview(False)
        self.assertEqual(data["pi"]["state"],"error")
        self.assertEqual(data["needs_title"],1)
        self.assertEqual(data["waiting_publish"],1)
        self.assertEqual(data["queue_needs_title"],1)
        self.assertEqual(data["youtube"]["state"],"unavailable")
        self.assertTrue(store.hydrated()[0]["videos"][0]["needs_title"])
