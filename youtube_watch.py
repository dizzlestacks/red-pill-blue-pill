"""YouTube channels and playlists watched through you2mp3 (documented /youtube-watch API only).

The owner chooses which channels and playlists are watched on the you2mp3
developers page; the Pi's connection key can only read them. The Pi pages
through /youtube-watch/videos with a change cursor and keeps one record per
YouTube video ID, so a video listed by several sources appears once.

For each video the owner can Add (hub entry that plays from YouTube, plus a
copy request through /youtube-watch/imports) or Decline (remembered). A source
can be set to add new videos automatically. Removed or declined videos are never
brought back automatically.
"""
import json
import threading
import time
import uuid

import admin_store as store

CHECK_LOCK = threading.Lock()
LAST_HEARTBEAT = 0
PAGE = 100
NOT_AVAILABLE = "YouTube watching isn't switched on for this you2mp3 connection yet."


def init():
    with store.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS yt_watch (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS yt_videos (video_id TEXT PRIMARY KEY, data TEXT NOT NULL,
            decision TEXT NOT NULL DEFAULT '', seen REAL NOT NULL, changed REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS yt_sources (id TEXT PRIMARY KEY, auto_add INTEGER NOT NULL DEFAULT 0);
        """)
        c.execute("INSERT OR IGNORE INTO yt_watch VALUES(1,?)", (json.dumps(dict(
            enabled=True, interval_minutes=30, cursor=None, next_check=0, last_check=None, status="pending",
            error="", sources=[], limits={}, requested=False, baseline_done=False)),))


def settings():
    with store.db() as c:
        return json.loads(c.execute("SELECT data FROM yt_watch WHERE id=1").fetchone()[0])


def update(**fields):
    with store.LOCK, store.db() as c:
        data = json.loads(c.execute("SELECT data FROM yt_watch WHERE id=1").fetchone()[0])
        data.update(fields)
        c.execute("UPDATE yt_watch SET data=? WHERE id=1", (json.dumps(data),))
    return data


def _source_id(source):
    return str(source.get("id") or source.get("sourceId") or "")


def _clean_source(s):
    keep = ("id", "kind", "type", "title", "name", "url", "lastSuccess", "lastError", "warning", "lastCheckComplete",
            "videoCount", "enabled")
    out = {k: s.get(k) for k in keep if k in s}
    out["id"] = _source_id(s)
    return out


def _clean_video(v):
    keep = ("videoId", "url", "title", "channel", "channelId", "publishedAt", "durationSeconds", "thumbnail",
            "availability", "unavailable", "sources", "watched", "latestImport")
    out = {k: v.get(k) for k in keep if k in v}
    thumb = out.get("thumbnail")
    if not (isinstance(thumb, str) and thumb.startswith("https://") and len(thumb) < 2000):
        out["thumbnail"] = "https://i.ytimg.com/vi/" + v["videoId"] + "/hqdefault.jpg"
    return out


def _video_sources(v):
    ids = []
    for s in v.get("sources") or []:
        sid = _source_id(s) if isinstance(s, dict) else str(s)
        if sid:
            ids.append(sid)
    return ids


def check():
    """One pass: refresh sources, then up to five pages of changed videos."""
    import media_worker as worker
    import os
    if not CHECK_LOCK.acquire(blocking=False):
        return
    try:
        s = settings()
        update(last_check=time.time(), requested=False, next_check=time.time() + s["interval_minutes"] * 60)
        if not os.environ.get("YOU2MP3_API_KEY"):
            update(status="waiting for connection", error="The you2mp3 connection key isn't set on the Pi.")
            return
        try:
            info = worker.api("/youtube-watch")
        except worker.UpstreamError as e:
            if e.status in (403, 404, 501):
                update(status="unsupported", error=NOT_AVAILABLE)
                return
            raise
        sources = [_clean_source(x) for x in info.get("sources") or [] if isinstance(x, dict)]
        update(sources=sources, limits=info.get("limits") or {}, service_status=info.get("status"))
        first_pass = not s["baseline_done"]
        cursor = s["cursor"]
        for _ in range(5):
            query = f"/youtube-watch/videos?limit={PAGE}" + (f"&after={cursor}" if cursor is not None else "")
            page = worker.api(query)
            videos = page.get("videos")
            if not isinstance(videos, list):
                raise ValueError("you2mp3 sent an unexpected video list; nothing was skipped.")
            if page.get("sources"):
                update(sources=[_clean_source(x) for x in page["sources"] if isinstance(x, dict)])
            record(videos, first_pass)
            cursor = page.get("nextCursor", cursor)
            update(cursor=cursor)
            if not page.get("hasMore"):
                break
        else:
            update(next_check=time.time() + 5)  # More to read: continue shortly.
            first_pass = False
        if first_pass:
            update(baseline_done=True)
        update(status="watching", error="")
    except worker.UpstreamError as e:
        update(status="failed", error=str(e))
    except ValueError as e:
        update(status="failed", error=str(e))
    except Exception:
        update(status="failed", error="Couldn't check YouTube sources; will try again.")
    finally:
        CHECK_LOCK.release()
        import durable_state
        durable_state.checkpoint()


def record(videos, baseline):
    """Save each changed video; add automatically where its source says so (never on the first pass)."""
    with store.db() as c:
        auto = {r["id"] for r in c.execute("SELECT id FROM yt_sources WHERE auto_add=1")}
    for v in videos:
        if not isinstance(v, dict) or not isinstance(v.get("videoId"), str) or not 6 <= len(v["videoId"]) <= 20:
            continue
        data = _clean_video(v)
        now = time.time()
        with store.db() as c:
            old = c.execute("SELECT decision FROM yt_videos WHERE video_id=?", (v["videoId"],)).fetchone()
            c.execute("""INSERT INTO yt_videos(video_id,data,decision,seen,changed) VALUES(?,?,?,?,?)
                         ON CONFLICT(video_id) DO UPDATE SET data=excluded.data, changed=excluded.changed""",
                      (v["videoId"], json.dumps(data), "baseline" if baseline else "", now, now))
        decided = old and old["decision"] not in ("", "baseline")
        if not decided and not baseline and not data.get("unavailable") and set(_video_sources(data)) & auto:
            try:
                add(v["videoId"], automatic=True)
            except (ValueError, LookupError):
                pass


def in_hub(video_id):
    cat, _ = store.load_catalog()
    return next((v for v in cat["videos"] if v.get("youtube_id") == video_id), None)


def add(video_id, automatic=False, keep_copy=True):
    """Add to the hub (plays from YouTube straight away) and ask you2mp3 for a copy."""
    with store.db() as c:
        row = c.execute("SELECT * FROM yt_videos WHERE video_id=?", (video_id,)).fetchone()
        if automatic and c.execute("SELECT 1 FROM removed_sources WHERE source_key=?", ("yt:" + video_id,)).fetchone():
            return {"status": "removed by owner"}
    if not row:
        raise LookupError("Video not found")
    data = json.loads(row["data"])
    url = "https://www.youtube.com/watch?v=" + video_id
    with store.LOCK:
        cat, rev = store.load_catalog()
        existing = next((v for v in cat["videos"] if v.get("youtube_id") == video_id), None)
        if existing:
            vid = existing["id"]
        else:
            vid = uuid.uuid4().hex
            title = str(data.get("title") or "").strip()[:300]
            added = str(data.get("publishedAt") or "") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            video = {"id": vid, "title": title or "Untitled " + added[:10], "youtube_id": video_id, "source_url": url,
                     "url": "", "category": [], "tags": [], "added": added, "featured": False, "pinned": False,
                     "source": "youtube"}
            video["needs_title"] = store.needs_title(video)
            cat["videos"].append(video)
            store.save_catalog(cat, rev)
    with store.db() as c:
        c.execute("UPDATE yt_videos SET decision='added' WHERE video_id=?", (video_id,))
    result = {"status": "added", "video_id": vid}
    if keep_copy and not data.get("unavailable"):
        result["copy"] = store.enqueue(url, vid, data.get("title") or "", [], automatic=automatic,
                                       source_date=data.get("publishedAt"),
                                       metadata={"youtube_watch": True, "thumbnail": data.get("thumbnail")})
    return result


def decline(video_id):
    with store.db() as c:
        if not c.execute("UPDATE yt_videos SET decision='declined' WHERE video_id=?", (video_id,)).rowcount:
            raise LookupError("Video not found")
    return {"ok": True}


def undecline(video_id):
    with store.db() as c:
        c.execute("UPDATE yt_videos SET decision='' WHERE video_id=? AND decision='declined'", (video_id,))
    return {"ok": True}


def set_auto(source_id, on):
    if not isinstance(on, bool) or not isinstance(source_id, str) or not source_id or len(source_id) > 200:
        raise ValueError("Choose a source and on or off")
    with store.db() as c:
        c.execute("INSERT INTO yt_sources VALUES(?,?) ON CONFLICT(id) DO UPDATE SET auto_add=excluded.auto_add",
                  (source_id, int(on)))
    return status()


def configure(body):
    if not body.keys() <= {"enabled", "interval_minutes"}:
        raise ValueError("Unknown YouTube watching setting")
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise ValueError("enabled must be on or off")
    if "interval_minutes" in body and (type(body["interval_minutes"]) is not int or not 10 <= body["interval_minutes"] <= 1440):
        raise ValueError("Check every 10 to 1440 minutes")
    update(**body, next_check=0)
    return status()


def request_check():
    update(requested=True, next_check=0)
    return status()


def status(view="new", limit=60):
    s = settings()
    with store.db() as c:
        auto = {r["id"]: bool(r["auto_add"]) for r in c.execute("SELECT * FROM yt_sources")}
        where = {"new": "decision=''", "earlier": "decision='baseline'", "declined": "decision='declined'", "added": "decision='added'",
                 "all": "1=1"}.get(view, "decision=''")
        rows = [dict(r) for r in c.execute(f"SELECT * FROM yt_videos WHERE {where} ORDER BY changed DESC LIMIT ?", (limit,))]
        counts = {r["decision"] or "new": r["n"] for r in c.execute("SELECT decision, count(*) n FROM yt_videos GROUP BY decision")}
    hub = {v.get("youtube_id") for v in store.load_catalog()[0]["videos"] if v.get("youtube_id")}
    videos = []
    for r in rows:
        d = json.loads(r["data"])
        d.update(decision=r["decision"] or "new", in_hub=r["video_id"] in hub)
        videos.append(d)
    sources = [dict(x, auto_add=auto.get(x["id"], False)) for x in s.get("sources", [])]
    return {k: s.get(k) for k in ("enabled", "interval_minutes", "last_check", "next_check", "status", "error", "limits")} | {
        "sources": sources, "videos": videos, "counts": counts, "view": view}


def run():
    global LAST_HEARTBEAT
    while True:
        LAST_HEARTBEAT = time.time()
        try:
            s = settings()
            if (s["enabled"] or s["requested"]) and time.time() >= s["next_check"]:
                check()
        except Exception:
            pass
        time.sleep(10)
