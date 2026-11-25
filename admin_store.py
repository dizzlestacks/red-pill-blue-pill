"""Catalogue and restart-safe queue state. Never serve this module or its data."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent.resolve()
STATE = Path(os.environ.get("DATA_DIR", os.environ.get("ADMIN_DATA_DIR", str(ROOT / ".private")))).resolve()
CATALOG = STATE / "videos.json"
LOCK = threading.RLock()
RELEASE = "2026.11.15.9"


def db():
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    c = sqlite3.connect(STATE / "admin.sqlite", timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("PRAGMA synchronous=FULL")
    return c


def init():
    if os.environ.get("DATA_DIR") and not STATE.is_dir():
        raise RuntimeError("DATA_DIR does not exist. Mount/create the intended data folder before starting.")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not CATALOG.exists():
        if (STATE / "admin.sqlite").exists():
            # An existing queue without its catalogue must never be silently reseeded.
            raise RuntimeError("Existing data folder is missing videos.json; restore a recovery snapshot first")
        shutil.copyfile(ROOT / "videos.json", CATALOG)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, url TEXT NOT NULL,
          kind TEXT NOT NULL, video_id TEXT, title TEXT NOT NULL, tags TEXT NOT NULL,
          status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', upstream_id INTEGER,
          idempotency TEXT NOT NULL, payload TEXT, attempts INTEGER DEFAULT 0,
          created REAL NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS media (
          source_key TEXT PRIMARY KEY, object_key TEXT NOT NULL, poster_key TEXT,
          size INTEGER NOT NULL, sha256 TEXT NOT NULL, duration REAL NOT NULL,
          created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, url TEXT NOT NULL, status TEXT NOT NULL,
          reason TEXT NOT NULL, created REAL NOT NULL);
        """)
        columns = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        for name, definition in {
            "automatic": "INTEGER NOT NULL DEFAULT 0",
            "auto_publish": "INTEGER NOT NULL DEFAULT 0",
            "add_end_card": "INTEGER",
            "source_date": "TEXT",
            "metadata": "TEXT",
        }.items():
            if name not in columns:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        import playback
        playback.init(c)
        # Earlier releases used a hashtag-only caption as the title. Unpublished
        # items become "Untitled <post date>" and keep their hashtags as tags.
        for job in c.execute("SELECT id,title,source_date,created FROM jobs WHERE status!='published'").fetchall():
            if hashtags_only(job["title"]):
                date = (job["source_date"] or "")[:10] or time.strftime("%Y-%m-%d", time.gmtime(job["created"]))
                c.execute("UPDATE jobs SET title=? WHERE id=?", ("Untitled " + date, job["id"]))
    import tiktok_watch
    tiktok_watch.init()
    import back_catalog
    back_catalog.init()


def source(url):
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError("Invalid video URL")
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        raise ValueError("Use an HTTPS video link without credentials")
    if host in ("www.tiktok.com", "tiktok.com"):
        import tiktok_watch
        owner = tiktok_watch.owner()
        m = re.fullmatch(r"/@" + re.escape(owner) + r"/video/(\d{8,25})/?", p.path)
        if not m:
            raise ValueError(f"Use a canonical @{owner} TikTok post link, not a profile or short link")
        return "tt:" + m[1], f"https://www.tiktok.com/@{owner}/video/" + m[1], "tiktok"
    video = None
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        video = parse_qs(p.query).get("v", [None])[0]
        if p.path.startswith(("/shorts/", "/embed/")):
            video = p.path.split("/")[2]
    elif host == "youtu.be":
        video = p.path.strip("/")
    if video and re.fullmatch(r"[\w-]{11}", video):
        return "yt:" + video, "https://www.youtube.com/watch?v=" + video, "youtube"
    raise ValueError("Backup supports individual YouTube videos and your canonical TikTok posts only")


