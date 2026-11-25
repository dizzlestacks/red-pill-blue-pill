"""Saved videos are only "verified" after a real picture-and-sound check.

Covers: no audio track, a silent audio track, an audio format browsers don't
play (MP3), sound shorter than the picture, very quiet sound, a damaged file,
automatic limited re-downloads, "Keep without sound", and "check all saved videos".
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import admin_store as store  # noqa: E402
import media_check  # noqa: E402
import media_worker as worker  # noqa: E402
import playback  # noqa: E402
import tiktok_watch as watch  # noqa: E402

FIXTURES = {}


def make(name, audio="aac", seconds=4, audio_seconds=None, volume=None):
    """Small test video: 'none' = no audio track, 'silent' = digital silence, else a tone in that codec."""
    key = (name, audio, seconds, audio_seconds, volume)
    if key in FIXTURES:
        return FIXTURES[key]
    folder = Path(tempfile.mkdtemp(prefix="waw-check-fixture-"))
    out = folder / f"{name}.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x568:rate=25:duration={seconds}"]
    if audio != "none":
        source = "anullsrc=r=44100:cl=stereo" if audio == "silent" else "sine=frequency=440:sample_rate=44100"
        cmd += ["-f", "lavfi", "-t", str(audio_seconds or seconds), "-i", source]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "main"]
    if audio != "none":
        if volume is not None:
            cmd += ["-af", f"volume={volume}dB"]
        cmd += ["-c:a", {"aac": "aac", "silent": "aac", "mp3": "libmp3lame"}[audio], "-ac", "2"]
    cmd += ["-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    FIXTURES[key] = out
    return out


class InspectTests(unittest.TestCase):
    def test_good_file_passes_with_details(self):
        details = media_check.inspect(make("good"))
        self.assertEqual((details["video_codec"], details["audio_codec"]), ("h264", "aac"))
        self.assertTrue(details["browser_audio"])
        self.assertFalse(details["quiet"])
        self.assertAlmostEqual(details["seconds"], 4, delta=0.3)
        self.assertGreaterEqual(details["decoded_seconds"], 3.5)

    def test_no_audio_track(self):
        with self.assertRaises(media_check.Problem) as caught:
            media_check.inspect(make("mute", audio="none"))
        self.assertEqual(caught.exception.code, "no-sound")
        self.assertEqual(str(caught.exception), "Problem: no sound - the file has no audio track.")
        # The owner can confirm a source genuinely has no sound.
        self.assertIsNone(media_check.inspect(make("mute", audio="none"), allow_silent=True)["audio_codec"])

    def test_silent_track(self):
        with self.assertRaises(media_check.Problem) as caught:
            media_check.inspect(make("silent", audio="silent"))
        self.assertEqual(caught.exception.code, "silent")
        self.assertIn("silent for the whole video", caught.exception.reason)
        self.assertLess(caught.exception.details["max_db"], media_check.SILENT_DB)

    def test_unsupported_audio_format_is_flagged_for_conversion(self):
        details = media_check.inspect(make("mp3", audio="mp3"))
        self.assertEqual(details["audio_codec"], "mp3")
        self.assertFalse(details["browser_audio"])
        self.assertEqual(playback.assess(make("mp3", audio="mp3"))["audio"], "encode")

    def test_sound_shorter_than_picture(self):
        with self.assertRaises(media_check.Problem) as caught:
            media_check.inspect(make("short-sound", seconds=10, audio_seconds=3))
        self.assertEqual(caught.exception.code, "incomplete")
        self.assertIn("sound lasts 0:03 but the picture lasts 0:10", caught.exception.reason)

    def test_length_compared_with_source(self):
        with self.assertRaises(media_check.Problem) as caught:
            media_check.inspect(make("good"), expected=60)
        self.assertIn("the source is 1:00", caught.exception.reason)

    def test_quiet_sound_detected(self):
        self.assertTrue(media_check.inspect(make("quiet", volume=-38))["quiet"])

    def test_damaged_file(self):
        damaged = Path(tempfile.mkdtemp()) / "cut.mp4"
        data = make("long", seconds=20).read_bytes()
        damaged.write_bytes(data[: len(data) // 3])
        with self.assertRaises(media_check.Problem) as caught:
            media_check.inspect(damaged)
        self.assertIn(caught.exception.code, ("unreadable", "damaged", "incomplete"))


class Response:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        self.headers = {"Content-Length": str(len(self.data))}

    def iter_content(self, size):
        for i in range(0, len(self.data), size):
            yield self.data[i:i + size]

    def close(self):
        pass


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        root = Path(self.temp.name)
        patch.object(store, "STATE", root).start()
        patch.object(store, "CATALOG", root / "videos.json").start()
        store.CATALOG.write_text('{"videos":[]}')
        patch.dict(os.environ, {"YOU2MP3_API_KEY": "simulated-only", "STORAGE_BACKEND": "disk", "MIRROR_DIR": ""}).start()
        patch("durable_state.checkpoint", return_value=True).start()
        patch("lite_site.generate").start()
        patch.object(watch.shutil, "disk_usage", return_value=type("Usage", (), {"free": 50 * watch.GB})()).start()
        store.init()
        self.downloads = []
        self.requests = []

    def run_import(self, jid, files):
        """Simulate the media service: each new request returns the next file."""
        def api(path, payload=None, idempotency=None, binary=False):
            if path == "/capabilities":
                return {}
            if path == "/videos":
                self.requests.append(idempotency)
                return {"id": len(self.requests)}
            if path.startswith("/videos/"):
                return {"status": "done", "outputFilename": f"clip{path.rsplit('/', 1)[1]}.mp4"}
            if path.startswith("/files/"):
                n = int(path.split("clip")[1].split(".")[0])
                self.downloads.append(n)
                return Response(files[min(n, len(files)) - 1])
            return {}
        with patch.object(worker, "api", side_effect=api):
            worker.process(store.get_job(jid))

    def job(self, n=1):
        n = 7500000000000000000 + n
        return store.enqueue(f"https://www.tiktok.com/@wokearewe1/video/{n}", title="Ruth")["id"]

    def media(self, jid):
        with store.db() as c:
            return dict(c.execute("SELECT * FROM media WHERE source_key=?", (store.get_job(jid)["source_key"],)).fetchone())

    def test_no_sound_download_is_not_verified_and_redownloads_then_succeeds(self):
        jid = self.job()
        self.run_import(jid, [make("mute", audio="none"), make("good")])
        job = store.get_job(jid)
        self.assertEqual(job["status"], "problem")
        self.assertTrue(job["reason"].startswith("Problem: no sound - the file has no audio track."))
        self.assertIn("try 2 of 3", job["reason"])
        self.assertNotIn("Verified", job["reason"])
        self.assertEqual(self.media(jid)["check_status"], "problem")
        with self.assertRaisesRegex(ValueError, "verified imported copy"):
            store.publish(jid)
        # The worker only retries after the delay.
        with store.db() as c:
            due = c.execute("SELECT count(*) FROM jobs WHERE status='problem' AND download_attempts < ? AND updated < ?",
                            (media_check.MAX_DOWNLOADS, time.time() - media_check.RETRY_DELAY)).fetchone()[0]
        self.assertEqual(due, 0)
        self.run_import(jid, [make("mute", audio="none"), make("good")])
        job = store.get_job(jid)
        self.assertEqual(job["status"], "imported", job["reason"])
        self.assertTrue(job["reason"].startswith("Verified permanent copy. Checked: picture (h264) and sound (aac)"))
        self.assertEqual(len(set(self.requests)), 2, "a re-download is a new request to the media service")
        self.assertEqual(self.media(jid)["check_status"], "ok")
        store.publish(jid)
        self.assertEqual(store.get_job(jid)["status"], "published")

    def test_retries_are_limited_and_keep_without_sound(self):
        jid = self.job()
        mute = make("mute", audio="none")
        for _ in range(media_check.MAX_DOWNLOADS):
            self.run_import(jid, [mute] * 5)
        job = store.get_job(jid)
        self.assertEqual((job["status"], job["download_attempts"]), ("problem", media_check.MAX_DOWNLOADS))
        self.assertIn("Downloaded 3 times with the same result", job["reason"])
        self.assertIn("Keep without sound", job["reason"])
        with store.db() as c:
            due = c.execute("SELECT count(*) FROM jobs WHERE status='problem' AND download_attempts < ?",
                            (media_check.MAX_DOWNLOADS,)).fetchone()[0]
        self.assertEqual(due, 0, "no more automatic downloads")
        media_check.accept_silent(job)
        media_check.check_saved(job["source_key"])
        self.assertEqual(store.get_job(jid)["status"], "imported")
        self.assertEqual(self.media(jid)["check_status"], "accepted")
        store.publish(jid)

    def test_silent_and_mp3_downloads(self):
        silent, mp3 = self.job(1), self.job(2)
        self.run_import(silent, [make("silent", audio="silent")])
        self.assertIn("silent for the whole video", store.get_job(silent)["reason"])
        self.run_import(mp3, [make("mp3", audio="mp3")])
        self.assertEqual(store.get_job(mp3)["status"], "imported", store.get_job(mp3)["reason"])
        m = self.media(mp3)
        self.assertNotEqual(m["play_key"], m["object_key"], "browser-friendly copy made, original kept")
        client = worker.storage_client()
        self.assertEqual(playback.assess(client.path(m["play_key"]))["audio_codec"], "aac")
        self.assertEqual(playback.assess(client.path(m["object_key"]))["audio_codec"], "mp3")
        self.assertEqual(json.loads(m["check_details"])["playback_audio"], "aac")

    def test_quiet_sound_raised_in_playback_copy_only(self):
        jid = self.job()
        self.run_import(jid, [make("quiet", volume=-38)])
        m = self.media(jid)
        self.assertIn("quiet sound raised", store.get_job(jid)["reason"])
        client = worker.storage_client()
        louder = media_check.inspect(client.path(m["play_key"]), full_decode=False)
        original = media_check.inspect(client.path(m["object_key"]), full_decode=False)
        self.assertGreater(louder["lufs"], original["lufs"] + 10)

    def test_check_all_finds_problems_and_redownloads_in_one_go(self):
        good, bad = self.job(1), self.job(2)
        self.run_import(good, [make("good")])
        store.publish(good)
        # Simulate a copy saved by an earlier release that had no sound: replace its file.
        m = self.media(good)
        client = worker.storage_client()
        path = client.path(m["object_key"])
        mute = make("mute", audio="none").read_bytes()
        path.write_bytes(mute)
        with store.db() as c:
            c.execute("UPDATE media SET size=?, play_key=NULL, play_status=NULL WHERE source_key=?", (len(mute), m["source_key"]))
        self.run_import(bad, [make("silent", audio="silent")])
        report = media_check.run_all()
        self.assertTrue(report["running"])
        while media_check._next():
            media_check.check_saved(media_check._next())
        report = media_check.report()
        self.assertFalse(report["running"])
        self.assertEqual({p["job_id"] for p in report["problems"]}, {good, bad})
        published = next(p for p in report["problems"] if p["job_id"] == good)
        self.assertTrue(published["published"])
        self.assertEqual(published["code"], "no-sound")
        self.assertEqual(store.get_job(good)["status"], "published", "a public video stays public until replaced")
        for p in report["problems"]:
            media_check.request_redownload(store.get_job(p["job_id"]))
        with store.db() as c:
            queued = c.execute("SELECT id FROM jobs WHERE status='problem' AND download_attempts < ? AND updated < ?",
                               (media_check.MAX_DOWNLOADS, time.time() - media_check.RETRY_DELAY)).fetchall()
        self.assertEqual({r["id"] for r in queued}, {good, bad})
        self.run_import(good, [make("good")] * 9)
        self.assertEqual(store.get_job(good)["status"], "published", store.get_job(good)["reason"])
        self.assertTrue(store.get_job(good)["reason"].startswith("Downloaded again and verified."))


if __name__ == "__main__":
    unittest.main()
