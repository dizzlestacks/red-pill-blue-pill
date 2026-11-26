"""Import queue: plain-language cards, removing items, retries and cancelling at you2mp3.

Removing never waits on the network. Cancelling an open you2mp3 request is
recorded and done by the worker (retried if you2mp3 can't be reached), so
Remove works even when the state at you2mp3 is uncertain.
"""
import json
import re
import time
import uuid

import admin_store as store

YT_IMPORT = "youtube-import"
YT_OFF = "YouTube copies are not switched on for this connection yet."
CHECKING = "Checking with you2mp3 whether this download started."
UNCONFIRMED = "you2mp3 couldn't confirm whether this download started. Retry starts a fresh download."

# Messages saved by earlier releases, in plain words.
LEGACY = [
    ("Upstream request outcome is uncertain", UNCONFIRMED),
    ("Idempotency receipt expired", UNCONFIRMED),
    ("This connection is TikTok-only", YT_OFF),
    ("YOU2MP3_API_KEY is not configured", "The you2mp3 connection key isn't set on the Pi (YOU2MP3_API_KEY)."),
    ("Connection key expired or invalid", "you2mp3 refused the connection key. Check YOU2MP3_API_KEY on the Pi."),
    ("Connection does not permit this operation", "This you2mp3 connection isn't allowed to do this."),
    ("Account download allowance or plan limit reached", "Your you2mp3 download allowance for this month is used up."),
    ("Account rate limit reached", "you2mp3 asked the Pi to slow down. It will try again shortly."),
    ("Connection timed out or unavailable", "Couldn't reach you2mp3. It will try again shortly."),
    ("Submitting source", "Sending the request to you2mp3."),
    ("Verifying video and copying to permanent storage", "Copying the video to the Pi."),
    ("Waiting for confirmed end-card output readiness", "Waiting for you2mp3 to finish the end card."),
    ("Waiting for the final end-card output", "Waiting for you2mp3 to finish the end card."),
    ("Only individual YouTube videos and your canonical TikTok posts",
     "Only single YouTube videos and your own TikTok posts can be copied."),
]

GROUPS = {"imported": "review", "failed": "failed", "blocked": "needhelp", "waiting for connection": "needhelp",
          "problem": "needhelp", "published": "done", "duplicate skipped": "done"}


def plain(reason):
    reason = str(reason or "").strip()
    for start, text in LEGACY:
        if reason.startswith(start):
            return text
    if reason.startswith("Media service: "):
        return "you2mp3 is preparing it."
    return reason


def site_keys():
    """Saved copies the public hub uses (directly or through a confirmed match)."""
    cat, _ = store.load_catalog()
    keys = set()
    for v in cat["videos"]:
        keys.update(store.video_keys(v))
    ids = {v["id"] for v in cat["videos"]}
    with store.db() as c:
        for r in c.execute("SELECT * FROM backup_matches WHERE undone=0"):
            if r["video_id"] in ids:
                keys.add(r["source_key"])
    return keys, ids


def thumbnail(job):
    if job.get("kind") == "youtube" and str(job.get("source_key", "")).startswith("yt:"):
        return "https://i.ytimg.com/vi/" + job["source_key"][3:] + "/hqdefault.jpg"
    try:
        meta = json.loads(job.get("metadata") or "null") or {}
    except ValueError:
        meta = {}
    url = meta.get("thumbnail") if isinstance(meta, dict) else None
    return url if isinstance(url, str) and url.startswith("https://") and len(url) < 2000 else None