def video_source(v):
    if v.get("youtube_id"):
        return "https://www.youtube.com/watch?v=" + str(v["youtube_id"])
    return v.get("source_url") or v.get("url", "")


def load_catalog():
    raw = CATALOG.read_bytes()
    cat = json.loads(raw)
    if isinstance(cat, list):
        cat = {"videos": cat}
    seen = {}
    for v in cat["videos"]:
        seed = str(v.get("youtube_id") or v.get("url") or v.get("title"))
        seen[seed] = seen.get(seed, 0) + 1
        v.setdefault("id", hashlib.sha256((seed + ":" + str(seen[seed])).encode()).hexdigest()[:24])
    return cat, hashlib.sha256(raw).hexdigest()


def hashtags_only(title):
    """True when a title is empty apart from #hashtags and punctuation."""
    text = re.sub(r"(?<!\w)#\w+", "", str(title or ""))
    return "#" in str(title or "") and not re.search(r"\w", text)


def needs_title(video):
    title = str(video.get("title") or "").strip()
    return bool(video.get("needs_title")) or not title or hashtags_only(title) or title in ("Untitled clip", "Untitled video") or bool(
        re.fullmatch(r"Untitled(?: \d{4}-\d{2}-\d{2})?|TikTok post \d+ \(caption unavailable\)", title))


def overview(worker_alive):
    cat, _ = load_catalog()
    with db() as c:
        jobs = [dict(r) for r in c.execute("SELECT status,title FROM jobs")]
    counts = {}
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    return {
        "pi": {"state": "ok" if worker_alive else "error",
               "message": "Site and import worker are running." if worker_alive else
               "The import worker has stopped. Restart the WokeAreWe service on the Pi."},
        "needs_title": sum(needs_title(v) for v in cat["videos"]),
        "queue_needs_title": sum(needs_title(j) for j in jobs if j["status"] != "published"),
        "waiting_publish": counts.get("imported", 0),
        "failed_imports": counts.get("failed", 0),
        "active_imports": sum(counts.get(k, 0) for k in ("queued", "processing", "copying", "waiting for connection")),
        "queue_counts": counts,
        "youtube": {"state": "unavailable", "message": "YouTube watching not available yet"},
    }


def clean_catalog(cat):
    if isinstance(cat, list):
        cat = {"videos": cat}
    if not isinstance(cat, dict) or not isinstance(cat.get("videos"), list):
        raise ValueError("JSON must contain a videos array")
    if len(cat["videos"]) > 20000:
        raise ValueError("Catalogue limit exceeded")
    ids = set()
    for v in cat["videos"]:
        if not isinstance(v, dict) or not isinstance(v.get("title"), str):
            raise ValueError("Every video needs a text title")
        if not v["title"].strip():
            v["title"] = "Untitled " + time.strftime("%Y-%m-%d", time.gmtime())
            v["needs_title"] = True
        if len(v["title"]) > 1000:
            raise ValueError("Title is too long")
        for field in ("url", "thumb", "source_url"):
            val = v.get(field)
            if val and (not isinstance(val, str) or not (val.startswith("/media/") or urlparse(val).scheme in ("https", "http"))):
                raise ValueError("Video and thumbnail links must be HTTP(S) links")
        if v.get("youtube_id") and not re.fullmatch(r"[A-Za-z0-9_-]{5,100}", str(v["youtube_id"])):
            raise ValueError("Invalid YouTube ID")
        if not v.get("youtube_id") and not v.get("url"):
            raise ValueError("Every video needs a video link")
        if not isinstance(v.get("tags", []), list) or any(not isinstance(t, str) for t in v.get("tags", [])):
            raise ValueError("Hashtags must be a list of text values")
        if not isinstance(v.get("category", ""), (str, list)):
            raise ValueError("Categories must be text or a list")
        def seconds(value):
            if value in (None, ""):
                return None
            parts = str(value).split(":")
            if len(parts) > 3 or any(not re.fullmatch(r"\d+(\.\d+)?", x) for x in parts):
                raise ValueError("Clip times must be seconds, MM:SS or HH:MM:SS")
            return sum(float(x) * 60 ** i for i, x in enumerate(reversed(parts)))
        start, end = seconds(v.get("clip_start", v.get("start"))), seconds(v.get("clip_end", v.get("end")))
        if end is not None and end <= (start or 0):
            raise ValueError("Clip end must be after clip start")
        v["id"] = str(v.get("id") or uuid.uuid4().hex)
        if v["id"] in ids or len(v["id"]) > 100:
            raise ValueError("Duplicate or invalid video ID")
        ids.add(v["id"])
        for field in ("backup", "backup_url", "backup_available", "backup_status"):
            v.pop(field, None)
    return cat


