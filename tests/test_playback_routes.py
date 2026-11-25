"""End-to-end check of saved-video playback through the production HTTP routes.

Simulates saved TikTok downloads in formats iPhones refuse (H.264 labelled
level 6.2 with MP3 audio, HEVC with the index at the end), then runs the real
server and confirms: a browser-friendly version is made automatically while
the original is kept; the admin preview and the public /media player route
answer range requests with 206, Content-Range, Accept-Ranges, video/mp4 and the
right length; posters are served; preview problems report a plain reason.
"""
import hashlib
import http.client
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import playback  # noqa: E402


def make_video(path, codec):
    common = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=576x1024:rate=25:duration=3",
              "-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
    if codec == "h264-level62":
        args = ["-c:v", "libx264", "-profile:v", "high", "-level:v", "6.2", "-pix_fmt", "yuv420p",
                "-c:a", "libmp3lame", "-shortest", str(path)]
    elif codec == "hevc-index-last":
        args = ["-c:v", "libx265", "-tag:v", "hvc1", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(path)]
    else:
        args = ["-c:v", "libx264", "-profile:v", "main", "-level:v", "3.1", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-movflags", "+faststart", "-shortest", str(path)]
    subprocess.run(common + args, check=True, capture_output=True, timeout=120)


SEED = r"""
import hashlib, json, sys, time
import admin_store as store
from disk_storage import DiskStorage
store.init()
client = DiskStorage()
out = {}
for n, (name, title) in enumerate(json.loads(sys.argv[1]).items()):
    post = 7500000000000000000 + n
    job = store.enqueue(f"https://www.tiktok.com/@wokearewe1/video/{post}", title=title, tags=["bible"])
    key = f"tt:{post}"
    data = open(name, "rb").read()
    digest = hashlib.sha256(data).hexdigest()
    object_key = f"wokearewe/backups/tt-{post}/{digest}.mp4"
    client.upload_from_filename(object_key, name)
    with store.db() as c:
        c.execute("INSERT INTO media(source_key,object_key,poster_key,size,sha256,duration,created) VALUES(?,?,?,?,?,?,?)",
                  (key, object_key, None, len(data), digest, 3.0, time.time()))
    store.update_job(job["id"], status="imported", reason="Verified permanent copy; ready for review")
    out[name] = {"id": job["id"], "key": key, "object_key": object_key, "sha256": digest}
print(json.dumps(out))
"""


class PlaybackRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.mkdtemp(prefix="waw-playback-test-")
        data = Path(cls.temp) / "data"
        data.mkdir()
        shutil.copyfile(ROOT / "videos.json", data / "videos.json")
        cls.files = {}
        for codec in ("h264-level62", "hevc-index-last", "compatible"):
            path = Path(cls.temp) / f"{codec}.mp4"
            make_video(path, codec)
            cls.files[str(path)] = codec
        titles = {name: "#gathering #bible #scripture" if codec == "h264-level62" else f"Test {codec}"
                  for name, codec in cls.files.items()}
        cls.password = secrets.token_urlsafe(24)  # temporary test value only, never written to disk
        cls.env = dict(os.environ, DATA_DIR=str(data), STORAGE_BACKEND="disk", ADMIN_PASSWORD=cls.password,
                       BIND_HOST="127.0.0.1", PYTHONDONTWRITEBYTECODE="1",
                       MIN_FREE_GB="1")
        for k in ("YOU2MP3_API_KEY", "MIRROR_DIR", "GITHUB_TOKEN", "PUBLIC_SYNC_TOKEN"):
            cls.env.pop(k, None)
        seeded = subprocess.run([sys.executable, "-c", SEED, json.dumps(titles)], cwd=ROOT, env=cls.env,
                                capture_output=True, text=True, check=True, timeout=120)
        cls.seed = json.loads(seeded.stdout.strip().splitlines()[-1])
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        cls.env["PORT"] = str(cls.port)
        cls.server = subprocess.Popen([sys.executable, "server.py"], cwd=ROOT, env=cls.env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if cls.request("GET", "/healthz")[0] in (200, 503):
                    break
            except OSError:
                time.sleep(0.2)
        status, headers, body = cls.request("POST", "/api/admin/login", {"password": cls.password})
        assert status == 200, body
        cls.cookie = headers["set-cookie"].split(";")[0]
        cls.csrf = json.loads(body)["csrf"]

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=20)
        shutil.rmtree(cls.temp, ignore_errors=True)

    @classmethod
    def request(cls, method, path, data=None, headers=None, cookie=True):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=60)
        h = dict(headers or {})
        if cookie and getattr(cls, "cookie", None):
            h["Cookie"] = cls.cookie
            if method == "POST":
                h["X-CSRF-Token"] = cls.csrf
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        out = r.read()
        result = r.status, {k.lower(): v for k, v in r.getheaders()}, out
        conn.close()
        return result

    def wait_ready(self, jid, timeout=240):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _, body = self.request("GET", f"/api/admin/media/{jid}/status")
            self.assertEqual(status, 200)
            state = json.loads(body)
            if state["state"] != "processing" and state["check"]["state"] not in (None, "checking"):
                return state
            time.sleep(1)
        self.fail("Preview preparation did not finish")

    def assert_range_ok(self, path, cookie=True):
        status, h, body = self.request("GET", path, headers={"Range": "bytes=0-1"}, cookie=cookie)
        self.assertEqual(status, 206, path)
        self.assertEqual(h["content-type"], "video/mp4")
        self.assertEqual(h["accept-ranges"], "bytes")
        self.assertEqual(h["content-length"], "2")
        total = int(h["content-range"].split("/")[1])
        self.assertEqual(h["content-range"], f"bytes 0-1/{total}")
        self.assertEqual(body, b"\x00\x00")
        status, h, tail = self.request("GET", path, headers={"Range": f"bytes={total - 100}-"}, cookie=cookie)
        self.assertEqual((status, h["content-range"], len(tail)), (206, f"bytes {total - 100}-{total - 1}/{total}", 100))
        status, h, full = self.request("GET", path, cookie=cookie)
        self.assertEqual((status, int(h["content-length"]), len(full)), (200, total, total))
        status, h, _ = self.request("GET", path, headers={"Range": f"bytes={total}-"}, cookie=cookie)
        self.assertEqual((status, h["content-range"]), (416, f"bytes */{total}"))
        return full

    def check_browser_friendly(self, data):
        file = Path(self.temp) / f"served-{hashlib.sha256(data).hexdigest()[:8]}.mp4"
        file.write_bytes(data)
        plan = playback.assess(file)
        self.assertTrue(plan["playable"], plan)
        self.assertLessEqual(plan["level"], playback.MAX_LEVEL)
        self.assertEqual(plan["codec"], "h264")
        self.assertEqual(plan["audio_codec"], "aac")
        self.assertTrue(playback.index_first(file))

    def test_incompatible_downloads_get_playable_preview_and_public_playback(self):
        _, _, body = self.request("GET", "/api/admin/queue")
        items = {i["id"]: i for i in json.loads(body)["items"] if "preview_url" in i}
        for name, codec in self.files.items():
            with self.subTest(codec=codec):
                seed = self.seed[name]
                state = self.wait_ready(seed["id"])
                self.assertEqual(state["state"], "ready", state)
                self.assertEqual(state["check"]["state"], "ok", state)
                self.assertTrue(state["check"]["reason"].startswith("Checked: picture (h"), state)
                self.assertTrue(state["poster"])
                preview = items[seed["id"]]["preview_url"]
                self.assertEqual(preview, f"/api/admin/media/{seed['id']}")
                # Signed-out visitors cannot fetch unpublished previews.
                self.assertEqual(self.request("GET", preview, headers={"Range": "bytes=0-1"}, cookie=False)[0], 401)
                served = self.assert_range_ok(preview)
                self.check_browser_friendly(served)
                original = (Path(self.env["DATA_DIR"]) / "objects" / seed["object_key"]).read_bytes()
                self.assertEqual(hashlib.sha256(original).hexdigest(), seed["sha256"], "original must be kept")
                if codec == "compatible":
                    self.assertEqual(served, original, "an already compatible file is served as-is")
                else:
                    self.assertNotEqual(served, original)
                status, h, poster = self.request("GET", preview + "/poster")
                self.assertEqual((status, h["content-type"]), (200, "image/jpeg"))
                self.assertTrue(poster.startswith(b"\xff\xd8"))
                # Publish, then fetch through the public player's route without signing in.
                status, _, body = self.request("POST", f"/api/admin/queue/{seed['id']}/publish", {})
                self.assertEqual(status, 200, body)
                cat = json.loads(self.request("GET", "/videos.json", cookie=False)[2])
                entry = next(v for v in cat["videos"] if v.get("backup_url") == "/media/" + seed["key"].replace(":", "-"))
                self.assertEqual(entry["poster_url"], entry["backup_url"] + "/poster")
                public = self.assert_range_ok(entry["backup_url"], cookie=False)
                self.assertEqual(public, served)
                self.assertEqual(self.request("GET", entry["poster_url"], cookie=False)[0], 200)
                status, h, download = self.request("GET", entry["backup_url"] + "/download", cookie=False)
                self.assertEqual((status, hashlib.sha256(download).hexdigest()), (200, seed["sha256"]),
                                 "downloads give the untouched original")

    def test_hashtag_only_title_is_untitled(self):
        name = next(n for n, c in self.files.items() if c == "h264-level62")
        _, _, body = self.request("GET", "/api/admin/queue")
        item = next(i for i in json.loads(body)["items"] if i["id"] == self.seed[name]["id"])
        self.assertRegex(item["title"], r"^Untitled \d{4}-\d{2}-\d{2}$")
        self.assertTrue(item["needs_title"])
        self.assertEqual(item["tags"], ["bible"])

    def test_missing_file_reports_plain_reason_and_retry(self):
        name = next(n for n, c in self.files.items() if c == "compatible")
        seed = self.seed[name]
        self.wait_ready(seed["id"])
        objects = Path(self.env["DATA_DIR"]) / "objects"
        hidden = Path(self.temp) / "hidden"
        shutil.move(objects / seed["object_key"], hidden)
        try:
            status, _, body = self.request("POST", f"/api/admin/queue/{seed['id']}/preview", {})
            self.assertEqual(status, 200, body)
            state = self.wait_ready(seed["id"])
            self.assertEqual(state["state"], "missing")
            self.assertEqual(state["check"]["state"], "problem")
            self.assertIn("missing", state["reason"])
            self.assertEqual(self.request("GET", f"/api/admin/media/{seed['id']}", headers={"Range": "bytes=0-1"})[0], 404)
        finally:
            shutil.move(hidden, objects / seed["object_key"])
        self.request("POST", f"/api/admin/queue/{seed['id']}/preview", {})
        self.assertEqual(self.wait_ready(seed["id"])["state"], "ready")


class CaptionTitleTests(unittest.TestCase):
    def setUp(self):
        # normalize() reads the watched profile owner from the private database.
        from test_watcher import WatchTests
        WatchTests.setUp(self)

    def test_hashtag_only_caption(self):
        import tiktok_watch
        post = dict(id="7500000000000000099", url="https://www.tiktok.com/@wokearewe1/video/7500000000000000099",
                    caption="#gathering #childrenofisreal #ruth #bible #scripture", hashtags=[],
                    posted_at="2026-11-20T10:00:00Z")
        result = tiktok_watch.normalize(post, "https://www.tiktok.com/@wokearewe1")
        self.assertEqual(result["title"], "Untitled 2026-11-20")
        self.assertEqual(result["tags"], ["gathering", "childrenofisreal", "ruth", "bible", "scripture"])
        post["caption"] = "Ruth and Naomi #bible"
        self.assertEqual(tiktok_watch.normalize(post, "https://www.tiktok.com/@wokearewe1")["title"], "Ruth and Naomi")


if __name__ == "__main__":
    unittest.main()
