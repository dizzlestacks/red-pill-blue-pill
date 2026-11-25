"""The NAS library: folders by category, readable names, and following site edits by moving files."""
import csv
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import admin_store as store  # noqa: E402
import disk_storage  # noqa: E402
import nas_library as lib  # noqa: E402


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        base = Path(self.temp.name)
        self.data, self.nas = base / "data", base / "nas"
        self.data.mkdir()
        self.nas.mkdir()
        patch.object(store, "STATE", self.data).start()
        patch.object(store, "CATALOG", self.data / "videos.json").start()
        store.CATALOG.write_text('{"videos":[]}')
        patch.dict(os.environ, {"STORAGE_BACKEND": "disk", "MIRROR_DIR": str(self.nas), "YOU2MP3_API_KEY": ""}).start()
        patch("durable_state.checkpoint", return_value=True).start()
        patch("lite_site.generate").start()
        patch.object(lib, "PAUSE_BETWEEN_COPIES", 0).start()
        store.init()
        self.videos = []

    def add(self, yid, title, category, content=None, play=None, added="2025-07-06T00:49:54Z"):
        """A saved video with an original (and optionally a separate playback copy)."""
        content = content or f"original {yid}".encode()
        objects = self.data / "objects"
        (objects / "media").mkdir(parents=True, exist_ok=True)
        (objects / "media" / f"{yid}.mp4").write_bytes(content)
        row = {"source_key": "yt:" + yid, "object_key": f"media/{yid}.mp4", "size": len(content),
               "sha256": hashlib.sha256(content).hexdigest(), "duration": 65.0, "created": time.time() + len(self.videos),
               "play_status": "ready", "play_key": f"media/{yid}.mp4", "play_sha256": hashlib.sha256(content).hexdigest(),
               "play_size": len(content), "check_status": "ok"}
        if play:
            (objects / "media" / f"{yid}-play.mp4").write_bytes(play)
            row.update(play_key=f"media/{yid}-play.mp4", play_sha256=hashlib.sha256(play).hexdigest(), play_size=len(play))
        with store.db() as c:
            c.execute(f"INSERT INTO media ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        self.videos.append({"id": "v" + yid, "title": title, "youtube_id": yid, "category": category,
                            "tags": ["truth", "bible"], "added": added})
        self.save()

    def save(self):
        _, rev = store.load_catalog()
        store.save_catalog({"videos": self.videos}, rev)

    def edit(self, yid, **fields):
        next(v for v in self.videos if v["youtube_id"] == yid).update(fields)
        self.save()

    def run_pass(self):
        lib.run_once()
        self.assertEqual(lib.STATUS["last_error"], "")
        return lib.STATUS

    def files(self, area="Videos"):
        root = self.nas / area
        return sorted(str(p.relative_to(root)) for p in root.rglob("*.mp4")) if root.exists() else []

    def test_layout_names_index_and_system_folder(self):
        self.add("AAAAAAAAAAA", "ARE YOU STILL SLEEPING? I THINK ITS TIME YOU WAKE UP", ["FALSE DOCTRINE", "TRUTH"],
                 play=b"playable copy")
        self.add("BBBBBBBBBBB", "#fyp #bible", [])
        self.add("CCCCCCCCCCC", "Plain", "")
        disk_storage.reconcile_mirror()
        status = self.run_pass()
        self.assertEqual(status["state"], "up to date")
        self.assertEqual(self.files(), [
            "FALSE DOCTRINE/2025-07-06 - ARE YOU STILL SLEEPING - I THINK ITS TIME YOU WAKE UP.mp4",
            f"Needs title/2025-07-06 - Untitled [{lib.short_id('yt:BBBBBBBBBBB')}].mp4",
            "Uncategorised/2025-07-06 - Plain.mp4"])
        # The playback copy is in Videos; the untouched original sits in Originals with the same name.
        self.assertEqual((self.nas / "Videos/FALSE DOCTRINE" / self.files()[0].split("/")[1]).read_bytes(), b"playable copy")
        self.assertEqual(self.files("Originals"), [self.files()[0]])
        self.assertEqual((self.nas / "Originals" / self.files()[0]).read_bytes(), b"original AAAAAAAAAAA")
        # Recovery copy lives only inside the system folder.
        self.assertTrue((self.nas / lib.SYSTEM / "objects/media/AAAAAAAAAAA.mp4").is_file())
        self.assertFalse((self.nas / "objects").exists())
        table = list(csv.DictReader(io.StringIO((self.nas / "index.csv").read_text("utf-8-sig"))))
        first = next(r for r in table if r["Title"].startswith("ARE YOU"))
        self.assertEqual((first["Categories"], first["Hashtags"], first["Published"], first["Post date"], first["Length"]),
                         ("FALSE DOCTRINE, TRUTH", "#truth #bible", "Yes", "2025-07-06", "1:05"))
        self.assertEqual(first["Original link"], "https://www.youtube.com/watch?v=AAAAAAAAAAA")
        page = (self.nas / "index.html").read_text()
        self.assertIn("Videos/FALSE%20DOCTRINE/", page)
        self.assertIn('id="q"', page)
        self.assertTrue((self.nas / "By source/YouTube videos/index.html").is_file())
        self.assertTrue((self.nas / "README.txt").is_file())

    def test_awkward_categories_duplicates_and_long_titles(self):
        self.add("DDDDDDDDDDD", "Same title", ['WHO/WHAT? "Truth": <Part 1>|*'])
        self.add("EEEEEEEEEEE", "Same title", ['WHO/WHAT? "Truth": <Part 1>|*'])
        self.add("FFFFFFFFFFF", "same TITLE", ["who/what? truth: part 1"])
        self.add("GGGGGGGGGGG", "Word " * 120, ["CON"])
        self.add("HHHHHHHHHHH", "Ünïcødé — 𐤉𐤄𐤉𐤔𐤉𐤏 title #hash", ["Läst Däys"])
        self.run_pass()
        names = self.files()
        for name in names:
            for part in name.split("/"):
                self.assertLessEqual(len(part), 100)
                self.assertNotRegex(part, r'[<>:"\\|?*]')
                part.encode("ascii")
        folders = {n.split("/")[0] for n in names}
        # Two categories that differ only by punctuation/case still get separate folders.
        self.assertEqual(len(folders), 4)
        self.assertEqual(len({n.lower() for n in names}), 5)
        dup = [n for n in names if "Same title" in n or "same TITLE" in n]
        self.assertEqual(sum("[" in n for n in dup), 1)  # Only the second of the same-folder pair gets an id.
        self.assertTrue(any(n.startswith("CON (folder)/") for n in names))
        self.assertTrue(any("Unicode - title" in n and n.startswith("Last Days/") for n in names))

    def test_rename_category_change_delete_follow_by_moving(self):
        self.add("IIIIIIIIIII", "First name", ["TRUTH"])
        self.run_pass()
        before = self.nas / "Videos/TRUTH/2025-07-06 - First name.mp4"
        self.assertTrue(before.is_file())
        inode = before.stat().st_ino
        copies = []
        original_copy = lib._copy
        with patch.object(lib, "_copy", side_effect=lambda *a: (copies.append(a), original_copy(*a))):
            self.edit("IIIIIIIIIII", title="Better name")
            self.run_pass()
            self.assertEqual(self.files(), ["TRUTH/2025-07-06 - Better name.mp4"])
            self.edit("IIIIIIIIIII", category=["HISTORY", "TRUTH"])
            self.run_pass()
            self.assertEqual(self.files(), ["HISTORY/2025-07-06 - Better name.mp4"])
            self.videos.clear()
            self.save()
            self.run_pass()
        self.assertEqual(copies, [])  # Moved, never re-copied.
        self.assertEqual(self.files(), [])
        moved = self.nas / "Removed from site/Videos/2025-07-06 - Better name.mp4"
        self.assertEqual(moved.stat().st_ino, inode)
        self.assertFalse(any(self.nas.glob(lib.MOVING + "/*")))
        self.assertFalse((self.nas / "Videos/TRUTH").exists())  # Emptied category folders are tidied away.

    def test_swapped_titles_do_not_collide(self):
        self.add("JJJJJJJJJJJ", "Alpha", ["A"], content=b"one")
        self.add("KKKKKKKKKKK", "Beta", ["A"], content=b"two")
        self.run_pass()
        self.edit("JJJJJJJJJJJ", title="Beta")
        self.edit("KKKKKKKKKKK", title="Alpha")
        self.run_pass()
        self.assertEqual((self.nas / "Videos/A/2025-07-06 - Alpha.mp4").read_bytes(), b"two")
        self.assertEqual(self.files(), ["A/2025-07-06 - Alpha.mp4", "A/2025-07-06 - Beta.mp4"])

    def test_nas_unavailable_then_catches_up_and_old_layout_is_moved(self):
        (self.nas / "objects/media").mkdir(parents=True)
        (self.nas / "objects/media/old.mp4").write_bytes(b"old copy")
        self.add("LLLLLLLLLLL", "Later", ["TRUTH"])
        with patch.dict(os.environ, {"MIRROR_DIR": str(self.nas / "missing")}):
            lib.run_once()
            disk_storage.reconcile_mirror()
        self.assertEqual(lib.STATUS["state"], "waiting for NAS")
        self.assertEqual(self.files(), [])
        disk_storage.reconcile_mirror()
        self.run_pass()
        self.assertEqual(self.files(), ["TRUTH/2025-07-06 - Later.mp4"])
        self.assertEqual((self.nas / lib.SYSTEM / "objects/media/old.mp4").read_bytes(), b"old copy")
        self.assertEqual(disk_storage.mirror_status()["count"], 1)

    def test_changed_file_is_set_aside_and_rebuild_recopies(self):
        self.add("MMMMMMMMMMM", "Keep", ["TRUTH"], content=b"good bytes")
        self.run_pass()
        target = self.nas / "Videos/TRUTH/2025-07-06 - Keep.mp4"
        target.write_bytes(b"BAD bytes!")  # Same size, different content: only a rebuild notices.
        self.run_pass()
        self.assertEqual(target.read_bytes(), b"BAD bytes!")
        lib.request_rebuild()
        status = self.run_pass()
        self.assertEqual(target.read_bytes(), b"good bytes")
        self.assertFalse(status["rebuild"])
        self.assertTrue((self.nas / lib.SET_ASIDE / "changed/Videos/TRUTH/2025-07-06 - Keep.mp4").is_file())
        self.assertTrue(status["problems"])

    def test_copy_budget_resumes(self):
        for n, yid in enumerate(["NNNNNNNNNNN", "OOOOOOOOOOO", "PPPPPPPPPPP"]):
            self.add(yid, f"Clip {n}", ["TRUTH"])
        with patch.object(lib, "COPY_BUDGET", -1):
            status = self.run_pass()
        self.assertEqual((status["state"], status["done"], status["total"]), ("copying", 0, 3))
        self.assertIn("(copying soon)", (self.nas / "index.csv").read_text())
        status = self.run_pass()
        self.assertEqual((status["state"], status["done"]), ("up to date", 3))


if __name__ == "__main__":
    unittest.main()