def save_catalog(cat, revision):
    with LOCK:
        _, current = load_catalog()
        if revision != current:
            raise FileExistsError("The video list changed. Reload before saving to avoid overwriting another edit.")
        cat = clean_catalog(cat)
        backups = STATE / "backups"
        backups.mkdir(exist_ok=True)
        shutil.copyfile(CATALOG, backups / f"videos-{time.time_ns()}.json")
        shutil.copyfile(CATALOG, str(CATALOG) + ".bak")
        data = (json.dumps(cat, indent=2, ensure_ascii=False) + "\n").encode()
        tmp = CATALOG.with_suffix(".json.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CATALOG)
        import lite_site
        try:
            lite_site.generate(cat)
            lite_site.LAST_ERROR = ""
        except Exception:
            lite_site.LAST_ERROR = "Catalogue saved, but lite fallback generation failed. The worker will retry."
        return hashlib.sha256(data).hexdigest()


def hydrated():
    cat, rev = load_catalog()
    with db() as c:
        media = {r["source_key"]: dict(r) for r in c.execute("SELECT * FROM media")}
        matches = {r["video_id"]: r["source_key"] for r in c.execute(
            "SELECT * FROM backup_matches WHERE undone=0 ORDER BY rowid")}
    count = 0
    for v in cat["videos"]:
        v["needs_title"] = needs_title(v)
        try:
            key = source(video_source(v))[0]
        except ValueError:
            key = ""
        if key not in media and v["id"] in matches:
            key = matches[v["id"]]
        m = media.get(key)
        v["backup_available"] = bool(m)
        v["backup_status"] = "backed up" if m else "not backed up"
        v.pop("backup_url", None)
        v.pop("poster_url", None)
        if m:
            v["backup_url"] = "/media/" + key.replace(":", "-")
            if m.get("poster_key"):
                v["poster_url"] = v["backup_url"] + "/poster"
            count += 1
    return cat, rev, count


def enqueue(url, video_id=None, title="", tags=None, *, automatic=False,
            auto_publish=False, add_end_card=None, source_date=None, metadata=None):
    key, canonical, kind = source(url)
    if kind == "tiktok":
        add_end_card = False
    now = time.time()
    with LOCK, db() as c:
        if video_id is None:
            for v in load_catalog()[0]["videos"]:
                try:
                    duplicate = source(video_source(v))[0] == key
                except ValueError:
                    duplicate = False
                if duplicate:
                    return {"id": v["id"], "status": "duplicate skipped"}
        existing = c.execute("SELECT id FROM jobs WHERE source_key=?", (key,)).fetchone()
        if existing:
            eid = uuid.uuid4().hex
            c.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                      (eid, canonical, "duplicate skipped", "Already queued or backed up; original job retained", now))
            return {"id": existing["id"], "status": "duplicate skipped"}
        jid = uuid.uuid4().hex
        status = "queued" if os.environ.get("YOU2MP3_API_KEY") else "waiting for connection"
        c.execute("""INSERT INTO jobs
          (id,source_key,url,kind,video_id,title,tags,status,reason,idempotency,created,updated)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (jid, key, canonical, kind, video_id, title or "Untitled clip", json.dumps(tags or []),
                   status, "" if status == "queued" else "YOU2MP3_API_KEY is not configured",
                   "waw-" + jid, now, now))
        c.execute("""UPDATE jobs SET automatic=?,auto_publish=?,add_end_card=?,source_date=?,metadata=?
                     WHERE id=?""", (int(automatic), int(auto_publish), add_end_card, source_date,
                                    json.dumps(metadata) if metadata else None, jid))
        return {"id": jid, "status": status}


def enqueue_unsupported(v, reason):
    """Keep unsupported entries visible in the queue instead of losing batch failures."""
    jid = uuid.uuid4().hex
    now = time.time()
    with db() as c:
        existing = c.execute("SELECT id FROM jobs WHERE source_key=?", ("unsupported:" + v["id"],)).fetchone()
        if existing:
            return {"id": existing["id"], "status": "duplicate skipped"}
        c.execute("""INSERT INTO jobs
          (id,source_key,url,kind,video_id,title,tags,status,reason,idempotency,created,updated)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (jid, "unsupported:" + v["id"], video_source(v), "unsupported", v["id"],
                   v["title"], json.dumps(v.get("tags", [])), "blocked", reason, "waw-" + jid, now, now))
    return {"id": jid, "status": "blocked", "reason": reason}