def card(job, media, on_site, in_hub):
    """One status, one reason and the sensible actions for a queue item."""
    status, reason = job["status"], plain(job.get("reason"))
    actions = []
    copy = bool(media) and status in ("imported", "published", "problem")
    not_yet = "Not downloaded yet"
    if status == "queued":
        label, line = "Waiting to start", reason or "It starts when the ones before it finish."
    elif status == "waiting for connection":
        label, line = "Needs setup", reason or "The you2mp3 connection key isn't set on the Pi (YOU2MP3_API_KEY)."
        actions.append("retry")
    elif status == "blocked":
        if reason == YT_OFF:
            label = "Needs setup"
            line = YT_OFF + (" It's in your hub and plays from YouTube." if in_hub else " You can add it to the hub as a YouTube-only video now.")
            if not in_hub:
                actions.append("addhub")
            actions.append("retry")
        elif reason == CHECKING:
            label, line = "Checking", CHECKING + " This happens automatically."
        elif reason.startswith("Imports paused"):
            label, line = "Paused", reason
        else:
            label, line = "Needs setup", reason
            actions.append("retry")
    elif status == "processing":
        label, line = "Downloading", reason or "you2mp3 is preparing it."
    elif status == "copying":
        label, line = "Saving", reason or "Copying the video to the Pi."
    elif status == "failed":
        label, line = "Failed", reason or "The download didn't finish."
        actions.append("retry")
        if job.get("kind") == "youtube" and not in_hub:
            actions.append("addhub")
    elif status == "problem":
        label, line = "Problem", reason
        actions.append("redownload")
    elif status == "imported":
        label, line = "Ready to publish", "Saved and checked. Review the title and hashtags, then publish."
        actions.append("publish")
    elif status == "published":
        label, line = "Published", "In your hub."
    else:
        label, line = status.capitalize(), reason
    if not copy and status not in ("published",):
        line = f"{not_yet}: {line[0].lower() + line[1:]}" if line and not line.startswith(("you2mp3", "YouTube")) else f"{not_yet}. {line}"
    actions.append("remove")
    return {"label": label, "line": line, "actions": actions, "group": "active" if reason == CHECKING else GROUPS.get(status, "active"),
            "has_copy": copy, "thumbnail": thumbnail(job), "on_site": on_site}


def describe_removal(job, on_site, media):
    """What Remove will do, in plain words (shown in the confirmation)."""
    parts = []
    if job["status"] == "published" or on_site:
        parts.append("This only clears it from the import list. The video stays in your hub with its saved copy.")
    else:
        parts.append("It's removed from the import list.")
        if media:
            parts.append("Its saved file on the Pi is deleted, and its NAS copy moves to \"Removed from site\".")
    if job.get("upstream_id") and job["status"] not in ("imported", "published"):
        parts.append("Its download at you2mp3 is cancelled.")
    if job.get("kind") == "tiktok":
        parts.append("The automatic watcher won't bring this post back; you can still add it again by hand.")
    elif job.get("kind") == "youtube":
        parts.append("Watched channels and playlists won't bring it back; you can still add it again by hand.")
    return " ".join(parts)


def _delete_objects(c, media):
    import media_worker
    client = media_worker.storage_client()
    keys = {media.get("object_key"), media.get("poster_key"), media.get("play_key")}
    if media.get("object_key"):
        keys.add(media["object_key"] + ".jpg")
    for key in filter(None, keys):
        if c.execute("SELECT 1 FROM media WHERE source_key!=? AND (object_key=? OR poster_key=? OR play_key=?)",
                     (media["source_key"], key, key, key)).fetchone():
            continue  # Shared with another saved video.
        try:
            client.delete(key)
        except Exception:
            pass  # Already gone; the database no longer points at it.


def schedule_cancel(c, job):
    if not job.get("upstream_id") or job["status"] in ("imported", "published"):
        return
    if job.get("upstream_kind") == YT_IMPORT:
        path = f"/youtube-watch/imports/{int(job['upstream_id'])}/cancel"
    else:
        path = f"/videos/{int(job['upstream_id'])}/cancel"
    c.execute("INSERT OR IGNORE INTO pending_cancels VALUES(?,?,0,?,?)", (path, path, 0, time.time()))


def remove(item_id):
    with store.LOCK:
        with store.db() as c:
            job = c.execute("SELECT * FROM jobs WHERE id=?", (item_id,)).fetchone()
            if not job:
                if c.execute("DELETE FROM events WHERE id=?", (item_id,)).rowcount:
                    return {"removed": True}
                raise LookupError("Queue item not found")
        job = dict(job)
        keys, _ = site_keys()
        on_site = job["source_key"] in keys
        with store.db() as c:
            media = c.execute("SELECT * FROM media WHERE source_key=?", (job["source_key"],)).fetchone()
            schedule_cancel(c, job)
            if media and not on_site:
                _delete_objects(c, dict(media))
                c.execute("DELETE FROM media WHERE source_key=?", (job["source_key"],))
                c.execute("DELETE FROM backup_matches WHERE job_id=?", (job["id"],))
            c.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
            if not job["source_key"].startswith("unsupported:"):
                c.execute("INSERT OR REPLACE INTO removed_sources VALUES(?,?,?,?)",
                          (job["source_key"], job["url"], job["title"], time.time()))
    import nas_library
    nas_library.wake()
    return {"removed": True}


