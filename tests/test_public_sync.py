import sys, os, json, base64, tempfile, time
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import admin_store as store
import public_catalog as public
import public_sync as sync


class SyncTests(TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(store,"STATE",root/"data").start()
        patch.object(store,"ROOT",root).start()
        patch.object(store,"CATALOG",root/"data/videos.json").start()
        store.STATE.mkdir()
        self.seed={"videos":[{"id":"public-id","title":"Original","youtube_id":"abcdefghijk","notes":"PRIVATE","source_url":"PRIVATE","backup_url":"PRIVATE","queue":{"private":True}}]}
        (root/"videos.json").write_text(json.dumps(self.seed))
        store.CATALOG.write_text(json.dumps(self.seed))
        patch.dict(os.environ,{"GITHUB_SYNC_REPO":"fixture/repo","GITHUB_SYNC_BRANCH":"throwaway","GITHUB_SYNC_TOKEN":"test-fixture-only","PI_PUBLIC_URL":""}).start()
        self.calls=[]
        self.remote={"sha":"first","content":base64.b64encode(json.dumps(self.seed).encode()).decode()}
        def request(method,path,token,**kwargs):
            self.calls.append((method,path,kwargs))
            if method=="GET":return self.remote
            self.remote={"sha":"second","content":kwargs["json"]["content"]}
            return {"content":{"sha":"second"}}
        patch.object(sync,"request",request).start()

    def edit(self,title):
        data=json.loads(store.CATALOG.read_text());data["videos"][0]["title"]=title
        store.CATALOG.write_text(json.dumps(data))

    def test_no_token_no_network(self):
        os.environ["GITHUB_SYNC_TOKEN"]=""
        self.edit("Saved offline");sync.tick()
        self.assertEqual(sync.status()["status"],"not set up")
        self.assertEqual(self.calls,[])
        self.assertEqual(json.loads(store.CATALOG.read_text())["videos"][0]["title"],"Saved offline")

    def test_debounce_sync_and_allowlist(self):
        self.edit("A");sync.tick();self.edit("B");sync.tick()
        self.assertEqual(self.calls,[])
        sync.now();sync.tick()
        self.assertEqual([c[0] for c in self.calls],["GET","PUT"])
        exported=json.loads(base64.b64decode(self.calls[-1][2]["json"]["content"]))
        self.assertEqual(exported["videos"][0]["title"],"B")
        self.assertNotIn("PRIVATE",json.dumps(exported))
        self.assertEqual(sync.status()["status"],"synced")
        sync.tick();self.assertEqual(len(self.calls),2)
        self.assertTrue(sync.state()["last_success"])

    def test_remote_changes_stop_overwrite(self):
        sync.now();sync.tick()
        self.remote["sha"]="somebody-else"
        self.remote["content"]=base64.b64encode(json.dumps({"videos":[{"title":"Newer GitHub edit"}]}).encode()).decode()
        self.edit("Local edit");sync.now();sync.tick()
        self.assertEqual(self.calls[-1][0],"GET")
        self.assertIn("Conflict",sync.status()["reason"])

    def test_retry_after_failure(self):
        with patch.object(sync,"request",side_effect=ValueError("GitHub unreachable")):
            sync.now();sync.tick()
        self.assertEqual(sync.status()["status"],"failed")
        self.assertGreater(sync.state()["next_try"],time.time())
        sync.tick();self.assertEqual(self.calls,[])
        sync.now();sync.tick()
        self.assertEqual(sync.status()["status"],"synced")

    def test_config_change_does_not_reuse_baseline(self):
        sync.now();sync.tick()
        os.environ["GITHUB_SYNC_BRANCH"]="other"
        self.remote["content"]=base64.b64encode(b'{"videos":[{"title":"Other branch"}]}').decode()
        sync.now();sync.tick()
        self.assertEqual(sync.status()["status"],"failed")
        self.assertIn("Conflict",sync.status()["reason"])

    def test_first_sync_notices_changed_remote_origin(self):
        changed=dict(self.seed,public_pi_url="https://newer.example")
        self.remote["content"]=base64.b64encode(json.dumps(changed).encode()).decode()
        sync.now();sync.tick()
        self.assertEqual([c[0] for c in self.calls],["GET"])
        self.assertIn("Conflict",sync.status()["reason"])

    def test_origin_is_public_only(self):
        os.environ["PI_PUBLIC_URL"]="https://backup.example"
        self.assertEqual(public.export(self.seed)["public_pi_url"],"https://backup.example")
        os.environ["PI_PUBLIC_URL"]="https://user:secret@backup.example"
        with self.assertRaises(ValueError):public.export(self.seed)

    def test_pending_survives_new_process(self):
        import subprocess
        self.edit("Persisted pending edit");sync.tick()
        module_dir=Path(sync.__file__).parent
        result=subprocess.run([sys.executable,"-c",
            "import public_sync;print(public_sync.status()['status'])"],
            cwd=module_dir,env=dict(os.environ,DATA_DIR=str(store.STATE)),capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),"pending")

    def test_public_backup_endpoint_only_published_and_cors(self):
        import server, threading, urllib.request
        from http.server import ThreadingHTTPServer
        os.environ["PI_PUBLIC_URL"]="https://backup.example"
        os.environ["PUBLIC_SITE_ORIGIN"]="https://wokearewe.com"
        store.init()
        with store.db() as db:
            db.execute("INSERT INTO media(source_key,object_key,poster_key,size,sha256,duration,created,play_status,check_status) VALUES(?,?,?,?,?,?,?,'ready','ok')",("yt:abcdefghijk","private-object",None,1,"sha",1,0))
            db.execute("INSERT INTO media(source_key,object_key,poster_key,size,sha256,duration,created,play_status,check_status) VALUES(?,?,?,?,?,?,?,'ready','ok')",("yt:xxxxxxxxxxx","unpublished-private-object",None,1,"other",1,0))
        http=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            req=urllib.request.Request(f"http://127.0.0.1:{http.server_port}/api/public/backups",headers={"Origin":"https://wokearewe.com"})
            with urllib.request.urlopen(req) as response:
                self.assertEqual(response.headers["Access-Control-Allow-Origin"],"https://wokearewe.com")
                data=json.load(response)
            self.assertEqual(data,{"backups":{"public-id":"https://backup.example/media/yt-abcdefghijk"},"posters":{}})
        finally:
            http.shutdown();http.server_close()


if __name__=="__main__":main()
