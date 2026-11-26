"""Publish to YouTube against a simulated channel (no real Google account or network)."""
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

from test_watcher import WatchTests
import admin_store as store
import youtube_publish as yp
import media_worker as worker


class FakeYouTube:
    """Enough of Google's OAuth and the YouTube Data API v3 to exercise the Pi's uploader."""

    def __init__(self, handle="@wokearewe1"):
        self.handle = handle
        self.videos = {}  # id -> dict(title, description, tags, duration, privacy)
        self.sessions = {}  # uri -> dict(meta, size, received, video)
        self.inserts = 0
        self.insert_quota = 100
        self.calls = []
        self.force_private = False
        self.refresh_ok = True
        self.drop_sessions = False
        self.n = 0

    def add(self, title, duration=30, description="", tags=None):
        self.n += 1
        vid = f"chan{self.n:07d}"
        self.videos[vid] = dict(title=title, description=description, tags=tags or [], duration=duration, privacy="public")
        return vid

    def _r(self, status, body=None, headers=None):
        return yp.Response(status, headers or {}, json.dumps(body or {}).encode())

    def _error(self, status, reason):
        return self._r(status, {"error": {"code": status, "errors": [{"reason": reason}], "message": reason}})

    def request(self, method, url, params=None, headers=None, data=None, json_body=None, form=None):
        self.calls.append((method, url.split("?")[0]))
        if url == yp.TOKEN_URL:
            if form.get("grant_type") == "authorization_code":
                assert form["code_verifier"]
                return self._r(200, {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600,
                                     "scope": " ".join(yp.SCOPES)})
            if not self.refresh_ok:
                return self._r(400, {"error": "invalid_grant"})
            return self._r(200, {"access_token": "at-2", "expires_in": 3600})
        if url == yp.REVOKE_URL:
            return self._r(200)
        if url == yp.API + "/channels":
            return self._r(200, {"items": [{"id": "UCx", "snippet": {"title": "Woke Are We", "customUrl": self.handle},
                                            "contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]})
        if url == yp.API + "/playlistItems":
            ids = sorted(self.videos, reverse=True)
            start = int(params.get("pageToken") or 0)
            page = ids[start:start + 50]
            body = {"items": [{"contentDetails": {"videoId": i}} for i in page]}
            if start + 50 < len(ids):
                body["nextPageToken"] = str(start + 50)
            return self._r(200, body)
        if url == yp.API + "/videos":
            out = []
            for i in params["id"].split(","):
                v = self.videos[i]
                out.append({"id": i, "snippet": {"title": v["title"], "description": v["description"], "tags": v["tags"]},
                            "contentDetails": {"duration": f"PT{int(v['duration'])}S"},
                            "status": {"privacyStatus": v["privacy"], "uploadStatus": "processed"}})
            return self._r(200, {"items": out})
        if url == yp.UPLOAD_URL and method == "POST":
            if self.inserts >= self.insert_quota:
                return self._error(403, "quotaExceeded")
            self.inserts += 1
            uri = f"https://upload.example/session/{len(self.sessions) + 1}"
            self.sessions[uri] = dict(meta=json_body, size=int(headers["X-Upload-Content-Length"]), received=0, video=None)
            return self._r(200, {}, {"Location": uri})
        if url in self.sessions and method == "PUT":
            s = self.sessions[url]
            if self.drop_sessions:
                return self._r(404)
            rng = headers["Content-Range"]
            if rng.startswith("bytes */"):
                if s["video"]:
                    return self._r(200, {"id": s["video"], "status": {"privacyStatus": self.videos[s["video"]]["privacy"]}})
                return self._r(308, {}, {"Range": f"bytes=0-{s['received'] - 1}"} if s["received"] else {})
            m = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", rng)
            assert int(m[1]) == s["received"], "chunks must continue where YouTube stopped"
            s["received"] = int(m[2]) + 1
            if s["received"] < s["size"]:
                return self._r(308, {}, {"Range": f"bytes=0-{s['received'] - 1}"})
            snip = s["meta"]["snippet"]
            vid = self.add(snip["title"], 30, snip["description"], snip["tags"])
            self.videos[vid]["privacy"] = "private" if self.force_private else s["meta"]["status"]["privacyStatus"]
            s["video"] = vid
            return self._r(200, {"id": vid, "status": {"privacyStatus": self.videos[vid]["privacy"]}})
        raise AssertionError("unexpected request " + method + " " + url)


class FakeStorage:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, name):
        return self.root / name


class PublishTests(WatchTests):
    def setUp(self):
        super().setUp()
        self.yt = FakeYouTube()
        patch.object(yp, "TRANSPORT", self.yt).start()
        patch.object(yp, "CHUNK", 256 * 1024).start()
        patch.object(yp, "shorts_ready", return_value=True).start()
        patch("public_sync.now").start()
        patch.dict(os.environ, {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "simulated",
                                "PI_PUBLIC_URL": "https://harriet.example"}).start()
        self.files = Path(self.temp.name) / "objects"
        self.files.mkdir()
        patch.object(worker, "storage_client", return_value=FakeStorage(self.files)).start()

    def hub_video(self, n, title, duration=30, size=600 * 1024, youtube_id=""):
        key = f"tt:{n}"
        url = f"https://www.tiktok.com/@wokearewe1/video/{n}"
        (self.files / f"{n}.mp4").write_bytes(os.urandom(size))
        with store.db() as c:
            c.execute("""INSERT INTO media(source_key,object_key,poster_key,size,sha256,duration,created,play_status,check_status)
                         VALUES(?,?,?,?,?,?,?,'missing','ok')""", (key, f"{n}.mp4", None, size, f"sha{n}", duration, time.time()))
            c.execute("""INSERT INTO jobs(id,source_key,url,kind,video_id,title,tags,status,reason,idempotency,created,updated,metadata)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (f"job{n}", key, url, "tiktok", None, title, "[]", "published", "",
                                                                "i" + str(n), 0, 0, json.dumps({"caption": title + " #truth #woke"})))
        cat, rev = store.load_catalog()
        cat["videos"].append({"id": f"v{n}", "title": title, "url": f"/media/tt-{n}", "source_url": url, "youtube_id": youtube_id,
                              "tags": ["truth", "woke"], "added": f"2026-01-{len(cat['videos']) + 1:02d}"})
        store.save_catalog(cat, rev)
        return f"v{n}"

    def connect(self):
        start = yp.begin_connect()
        q = parse_qs(urlparse(start["url"]).query)
        self.assertEqual(q["redirect_uri"], ["https://harriet.example/admin/youtube/callback"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertIn("youtube.upload", q["scope"][0])
        yp.finish_connect(q["state"][0], "auth-code")
        yp.tick()  # first channel read

    def run_until_idle(self, limit=200):
        for _ in range(limit):
            yp.update(next_upload_at=0)
            if yp.upload_tick() in ("nothing waiting", "daily limit", "quota"):
                return

    def test_connect_is_single_use_kept_private_and_disconnects(self):
        start = yp.begin_connect()
        state = parse_qs(urlparse(start["url"]).query)["state"][0]
        with self.assertRaises(ValueError):
            yp.finish_connect("forged", "auth-code")
        yp.finish_connect(state, "auth-code")
        with self.assertRaises(ValueError):
            yp.finish_connect(state, "auth-code")  # replay
        path = yp.private_path()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("rt-1", (store.STATE / "admin.sqlite").read_bytes().decode("latin-1"))
        st = yp.status()
        self.assertTrue(st["connected"])
        self.assertEqual(st["channel"]["handle"], "@wokearewe1")
        self.assertNotIn("rt-1", json.dumps(st))
        yp.disconnect()
        self.assertNotIn("refresh_token", yp.private())
        self.assertFalse(yp.status()["connected"])

    def test_wrong_channel_never_uploads(self):
        self.yt.handle = "@someoneelse"
        self.hub_video(1000000001, "A video about something important")
        self.connect()
        yp.configure({"visibility": "public"})
        with self.assertRaises(ValueError):
            yp.start()
        self.assertEqual(self.yt.inserts, 0)

    def test_matching_duplicates_and_uploads_once(self):
        already = self.yt.add("Why the news never tells you this #truth", 31)
        linked = self.hub_video(1000000001, "Linked one", youtube_id="abcdefghijk")
        same = self.hub_video(1000000002, "Why the news never tells you this", 30)
        twins = self.hub_video(1000000003, "Morning walk thoughts about life", 40)
        self.yt.add("Morning walk thoughts about life", 40)
        self.yt.add("Morning walk thoughts about life!", 41)
        new = self.hub_video(1000000004, "Brand new upload for the channel", 20)
        self.connect()
        groups = {x["id"]: g for g, xs in yp.status()["groups"].items() for x in xs}
        self.assertEqual(groups[linked], "on_youtube")
        self.assertEqual(groups[same], "on_youtube")
        self.assertEqual(groups[twins], "check")
        self.assertEqual(groups[new], "missing")
        with self.assertRaises(ValueError):
            yp.start()  # visibility not chosen
        yp.configure({"visibility": "unlisted", "per_day": 10})
        yp.start()
        self.assertEqual(next(v for v in store.load_catalog()[0]["videos"] if v["id"] == same)["youtube_id"], already)
        self.run_until_idle()
        self.assertEqual(self.yt.inserts, 1)  # only the new one; the ambiguous one waits for the owner
        up = self.yt.videos[next(v["youtube_id"] for v in store.load_catalog()[0]["videos"] if v["id"] == new)]
        self.assertEqual(up["title"], "Brand new upload for the channel")
        self.assertIn("wokearewe-tt-1000000004", up["tags"])
        self.assertIn("truth", up["tags"])
        self.assertIn("wokearewe.com", up["description"])
        self.assertIn("#Shorts", up["description"])
        meta = next(iter(self.yt.sessions.values()))["meta"]
        self.assertFalse(meta["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(meta["status"]["privacyStatus"], "unlisted")
        # Owner picks the right one of the two lookalikes; nothing more is uploaded.
        yp.correct(twins, "match", sorted(self.yt.videos)[1])
        yp.refresh_items()
        self.run_until_idle()
        self.assertEqual(self.yt.inserts, 1)
        # Lost local state (e.g. a restored backup): the marker on the channel prevents a second upload.
        yp.scan()
        with store.db() as c:
            c.execute("DELETE FROM yt_items")
        cat, rev = store.load_catalog()
        for v in cat["videos"]:
            if v["id"] == new:
                v["youtube_id"] = ""
        store.save_catalog(cat, rev)
        yp.refresh_items()
        self.assertEqual(yp.item(new)["state"], "on_youtube")
        self.assertEqual(yp.item(new)["match_kind"], "marker")
        # Own copy stays the main version and stays findable under the TikTok key.
        cat, _, _ = store.hydrated()
        entry = next(v for v in cat["videos"] if v["id"] == same)
        self.assertEqual(store.video_keys(entry)[0], "tt:1000000002")
        self.assertEqual(store.enqueue("https://www.tiktok.com/@wokearewe1/video/1000000002")["status"], "duplicate skipped")

    def test_quota_exhaustion_pauses_until_pacific_midnight_and_resumes(self):
        for n in range(3):
            self.hub_video(1000000010 + n, f"Different topic number {n} for the channel")
        self.connect()
        yp.configure({"visibility": "public", "per_day": 10})
        yp.start()
        self.yt.insert_quota = 1
        self.run_until_idle()
        self.assertEqual(self.yt.inserts, 1)
        s = yp.settings()
        self.assertGreater(s["paused_until"], time.time())
        self.assertAlmostEqual(s["paused_until"], yp.next_reset() + 300, delta=2)
        self.assertIn("midnight Pacific", yp.status()["pause_reason"])
        self.assertEqual(yp.upload_tick(), "Your Google project's daily YouTube allowance is used up. Uploads continue after it resets at midnight Pacific time.")
        # Next day.
        self.yt.insert_quota = 100
        yp.update(paused_until=0, quota_day="2000-01-01")
        self.run_until_idle()
        self.assertEqual(len(self.yt.videos), 3)
        self.assertEqual(self.yt.inserts, 3)
        # Own daily number is respected too.
        self.hub_video(1000000020, "One more different topic for today")
        self.hub_video(1000000021, "And another quite different topic")
        yp.refresh_items()
        yp.correct("v1000000020", "upload")
        yp.correct("v1000000021", "upload")
        yp.update(per_day=1, quota_day=yp.pacific_day(), uploads_today=1)
        self.assertEqual(yp.upload_tick(), "daily limit")
        self.assertEqual(self.yt.inserts, 3)

    def test_restart_mid_upload_resumes_without_duplicate(self):
        vid = self.hub_video(1000000030, "A long video that gets interrupted", size=900 * 1024)
        self.connect()
        yp.configure({"visibility": "public"})
        yp.start()
        real_send = yp.send
        calls = {"n": 0}

        def crash_after_two(item_id, uri, path, size, offset):
            original = self.yt.request

            def limited(method, url, **kw):
                if url == uri and not kw.get("headers", {}).get("Content-Range", "").startswith("bytes */"):
                    calls["n"] += 1
                    if calls["n"] > 2:
                        raise SystemExit("power cut")
                return original(method, url, **kw)
            with patch.object(self.yt, "request", side_effect=limited):
                return real_send(item_id, uri, path, size, offset)
        with patch.object(yp, "send", side_effect=crash_after_two):
            with self.assertRaises(SystemExit):
                yp.upload_tick()
        session = next(iter(self.yt.sessions.values()))
        self.assertEqual(session["received"], 2 * 256 * 1024)
        self.assertEqual(yp.item(vid)["state"], "uploading")
        store.init()  # process restart
        yp.PROGRESS.clear()
        self.assertEqual(yp.upload_tick(), "uploaded")
        self.assertEqual(self.yt.inserts, 1)
        self.assertEqual(len(self.yt.videos), 1)
        self.assertEqual(yp.item(vid)["state"], "uploaded")

    def test_finished_upload_before_restart_is_adopted_not_repeated(self):
        vid = self.hub_video(1000000040, "Finished just before the power went")
        self.connect()
        yp.configure({"visibility": "public"})
        yp.start()
        with patch.object(yp, "finish", side_effect=SystemExit("power cut")):
            with self.assertRaises(SystemExit):
                yp.upload_tick()
        store.init()
        self.assertEqual(yp.upload_tick(), "uploaded")
        self.assertEqual(self.yt.inserts, 1)
        # Expired session and lost address: the channel marker is found instead of re-uploading.
        vid2 = self.hub_video(1000000041, "Second video whose session expired")
        yp.refresh_items()
        yp.correct(vid2, "upload")
        yp.update(next_upload_at=0)
        with patch.object(yp, "finish", side_effect=SystemExit("power cut")):
            with self.assertRaises(SystemExit):
                yp.upload_tick()
        self.yt.drop_sessions = True
        store.init()
        self.assertEqual(yp.upload_tick(), "adopted")
        self.assertEqual(self.yt.inserts, 2)

    def test_lost_session_waits_and_checks_before_second_try(self):
        vid = self.hub_video(1000000050, "Upload whose session disappears", size=900 * 1024)
        self.connect()
        yp.configure({"visibility": "public"})
        yp.start()
        self.yt.drop_sessions = True
        self.assertEqual(yp.upload_tick(), "session gone")
        self.assertEqual(yp.upload_tick(), "recheck")
        self.assertEqual(self.yt.inserts, 1)
        self.assertIn("Checking your channel again", yp.item(vid)["reason"])
        self.yt.drop_sessions = False
        yp.set_item(vid, recheck_at=time.time() - 1, retry_at=0)
        self.assertEqual(yp.upload_tick(), "uploaded")
        self.assertEqual(self.yt.inserts, 2)
        self.assertEqual(len(self.yt.videos), 1)

    def test_private_result_expired_permission_and_settings_validation(self):
        vid = self.hub_video(1000000060, "Comes back private from YouTube")
        self.connect()
        yp.configure({"visibility": "public"})
        yp.start()
        self.yt.force_private = True
        yp.upload_tick()
        self.assertIn("private", yp.item(vid)["reason"])
        self.assertEqual(yp.status()["private_results"], 1)
        for bad in ({"visibility": "everyone"}, {"per_day": 101}, {"category": "999"}, {"password": "x"}, {"end_card": "yes"}):
            with self.assertRaises(ValueError):
                yp.configure(bad)
        self.yt.refresh_ok = False
        yp.edit_private(lambda d: d.update(access_expires=0))
        self.hub_video(1000000061, "Another one after permission removed")
        yp.refresh_items()
        yp.correct("v1000000061", "upload")
        yp.update(next_upload_at=0)
        yp.upload_tick()
        st = yp.status()
        self.assertFalse(st["connected"])
        self.assertIn("Connect YouTube", st["error"])


if __name__ == "__main__":
    import unittest
    unittest.main()