def remove_all(group):
    if group not in ("needhelp", "failed"):
        raise ValueError("Remove all works for the Need help and Failed lists")
    with store.db() as c:
        rows = [dict(r) for r in c.execute("SELECT id,status,reason FROM jobs")]
    removed = 0
    for r in rows:
        g = "active" if r["reason"] == CHECKING else GROUPS.get(r["status"], "active")
        if g == group or (group == "needhelp" and g == "failed"):
            try:
                remove(r["id"])
                removed += 1
            except LookupError:
                pass
    return {"removed": removed}


def retry(job):
    import media_worker as worker
    if job["status"] not in ("failed", "blocked", "waiting for connection"):
        raise ValueError("Only items that need help or failed can be retried")
    fresh = bool(job.get("fresh_retry"))
    if not fresh and job["status"] == "failed" and job.get("upstream_id"):
        try:
            if job.get("upstream_kind") == YT_IMPORT:
                worker.api(f"/youtube-watch/imports/{int(job['upstream_id'])}/retry", {})
            else:
                upstream = worker.api("/videos/" + str(job["upstream_id"]))
                if upstream.get("status") in ("error", "cancelled"):
                    worker.api("/videos/" + str(job["upstream_id"]) + "/retry", {})
        except worker.UpstreamError as e:
            if e.status not in (400, 404, 409, 410):
                raise
            fresh = True  # you2mp3 won't retry that request: ask for a new one.
    if fresh:
        store.update_job(job["id"], idempotency="waw-" + uuid.uuid4().hex, payload=None, upstream_id=None,
                         upstream_kind=None, fresh_retry=0)
    store.update_job(job["id"], status="queued", reason="", uncertain_checks=0)


def add_to_hub(job):
    """Add a YouTube link to the hub straight away (plays from YouTube) while its copy waits."""
    if job.get("kind") != "youtube":
        raise ValueError("Only YouTube links can be added as YouTube-only videos")
    with store.LOCK:
        cat, rev = store.load_catalog()
        keys, ids = site_keys()
        if job["source_key"] in keys:
            return {"ok": True, "already": True}
        vid = job.get("video_id") if job.get("video_id") and job["video_id"] not in ids else uuid.uuid4().hex
        added = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        title = (job.get("title") or "").strip()
        video = {"id": vid, "title": title or "Untitled " + added[:10], "youtube_id": job["source_key"][3:],
                 "source_url": job["url"], "url": "", "category": [], "tags": json.loads(job.get("tags") or "[]"),
                 "added": added, "featured": False, "pinned": False}
        video["needs_title"] = store.needs_title(video)
        cat["videos"].append(video)
        store.save_catalog(cat, rev)
        with store.db() as c:
            c.execute("UPDATE jobs SET video_id=? WHERE id=?", (vid, job["id"]))
    return {"ok": True}


def run_cancels():
    """One pending cancel per call (the worker calls this between jobs)."""
    import media_worker as worker
    with store.db() as c:
        row = c.execute("SELECT * FROM pending_cancels WHERE next_try<=? ORDER BY next_try LIMIT 1", (time.time(),)).fetchone()
    if not row:
        return
    row = dict(row)
    done = False
    try:
        worker.api(row["path"], {})
        done = True
    except worker.UpstreamError as e:
        if e.status in (400, 404, 410):
            done = True  # Nothing left to cancel.
        elif e.status == 409 and "/youtube-watch/imports/" in row["path"]:
            # Already started: cancel the running download job instead.
            done = True
            try:
                number = int(re.search(r"/imports/(\d+)/", row["path"]).group(1))
                data = worker.api(f"/youtube-watch/imports/{number}")
                item = data.get("import") if isinstance(data.get("import"), dict) else data
                job_id = item.get("jobId") or (item.get("job") or {}).get("id")
                if type(job_id) is int and item.get("status") not in ("done", "error", "rejected", "revoked", "cancelled"):
                    path = f"/videos/{job_id}/cancel"
                    with store.db() as c:
                        c.execute("INSERT OR IGNORE INTO pending_cancels VALUES(?,?,0,?,?)", (path, path, 0, time.time()))
            except Exception:
                pass
        elif e.status == 409:
            done = True  # Finished or already cancelled.
    except Exception:
        pass
    with store.db() as c:
        if done or row["tries"] >= 12:
            c.execute("DELETE FROM pending_cancels WHERE id=?", (row["id"],))
        else:
            c.execute("UPDATE pending_cancels SET tries=tries+1, next_try=? WHERE id=?",
                      (time.time() + min(3600, 60 * 2 ** row["tries"]), row["id"]))
