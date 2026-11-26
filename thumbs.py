"""Small cover pictures for every video card (TikTok profile-grid style).

TikTok videos use the cover saved with the import, else a frame from the saved
copy. Files live in <data>/thumbs on the Pi and are published with the lite site
(as thumbs/<name>.jpg in the repository) so they show even when the Pi is down.
"""
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
import admin_store as store

NAME = re.compile(r"(?:tt-\d{8,25}|yt-[\w-]{11})\.jpg")
PUBLIC = re.compile(r"thumbs/(?:tt-\d{8,25}|yt-[\w-]{11})\.jpg")
MAX_COVER = 4 * 1024 * 1024
LOCK = threading.Lock()
FAILED = {}  # source key -> time to try again (a broken cover is not fetched every minute)
STATUS = {"running": False, "done": 0, "missing": 0, "failed": 0, "last_run": None}


def folder():
    return store.STATE / "thumbs"


def name(source_key):
    key = str(source_key or "")
    candidate = key.replace(":", "-", 1) + ".jpg"
    return candidate if NAME.fullmatch(candidate) else None


def path_for(source_key):
    n = name(source_key)
    return folder() / n if n else None


def find(n):
    """The thumbnail file for a name, on the Pi's data folder or in the release (repository) copy."""
    if not n or not NAME.fullmatch(n):
        return None
    for base in (folder(), store.ROOT / "thumbs"):
        p = base / n
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def for_video(v):
    """Relative public path of this video's thumbnail ("thumbs/tt-….jpg"), when one exists."""
    current = v.get("thumb")
    if isinstance(current, str) and PUBLIC.fullmatch(current) and find(current[7:]):
        return current
    try:
        keys = store.video_keys(v)
    except Exception:
        return None  # No Pi database (e.g. the Netlify build): only published thumb fields count.
    for key in keys:
        if not key.startswith("tt:"):
            continue
        n = name(key)
        if n and find(n):
            return "thumbs/" + n
    return None


def to_jpeg(src, dest):
    """Scale to a small JPEG (360px wide) so hundreds of cards stay fast."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-frames:v", "1",
                    "-vf", "scale='min(360,iw)':-2", "-q:v", "5", str(tmp)],
                   check=True, capture_output=True, timeout=60)
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise ValueError("Thumbnail could not be made")
    tmp.replace(dest)
    return dest


def download_cover(url, dest):
    """Fetch a TikTok cover. you2mp3 links use the connection key; other HTTPS images are fetched plainly."""
    p = urlparse(str(url or ""))
    if p.scheme != "https" or not p.hostname:
        raise ValueError("Cover link must be HTTPS")
    if p.hostname == "you2mp3.music":
        import media_worker as worker
        path = p.path[len("/api/v1"):] if p.path.startswith("/api/v1/") else p.path
        response = worker.api(path + (("?" + p.query) if p.query else ""), binary=True)
    else:
        import requests  # Only on the Pi; the Netlify build never fetches covers.
        response = requests.get(url, timeout=(10, 30), stream=True, allow_redirects=True,
                                headers={"User-Agent": "WokeAreWe-archive/1.0"})
        if not response.ok:
            response.close()
            raise ValueError("Cover not available")
    size = 0
    try:
        kind = response.headers.get("Content-Type", "")
        if kind and not kind.startswith("image/") and "octet-stream" not in kind:
            raise ValueError("Cover link is not an image")
        with open(dest, "wb") as f:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_COVER:
                    raise ValueError("Cover image is too large")
                f.write(chunk)
    finally:
        response.close()
    if not size:
        raise ValueError("Cover image is empty")
    return dest


def poster_bytes(source_key):
    with store.db() as c:
        row = c.execute("SELECT poster_key FROM media WHERE source_key=?", (source_key,)).fetchone()
    if not row or not row["poster_key"]:
        return None
    import media_worker as worker
    client = worker.storage_client()
    try:
        return client.path(row["poster_key"]).read_bytes() if hasattr(client, "path") else client.download_as_bytes(row["poster_key"])
    except Exception:
        return None


def make(source_key, cover_url=None, video=None, force=False):
    """Save the thumbnail for a source: cover first, then a frame of the saved copy. Returns its path or None."""
    dest = path_for(source_key)
    if not dest:
        return None
    if dest.exists() and not force:
        return dest
    store.STATE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="waw-thumb-", dir=store.STATE) as tmp:
        if cover_url:
            try:
                raw = download_cover(cover_url, Path(tmp) / "cover")
                return to_jpeg(raw, dest)
            except Exception:
                pass
        try:
            if video:
                return to_jpeg(video, dest)
            data = poster_bytes(source_key)
            if data:
                raw = Path(tmp) / "poster.jpg"
                raw.write_bytes(data)
                return to_jpeg(raw, dest)
        except Exception:
            return None
    return None


def cover_url(source_key, job=None):
    import captions
    p = captions.for_job(job) if job else captions.post(source_key)
    if not p:
        p = captions.post(source_key)
    url = (p or {}).get("thumbnail")
    return url if isinstance(url, str) and url.startswith("https://") else None


def missing():
    """TikTok sources in the catalogue or queue that have no thumbnail yet."""
    cat, _ = store.load_catalog()
    keys = []
    for v in cat["videos"]:
        keys += [k for k in store.video_keys(v) if k.startswith("tt:")]
    with store.db() as c:
        keys += [r["source_key"] for r in c.execute("SELECT source_key FROM jobs WHERE kind='tiktok'")]
        has_media = {r["source_key"] for r in c.execute("SELECT source_key FROM media")}
    out = []
    for key in dict.fromkeys(keys):
        n = name(key)
        if n and not find(n) and FAILED.get(key, 0) <= time.time() and (key in has_media or cover_url(key)):
            out.append(key)
    return out


def backfill(limit=None):
    """Fill in missing thumbnails (worker loop and the admin button). Never blocks a second run."""
    if not LOCK.acquire(blocking=False):
        return STATUS
    try:
        todo = missing()
        STATUS.update(running=True, missing=len(todo), done=0, failed=0)
        for key in todo[:limit] if limit else todo:
            if make(key, cover_url(key)):
                STATUS["done"] += 1
            else:
                STATUS["failed"] += 1
                FAILED[key] = time.time() + 6 * 3600
        STATUS.update(missing=max(0, len(todo) - STATUS["done"]), last_run=time.time())
        return STATUS
    finally:
        STATUS["running"] = False
        LOCK.release()


def status():
    return dict(STATUS)
