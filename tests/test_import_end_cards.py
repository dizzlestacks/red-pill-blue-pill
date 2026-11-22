from test_watcher import WatchTests
import json
import admin_store as store
import tiktok_watch as watch
import back_catalog as back


class EndCardPolicyTests(WatchTests):
    def test_policy_migrates_only_unsent_requests(self):
        a=store.enqueue(self.post()["url"],add_end_card=True)["id"]
        b=store.enqueue(self.post(987654321)["url"],add_end_card=None)["id"]
        store.update_job(a,add_end_card=True)
        store.update_job(b,add_end_card=True,payload=json.dumps({"addEndCard":True}),upstream_id=123)
        watch.update(add_end_card=True)
        store.init()
        self.assertFalse(watch.settings()["add_end_card"])
        self.assertEqual(store.get_job(a)["add_end_card"],0)
        self.assertEqual(store.get_job(b)["add_end_card"],1)
        self.assertTrue(json.loads(store.get_job(b)["payload"])["addEndCard"])
        watch.configure({"add_end_card":True})
        self.assertFalse(watch.settings()["add_end_card"])
        back.action("start",{"add_end_card":True})
        self.assertFalse(back.current()["add_end_card"])
