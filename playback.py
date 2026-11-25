"""Browser-friendly playback copies of saved videos.

The original download is never modified or deleted. When an original is not
safe for every browser (iPhone Safari is the strictest), a separate playback
copy is made next to it: H.264 (level 4.2 or lower, yuv420p), AAC audio and the
index ("moov") at the start of the file so playback can begin immediately.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import admin_store as store

MAX_LEVEL = 42              # H.264 level 4.2: 1080p60, accepted by every iPhone and browser.
MAX_LONG, MAX_SHORT = 1920, 1080
SAFE_PROFILES = {"Baseline", "Constrained Baseline", "Main", "High"}
COLUMNS = {
    "play_key": "TEXT", "play_size": "INTEGER", "play_sha256": "TEXT",
    "play_status": "TEXT", "play_reason": "TEXT NOT NULL DEFAULT ''", "play_updated": "REAL",
}
PREP_LOCK = threading.Lock()
STALE_SECONDS = 2 * 3600

READY, PROCESSING, MISSING, UNSUPPORTED, FAILED = "ready", "processing", "missing", "unsupported", "failed"


def init(c):
    columns = {r[1] for r in c.execute("PRAGMA table_info(media)")}
    for name, definition in COLUMNS.items():
        if name not in columns:
            c.execute(f"ALTER TABLE media ADD COLUMN {name} {definition}")


def _set(key, **values):
    values["play_updated"] = time.time()
    with store.db() as c:
        c.execute("UPDATE media SET " + ",".join(f"{k}=?" for k in values) + " WHERE source_key=?",
                  (*values.values(), key))


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, timeout=120, check=True)
    return json.loads(result.stdout)


def index_first(path):
    """True when the MP4 index (moov) comes before the media data, so playback can start at once."""
    with open(path, "rb") as f:
        while True:
            header = f.read(8)
            if len(header) < 8:
                return False
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            if size == 1:
                size = int.from_bytes(f.read(8), "big")
                skip = size - 16
            elif size == 0:
                return kind == b"moov"
            else:
                skip = size - 8
            if kind == b"moov":
                return True
            if kind == b"mdat" or skip < 0:
                return False
            f.seek(skip, os.SEEK_CUR)


def _fps(stream):
    try:
        num, den = (stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1").split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def assess(path):
    """Return (plan, info). plan says what must change for universal browser playback."""
    info = probe(path)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("no video track")
    w, h = int(video.get("width") or 0), int(video.get("height") or 0)
    fits = max(w, h) <= MAX_LONG and min(w, h) <= MAX_SHORT and _fps(video) <= 61
    h264 = (video.get("codec_name") == "h264" and video.get("profile") in SAFE_PROFILES
            and video.get("pix_fmt") in ("yuv420p", "yuvj420p") and fits)
    level = int(video.get("level") or 0)
    bitrate = int(video.get("bit_rate") or info.get("format", {}).get("bit_rate") or 0)
    if h264 and 0 < level <= MAX_LEVEL:
        video_plan = "copy"
    elif h264 and int(video.get("refs") or 1) <= 4 and bitrate <= 40_000_000:
        # Only the declared level is too high for this size/rate; relabel without re-encoding.
        video_plan = "relevel"
    else:
        video_plan = "encode"
    audio_plan = "none" if not audio else ("copy" if audio.get("codec_name") == "aac" else "encode")
    container_ok = "mp4" in info.get("format", {}).get("format_name", "") and index_first(path)
    return {"video": video_plan, "audio": audio_plan, "container_ok": container_ok,
            "playable": video_plan == "copy" and audio_plan in ("copy", "none") and container_ok,
            "width": w, "height": h, "fps": _fps(video), "codec": video.get("codec_name"),
            "audio_codec": audio.get("codec_name") if audio else None, "level": level,
            "duration": float(info.get("format", {}).get("duration") or 0)}


def describe(plan):
    reasons = []
    if plan["video"] == "relevel":
        reasons.append(f"H.264 level {plan['level'] / 10:g} label")
    elif plan["video"] == "encode":
        reasons.append(f"{plan['codec']} video")
    if plan["audio"] == "encode":
        reasons.append(f"{plan['audio_codec']} audio")
    if not plan["container_ok"]:
        reasons.append("index at the end of the file")
    return ", ".join(reasons)


def convert(source, dest, plan):
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
    if plan["video"] == "copy":
        cmd += ["-c:v", "copy", "-tag:v", "avc1"]
    elif plan["video"] == "relevel":
        cmd += ["-c:v", "copy", "-bsf:v", "h264_metadata=level=4.2", "-tag:v", "avc1"]
    else:
        w, h = plan["width"], plan["height"]
        factor = min(1.0, MAX_LONG / max(w, h, 1), MAX_SHORT / max(min(w, h), 1))
        tw, th = max(2, int(w * factor) // 2 * 2), max(2, int(h * factor) // 2 * 2)
        cmd += ["-vf", f"scale={tw}:{th}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
                "-maxrate", "8M", "-bufsize", "16M", "-tag:v", "avc1"]
        if plan["fps"] > 61:
            cmd += ["-r", "60"]
    if plan["audio"] == "copy":
        cmd += ["-c:a", "copy"]
    elif plan["audio"] == "encode":
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    cmd += ["-movflags", "+faststart", str(dest)]
    timeout = max(900, int(plan["duration"] * 8))  # Software encoding on a Pi is slow; allow for it.
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_original(client, row, folder):
    """Path to the original bytes without copying when they are already on local disk."""
    if hasattr(client, "path"):
        path = client.path(row["object_key"])
        if not path.is_file():
            raise FileNotFoundError("The saved video file is missing from the data folder.")
        if path.stat().st_size != row["size"]:
            raise FileNotFoundError("The saved video file is incomplete (its size does not match the verified copy).")
        return path
    path = Path(folder) / "original.mp4"
    client.download_to_filename(row["object_key"], str(path))
    return path


def _poster(client, row, video, folder):
    key = row["poster_key"] or row["object_key"] + ".jpg"
    if row["poster_key"] and client.exists(key):
        return key
    image = Path(folder) / "poster.jpg"
    for offset in ("1", "0"):
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", offset, "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=640:-2", str(image)], check=True, capture_output=True, timeout=120)
            if image.is_file() and image.stat().st_size:
                client.upload_from_filename(key, str(image))
                return key
        except Exception:
            continue
    return row["poster_key"]


def prepare(key, force=False):
    """Make sure the saved video has a browser-friendly version. Returns the final state."""
    import media_worker as worker
    import tiktok_watch
    with PREP_LOCK:
        with store.db() as c:
            row = c.execute("SELECT * FROM media WHERE source_key=?", (key,)).fetchone()
        if not row:
            return {"state": MISSING, "reason": "No saved copy is recorded for this video."}
        row = dict(row)
        if row.get("play_status") == READY and not force:
            return state(row)
        _set(key, play_status=PROCESSING, play_reason="Preparing a version that plays in every browser")
        try:
            client = worker.storage_client()
            with tempfile.TemporaryDirectory(prefix="waw-play-", dir=store.STATE) as folder:
                original = _local_original(client, row, folder)
                try:
                    plan = assess(original)
                except Exception:
                    _set(key, play_status=UNSUPPORTED, play_reason="The saved file could not be read as a video.")
                    return state(key)
                poster_key = _poster(client, row, original, folder)
                if poster_key != row["poster_key"]:
                    _set(key, poster_key=poster_key)
                if plan["playable"]:
                    _set(key, play_key=row["object_key"], play_size=row["size"], play_sha256=row["sha256"],
                         play_status=READY, play_reason="", poster_key=poster_key)
                    return state(key)
                tiktok_watch.disk_guard(extra=2 * row["size"] + 64 * 1024 * 1024)
                out = Path(folder) / "playback.mp4"
                try:
                    convert(original, out, plan)
                    worker.verify_media(out)
                    final = assess(out)
                except subprocess.TimeoutExpired:
                    _set(key, play_status=FAILED, play_reason="Converting the video took too long. Retry the preview when the Pi is less busy.")
                    return state(key)
                except Exception:
                    _set(key, play_status=UNSUPPORTED,
                         play_reason=f"This video's format ({describe(plan) or 'unknown'}) could not be converted for browsers.")
                    return state(key)
                if not final["playable"]:
                    _set(key, play_status=UNSUPPORTED,
                         play_reason=f"The converted version still isn't browser-friendly ({describe(final)}).")
                    return state(key)
                play_key = row["object_key"][:-4] + ".play.mp4"
                size, digest = out.stat().st_size, _sha(out)
                client.upload_from_filename(play_key, str(out))
                if hasattr(client, "path") and (client.path(play_key).stat().st_size != size or _sha(client.path(play_key)) != digest):
                    raise OSError("Playback copy verification failed")
                _set(key, play_key=play_key, play_size=size, play_sha256=digest, play_status=READY,
                     play_reason="Converted for browsers (" + describe(plan) + "); original kept", poster_key=poster_key)
                return state(key)
        except FileNotFoundError as e:
            _set(key, play_status=MISSING, play_reason=str(e))
        except tiktok_watch.Paused as e:
            _set(key, play_status=FAILED, play_reason="Not enough free space to prepare the preview. " + str(e))
        except Exception:
            _set(key, play_status=FAILED, play_reason="Couldn't read or write the data folder while preparing the preview. Retry the preview.")
        return state(key)


def state(row_or_key):
    row = row_or_key
    if isinstance(row_or_key, str):
        with store.db() as c:
            row = c.execute("SELECT * FROM media WHERE source_key=?", (row_or_key,)).fetchone()
    if not row:
        return {"state": MISSING, "reason": "No saved copy is recorded for this video.", "poster": False}
    row = dict(row)
    status = row.get("play_status")
    if status == PROCESSING and (row.get("play_updated") or 0) < time.time() - STALE_SECONDS:
        status = None
    if not status:
        return {"state": PROCESSING, "reason": "Checking that the video plays in every browser.", "poster": bool(row["poster_key"])}
    return {"state": status, "reason": row.get("play_reason") or "", "poster": bool(row["poster_key"])}


def checked_state(key):
    """State plus a cheap check that the file the player gets is really there."""
    import media_worker as worker
    current = state(key)
    if current["state"] != READY:
        return current
    with store.db() as c:
        row = dict(c.execute("SELECT * FROM media WHERE source_key=?", (key,)).fetchone())
    try:
        client = worker.storage_client()
        if hasattr(client, "path"):
            path = client.path(row["play_key"] or row["object_key"])
            if not path.is_file() or path.stat().st_size != (row["play_size"] or row["size"]):
                _set(key, play_status=MISSING, play_reason="The saved video file is missing from the data folder.")
                return state(key)
    except Exception:
        return {**current, "state": FAILED, "reason": "The data folder can't be read right now. Check that it is mounted, then retry the preview."}
    return current


def start(key, force=False):
    # Mark it busy now so the caller's next status check never sees the old result.
    _set(key, play_status=PROCESSING, play_reason="Preparing a version that plays in every browser")
    threading.Thread(target=prepare, args=(key, force), daemon=True, name="playback-prepare").start()


def backfill_loop():
    """Prepare saved videos from earlier releases, one at a time, oldest first."""
    while True:
        try:
            with store.db() as c:
                row = c.execute("""SELECT source_key FROM media WHERE play_status IS NULL
                    OR (play_status=? AND COALESCE(play_updated,0) < ?) ORDER BY created LIMIT 1""",
                                (PROCESSING, time.time() - STALE_SECONDS)).fetchone()
            if row:
                prepare(row["source_key"])
                continue
        except Exception:
            pass
        time.sleep(30)