def update_job(jid, **fields):
    allowed = {"status", "reason", "upstream_id", "payload", "title", "tags", "attempts", "add_end_card"}
    if not fields.keys() <= allowed:
        raise ValueError("Invalid job update")
    fields["updated"] = time.time()
    with db() as c:
        c.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                  (*fields.values(), jid))


def get_job(jid):
    with db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    if not r:
        raise LookupError("Queue item not found")
    return dict(r)


def publish(jid):
    with LOCK:
        j = get_job(jid)
        if j["status"] not in ("imported", "published"):
            raise ValueError("Only a verified imported copy can be published")
        with db() as c:
            m = c.execute("SELECT * FROM media WHERE source_key=?", (j["source_key"],)).fetchone()
        if not m:
            raise ValueError("No verified permanent copy exists")
        if m["play_status"] != "ready":
            import playback
            st = playback.state(m)
            raise ValueError("This video can't be published until its preview plays. "
                             + (st["reason"] or "It is still being checked.") + " Use Retry preview.")
        cat, revision = load_catalog()
        import back_catalog
        with db() as c:
            previous = c.execute("SELECT * FROM backup_matches WHERE job_id=?", (jid,)).fetchone()
        candidate = None if previous else back_catalog.match(j, cat["videos"])
        if candidate and not j["video_id"]:
            with db() as c:
                c.execute("INSERT INTO backup_matches VALUES(?,?,?,?,0)",
                          (jid, candidate["id"], j["source_key"], j["title"]))
            # Preserve the existing YouTube entry's title, date, and tags.
            save_catalog(cat, revision)
            update_job(jid, status="published", reason="Attached as an existing video's backup")
            return
        if previous and not previous["undone"]:
            update_job(jid, status="published", reason="Attached as an existing video's backup")
            return
        target = next((v for v in cat["videos"] if v["id"] == j["video_id"]), None)
        if target is None:
            target = next((v for v in cat["videos"] if v.get("source_url") == j["url"]), None)
        if target is None:
            target = {"id": uuid.uuid4().hex, "youtube_id": "", "url": "/media/" + j["source_key"].replace(":", "-"),
                      "source_url": j["url"], "added": j.get("source_date") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "category": [], "featured": False, "pinned": False}
            cat["videos"].append(target)
            target["title"] = j["title"]
            target["tags"] = json.loads(j["tags"])
            target["needs_title"] = needs_title(j)
        # Existing manual/merged entries keep their latest owner-edited metadata.
        save_catalog(cat, revision)
        update_job(jid, status="published", reason="")
