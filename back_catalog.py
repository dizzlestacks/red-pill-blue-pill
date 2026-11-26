"""Durable, bounded back-catalogue runs and reversible backup associations."""
import json
import re
import time
import uuid
from difflib import SequenceMatcher
import admin_store as store
import tiktok_watch as watch
import watch_provider


def init():
    with store.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS back_runs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS back_items(run_id TEXT, source_key TEXT,
          job_id TEXT, PRIMARY KEY(run_id,source_key));
        CREATE TABLE IF NOT EXISTS backup_matches(job_id TEXT PRIMARY KEY, video_id TEXT NOT NULL,
          source_key TEXT NOT NULL, title TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
        """)


def current():
    with store.db() as c:
        row = c.execute("SELECT data FROM back_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return json.loads(row[0]) if row else None


def save(run):
    with store.db() as c:
        c.execute("INSERT OR REPLACE INTO back_runs VALUES(?,?)", (run["id"], json.dumps(run)))


def status():
    run = current()
    with store.db() as c:
        matches = [dict(r) for r in c.execute("SELECT * FROM backup_matches WHERE undone=0")]
        counts = dict(c.execute("""SELECT COALESCE(j.status,'duplicate skipped'),count(*)
          FROM back_items b LEFT JOIN jobs j ON j.id=b.job_id
          WHERE b.run_id=? GROUP BY COALESCE(j.status,'duplicate skipped')""",
                               (run["id"] if run else "",)).fetchall())
    return {"run": run, "counts": counts, "matches": matches}


def estimate():
    s = watch.settings()
    page = watch_provider.PROVIDER.page(s["profile"], None, 1, older=True)
    total = page.get("inventory_count")
    if type(total) is not int or total < 0:
        raise ValueError("The service has not supplied a post count. Refresh the watcher before starting.")
    with store.db() as c:
        average = c.execute("SELECT avg(size) FROM media").fetchone()[0]
    # Clearly an estimate; transient copy reserve and the hard disk guard still apply.
    per_post = max(int(average or 100 * 1024**2), 25 * 1024**2)
    return dict(posts=total, estimated_bytes=total * per_post,
                free_bytes=watch.shutil.disk_usage(store.STATE).free,
                reserve_bytes=int(s["min_free_gb"] * watch.GB), estimated=True)


def action(operation, body):
    with watch.CHECK_LOCK, store.LOCK:
        run = current()
        if operation == "start":
            if run and run["state"] != "done":
                raise ValueError("A back-catalogue run already exists. Resume it instead.")
            run = dict(id=uuid.uuid4().hex, state="running", cursor=None, scanned=0,
                       discovery_done=False, profile=watch.settings()["profile"],
                       add_end_card=False, reason="", next_check=0)
        elif operation in ("pause", "resume"):
            if not run or run["state"] == "done":
                raise ValueError("No active back-catalogue run")
            run.update(state="paused" if operation == "pause" else "running", reason="", next_check=0)
        else:
            raise ValueError("Unknown back-catalogue action")
        save(run)
    return status()


def allowed(job):
    with store.db() as c:
        row = c.execute("""SELECT r.data FROM back_items b JOIN back_runs r ON r.id=b.run_id
          WHERE b.job_id=? ORDER BY r.rowid DESC LIMIT 1""", (job["id"],)).fetchone()
    return not row or json.loads(row[0])["state"] not in ("paused", "disk paused")


def tick():
    if not watch.CHECK_LOCK.acquire(False):
        return
    run = None
    try:
        run = current()
        if not run or run["state"] in ("done", "paused") or time.time() < run["next_check"]:
            return
        watch.disk_guard()
        run.update(state="running", reason="")
        if run["profile"] != watch.settings()["profile"]:
            run.update(state="paused", reason="Watched profile changed. Restore the original profile before resuming.")
            return
        with store.db() as c:
            # Retry failures with bounded exponential delay; reuse the immutable upstream
            # request identity rather than silently submitting duplicate paid jobs.
            for j in c.execute("""SELECT j.* FROM jobs j JOIN back_items b ON b.job_id=j.id
                    WHERE b.run_id=? AND j.status IN ('failed','blocked')""", (run["id"],)).fetchall():
                delay = min(3600, 60 * 2**min(j["attempts"], 6))
                if time.time() - j["updated"] >= delay:
                    c.execute("UPDATE jobs SET status='queued',attempts=attempts+1,updated=? WHERE id=?",
                              (time.time(), j["id"]))
        if not run["discovery_done"]:
            page = watch_provider.PROVIDER.page(run["profile"], run["cursor"], 25, older=True)
            if not isinstance(page.get("posts"), list) or len(page["posts"]) > 25:
                raise ValueError("Invalid profile page; retrying without skipping posts.")
            if type(page.get("has_more")) is not bool or (page["has_more"] and
                    (not page.get("next_cursor") or page["next_cursor"] == run["cursor"])):
                raise ValueError("Invalid pagination; cursor retained.")
            posts = [watch.normalize(p, run["profile"]) for p in page["posts"]]
            import captions
            captions.remember(page["posts"])
            for post in posts:
                watch.disk_guard()
                result = store.enqueue(**post, automatic=True,
                                       auto_publish=watch.settings()["auto_publish"],
                                       add_end_card=False)
                key = store.source(post["url"])[0]
                with store.db() as c:
                    c.execute("INSERT OR IGNORE INTO back_items VALUES(?,?,?)",
                              (run["id"], key, result["id"]))
            run.update(cursor=page.get("next_cursor"), discovery_done=not page["has_more"])
        info = status()
        run["scanned"] = sum(info["counts"].values())
        unfinished = sum(n for state, n in info["counts"].items()
                         if state not in ("imported", "published", "duplicate skipped"))
        if run["discovery_done"] and unfinished == 0:
            run["state"] = "done"
        run["next_check"] = time.time() + 5
    except watch.Paused as e:
        run.update(state="disk paused", reason=str(e), next_check=time.time()+30)
    except Exception as e:
        if run:
            run.update(reason=str(e) if isinstance(e, (ValueError, watch_provider.Unavailable)) else
                       "Connection unavailable. The run will retry automatically.", next_check=time.time()+60)
    finally:
        if run:
            save(run)
        watch.CHECK_LOCK.release()


def normalized(text):
    return " ".join(re.sub(r"#[\w]+", "", text).lower().split()).strip(" .!?")


def match(job, videos):
    title = normalized(job["title"])
    if len(title) < 16 or "caption unavailable" in title:
        return None
    candidates = [v for v in videos if v.get("youtube_id") and
                  SequenceMatcher(None, title, normalized(v["title"])).ratio() >= .96]
    # Ambiguous matches are never attached automatically.
    return candidates[0] if len(candidates) == 1 else None


def undo(jid):
    with store.LOCK, store.db() as c:
        row = c.execute("SELECT * FROM backup_matches WHERE job_id=? AND undone=0", (jid,)).fetchone()
        if not row:
            raise ValueError("Match not found")
        c.execute("UPDATE backup_matches SET undone=1 WHERE job_id=?", (jid,))
        c.execute("UPDATE jobs SET status='imported',automatic=0,auto_publish=0,video_id=NULL WHERE id=?", (jid,))
    return status()
