"""Release 2026.11.15.16: caption titles, cover-text fallback, card pictures and title review."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_watcher
import admin_store as store
import captions
import thumbs
import title_review
import lite_site
import public_catalog

REAL_GENERATE = lite_site.generate  # The watcher test setup replaces it with a mock.

POST_ID = "7571743818165341452"
KEY = "tt:" + POST_ID


def text_image(path, text):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=720x1280", "-frames:v", "1",
                    "-vf", f"drawtext=text='{text}':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=300", str(path)], check=True)
    return path


class CaptionCases(unittest.TestCase):
    def test_normal_caption(self):
        s = captions.suggest("Ruth and Naomi - a story of loyalty #bible #faith", [], "2026-11-20")
        self.assertEqual((s["title"], s["origin"], s["needs_review"]), ("Ruth and Naomi - a story of loyalty", "caption", False))
        self.assertEqual(s["tags"], ["bible", "faith"])

    def test_capitalisation_and_wording_kept(self):
        self.assertEqual(captions.suggest("  wHY do   THEY lie??  #x", [])["title"], "wHY do THEY lie??")

    def test_hashtags_only(self):
        s = captions.suggest("#bible #truth", ["bible"], "2026-11-20T10:00:00Z")
        self.assertEqual((s["title"], s["origin"], s["needs_review"]), ("Untitled 2026-11-20", "none", True))
        self.assertEqual(s["tags"], ["bible", "truth"])

    def test_empty(self):
        for caption in ("", None, "   "):
            s = captions.suggest(caption, [], "2026-11-21")
            self.assertEqual((s["title"], s["tags"]), ("Untitled 2026-11-21", []))

    def test_very_long_cut_at_word(self):
        long = "This is a very long caption " * 12 + "#tag"
        t = captions.suggest(long, [])["title"]
        self.assertLessEqual(len(t), captions.MAX_TITLE)
        self.assertTrue(t.endswith("…"))
        self.assertTrue(long.startswith(t[:-1]), t)
        self.assertNotIn("  ", t)

    @unittest.skipUnless(shutil.which("tesseract") and shutil.which("ffmpeg"), "needs tesseract and ffmpeg")
    def test_cover_text_when_caption_has_no_words(self):
        with tempfile.TemporaryDirectory() as d:
            img = text_image(Path(d) / "cover.jpg", "Ruth and Naomi")
            s = captions.suggest("#bible", [], "2026-11-20", img)
            self.assertEqual((s["title"], s["origin"], s["needs_review"]), ("Ruth and Naomi", "cover", True))
            blank = Path(d) / "blank.jpg"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=360x640",
                            "-frames:v", "1", str(blank)], check=True)
            self.assertEqual(captions.suggest("#bible", [], "2026-11-20", blank)["title"], "Untitled 2026-11-20")

    def test_reply_label_is_not_a_title(self):
        # Real captions on @wokearewe1: TikTok's reply label followed by hashtags only.
        self.assertEqual(captions.suggest("Replying to @WOKE ARE WE #discourage #dreams", [], "2026-11-20")["title"], "Untitled 2026-11-20")
        self.assertEqual(captions.caption_words("Replying to @bob here is why #x"), "here is why")

    def test_cover_reading_drops_frame_scraps(self):
        raw = "THE FATHER AND THE SON\nARE NOT THE SAME\n=\no\nsYd\nN En\nL2 00\nHERES WHAT YOU NEED TO\nGOD HEAD\n"
        self.assertEqual(captions.clean_ocr(raw), "THE FATHER AND THE SON ARE NOT THE SAME HERES WHAT YOU NEED TO GOD HEAD")
        self.assertEqual(captions.clean_ocr("Mixed case Title here\nsecond line"), "Mixed case Title here second line")

    def test_no_tesseract_falls_back(self):
        with patch("shutil.which", return_value=None):
            self.assertEqual(captions.suggest("", [], "2026-11-20", "/nonexistent.jpg")["origin"], "none")


class StoreBacked(unittest.TestCase):
    setUp = test_watcher.WatchTests.setUp

    def catalogue(self, videos):
        store.CATALOG.write_text(json.dumps({"videos": videos}))

    def thumb(self):
        dest = thumbs.path_for(KEY)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=360x480",
                        "-frames:v", "1", str(dest)], check=True)
        return dest

    def video(self, title="Wrong title", **extra):
        return dict(id="v1", title=title, tags=["old"], category=[], added="2026-11-20T00:00:00Z",
                    tiktok_url=f"https://www.tiktok.com/@wokearewe1/video/{POST_ID}", **extra)

    def test_hand_import_takes_remembered_caption(self):
        captions.remember([{"id": POST_ID, "url": f"https://www.tiktok.com/@wokearewe1/video/{POST_ID}",
                            "caption": "Ruth and Naomi #bible", "hashtags": ["bible"]}])
        result = store.enqueue(f"https://www.tiktok.com/@wokearewe1/video/{POST_ID}", add_end_card=False)
        job = store.get_job(result["id"])
        self.assertEqual(job["title"], "Ruth and Naomi")
        self.assertIn("bible", json.loads(job["tags"]))
        self.assertEqual(job["needs_title"], 0)

    def test_typed_title_is_never_replaced(self):
        captions.remember([{"id": POST_ID, "url": "u", "caption": "Caption words"}])
        result = store.enqueue(f"https://www.tiktok.com/@wokearewe1/video/{POST_ID}", None, "My own title", add_end_card=False)
        self.assertEqual(store.get_job(result["id"])["title"], "My own title")

    def test_thumb_in_hydrated_export_and_lite_site(self):
        self.catalogue([self.video()])
        self.thumb()
        cat, _, _ = store.hydrated()
        self.assertEqual(cat["videos"][0]["thumb"], f"thumbs/tt-{POST_ID}.jpg")
        exported = public_catalog.export(store.load_catalog()[0])
        self.assertEqual(exported["videos"][0]["thumb"], f"thumbs/tt-{POST_ID}.jpg")
        with tempfile.TemporaryDirectory() as d:
            folder = REAL_GENERATE(exported, Path(d) / "site")
            self.assertTrue((Path(folder) / "thumbs" / f"tt-{POST_ID}.jpg").is_file())
            published = json.loads((Path(folder) / "videos.json").read_text())
            self.assertEqual(published["videos"][0]["thumb"], f"thumbs/tt-{POST_ID}.jpg")

    def test_lite_site_drops_missing_thumb(self):
        with tempfile.TemporaryDirectory() as d:
            v = self.video(thumb="thumbs/tt-1234567890.jpg")
            folder = REAL_GENERATE({"videos": [v]}, Path(d) / "site")
            self.assertNotIn("thumb", json.loads((Path(folder) / "videos.json").read_text())["videos"][0])

    def test_title_review_use_keep_set(self):
        self.catalogue([self.video(), dict(self.video("Keep me"), id="v2",
                                           tiktok_url="https://www.tiktok.com/@wokearewe1/video/7571743818165341453")])
        captions.remember([{"id": POST_ID, "url": "u1", "caption": "Ruth and Naomi #bible"},
                           {"id": "7571743818165341453", "url": "u2", "caption": "Other caption #faith"}])
        rows = {r["id"]: r for r in title_review.items()}
        self.assertEqual(rows["v1"]["suggestion"], "Ruth and Naomi")
        self.assertEqual(title_review.summary()["to_review"], 2)
        # Nothing changed by just looking.
        self.assertEqual(store.load_catalog()[0]["videos"][0]["title"], "Wrong title")
        self.assertEqual(title_review.apply({"action": "use", "ids": ["v1"]})["changed"], 1)
        v1 = store.load_catalog()[0]["videos"][0]
        self.assertEqual((v1["title"], v1["tags"]), ("Ruth and Naomi", ["old", "bible"]))
        title_review.apply({"action": "keep", "ids": ["v2"]})
        self.assertEqual(store.load_catalog()[0]["videos"][1]["title"], "Keep me")
        self.assertEqual(title_review.summary()["to_review"], 0)
        title_review.apply({"action": "set", "ids": ["v2"], "title": "  Typed   fix "})
        self.assertEqual(store.load_catalog()[0]["videos"][1]["title"], "Typed fix")
        with self.assertRaises(ValueError):
            title_review.apply({"action": "set", "ids": ["v1", "v2"], "title": "x"})
        with self.assertRaises(ValueError):
            title_review.apply({"action": "rename", "ids": ["v1"]})

    def test_backfill_from_saved_cover(self):
        self.catalogue([self.video()])
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "cover.jpg"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=720x1280",
                            "-frames:v", "1", str(src)], check=True)
            captions.remember([{"id": POST_ID, "url": "u", "caption": "#x", "thumbnail": "https://example.com/c.jpg"}])

            def fake(url, dest):
                shutil.copy(src, dest)
                return dest
            with patch.object(thumbs, "download_cover", side_effect=fake):
                self.assertEqual(thumbs.missing(), [KEY])
                thumbs.backfill()
        made = thumbs.path_for(KEY)
        self.assertTrue(made.is_file())
        width = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width",
                                "-of", "csv=p=0", str(made)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(width, "360")
        self.assertEqual(thumbs.missing(), [])


if __name__ == "__main__":
    unittest.main()
