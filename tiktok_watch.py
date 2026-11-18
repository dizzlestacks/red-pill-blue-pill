"""Restart-safe discovery, owner policy and import admission. No TikTok scraping."""
from datetime import datetime, timezone
import json
import os
import re
import shutil
import threading
import time
from urllib.parse import urlparse
import admin_store as store
import watch_provider

CHECK_LOCK = threading.Lock()
LAST_HEARTBEAT = 0
GB = 1024 ** 3


class Paused(Exception):
    pass


def profile_owner(profile):
    p = urlparse(profile)
    if p.scheme != "https" or p.netloc not in ("www.tiktok.com", "tiktok.com"):
        raise ValueError("Use an HTTPS TikTok profile address")
    m = re.fullmatch(r"/@([A-Za-z0-9_.]{2,30})/?", p.path)
    if not m or p.query or p.fragment:
        raise ValueError("Use a TikTok profile, not a post address")
    return m[1]


def defaults():
    return dict(enabled=os.environ.get("TIKTOK_WATCH_ENABLED", "true").lower() == "true",
                profile=os.environ.get("TIKTOK_PROFILE", "https://www.tiktok.com/@wokearewe1"),
                interval_minutes=int(os.environ.get("TIKTOK_CHECK_MINUTES", "15")),
                hourly_limit=int(os.environ.get("IMPORTS_PER_HOUR", "6")),
                min_free_gb=float(os.environ.get("MIN_FREE_GB", "5")),
                auto_publish=os.environ.get("TIKTOK_AUTO_PUBLISH", "false").lower() == "true",
                add_end_card=os.environ.get("TIKTOK_ADD_END_CARD", "true").lower() == "true",
                last_check=None, last_count=0, next_check=0, error="", status="pending",
                cursor=None, history_cursor=None, history_done=False, requested="", generation=0)


def init():
    with store.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS watcher (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS import_admissions (job_id TEXT PRIMARY KEY, admitted REAL NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO watcher VALUES(1,?)", (json.dumps(defaults()),))


def settings():
    with store.db() as c:
        row = c.execute("SELECT data FROM watcher WHERE id=1").fetchone()
    return json.loads(row[0])


def owner():
    return profile_owner(settings()["profile"])


def update(**fields):
    with store.LOCK, store.db() as c:
        current = json.loads(c.execute("SELECT data FROM watcher WHERE id=1").fetchone()[0])
        current.update(fields)
        c.execute("UPDATE watcher SET data=? WHERE id=1", (json.dumps(current),))
    return current


def configure(body):
    allowed = {"enabled", "profile", "interval_minutes", "hourly_limit",
               "min_free_gb", "auto_publish", "add_end_card"}
    if not body.keys() <= allowed:
        raise ValueError("Unknown watching setting")
    for name in ("enabled", "auto_publish", "add_end_card"):
        if name in body and not isinstance(body[name], bool):
            raise ValueError(name + " must be on or off")
    for name, lo, hi in (("interval_minutes", 5, 1440), ("hourly_limit", 1, 60), ("min_free_gb", 1, 1024)):
        if name in body and (type(body[name]) not in (int, float) or not lo <= body[name] <= hi
                             or (name != "min_free_gb" and int(body[name]) != body[name])):
            raise ValueError(f"{name} must be between {lo} and {hi}")
    if "profile" in body:
        body["profile"] = "https://www.tiktok.com/@" + profile_owner(body["profile"])
    with CHECK_LOCK, store.LOCK:
        old = settings()
        if body.get("profile", old["profile"]) != old["profile"]:
            body.update(cursor=None, history_cursor=None, history_done=False)
        update(**body, next_check=0, error="", generation=old["generation"] + 1)
    return status()


def disk_guard(extra=0):
    free = shutil.disk_usage(store.STATE).free
    reserve = settings()["min_free_gb"] * GB
    if free - extra < reserve:
        raise Paused(f"Imports paused: only {free / GB:.2f} GB free; keep at least {reserve / GB:g} GB. Nothing is deleted.")
    return free


def status():
    data = settings()
    data["end_card_supported"] = False
    data["free_gb"] = round(shutil.disk_usage(store.STATE).free / GB, 2)
    try:
        disk_guard()
    except Paused as e:
        data.update(status="paused", error=str(e))
    else:
        if not data["enabled"]:
            data["status"] = "off"
        elif not os.environ.get("YOU2MP3_API_KEY"):
            data.update(status="waiting for connection", error="YOU2MP3_API_KEY is not configured")
    return data


def request(older=False):
    with store.LOCK:
        s = settings()
        if older and s["history_done"]:
            raise ValueError("All available older posts have already been checked")
        if s["requested"]:
            raise ValueError("A profile check is already pending")
        update(requested="older" if older else "now", next_check=0)
    return status()


def normalize(post, profile):
    if not isinstance(post, dict):
        raise ValueError("Profile list contains an invalid post")
    key, url, kind = store.source(post["url"])
    if kind != "tiktok" or profile_owner(profile) != url.split("/@")[1].split("/")[0]:
        raise ValueError("Profile list contains a post from another owner")
    if str(post["id"]) != key[3:]:
        raise ValueError("Profile post ID does not match its address")
    caption = post.get("caption")
    date = post.get("posted_at")
    tags = post.get("hashtags")
    if not isinstance(caption, str) or not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise ValueError("Profile post is missing caption or hashtags")
    if not isinstance(date, str):
        raise ValueError("Profile post is missing its original publication date")
    try:
        parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        date = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        raise ValueError("Profile publication dates must include a timezone")
    title = re.sub(r"(?<!\w)#[\w]+", "", caption).strip()
    title = " ".join(title.split())[:200] or caption.strip()[:200]
    if not title:
        raise ValueError("Profile post has no caption to use as a title")
    tags = list(dict.fromkeys(t.lstrip("#").strip() for t in tags + re.findall(r"#(\w+)", caption) if t.strip("# ")))
    return dict(url=url, title=title, tags=tags, source_date=date, metadata=post)


def check(older=False):
    import media_worker as worker
    if not CHECK_LOCK.acquire(blocking=False):
        return
    try:
        s = settings()
        update(last_check=time.time(), last_count=0, requested="", next_check=time.time() + s["interval_minutes"] * 60)
        if not os.environ.get("YOU2MP3_API_KEY"):
            update(status="waiting for connection", error="YOU2MP3_API_KEY is not configured")
            return
        disk_guard()
        # Capability/auth request uses the same shared rate gate as downloads.
        worker.api("/capabilities")
        cursor = s["history_cursor"] if older else s["cursor"]
        page = watch_provider.PROVIDER.page(s["profile"], cursor, 25, older=older)
        if not isinstance(page, dict) or not isinstance(page.get("posts"), list) or len(page["posts"]) > 25:
            raise ValueError("Invalid profile page; cursor retained")
        next_cursor = page.get("next_cursor")
        if next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > 4096):
            raise ValueError("Invalid profile cursor")
        if type(page.get("has_more")) is not bool or (page["has_more"] and (not next_cursor or next_cursor == cursor)):
            raise ValueError("Invalid profile pagination; cursor retained")
        posts = [normalize(p, s["profile"]) for p in page["posts"]]
        count = 0
        for post in posts:
            result = store.enqueue(**post, automatic=True, auto_publish=s["auto_publish"],
                                   add_end_card=s["add_end_card"])
            count += result["status"] != "duplicate skipped"
        fields = {"history_cursor" if older else "cursor": next_cursor}
        if older:
            fields["history_done"] = not page["has_more"]
        # Bounded one page per check, including "check now"; no unbounded backlog.
        update(**fields, last_count=count, status="watching", error="")
    except watch_provider.Unavailable:
        update(status="unsupported", error="watching not available yet")
    except Paused as e:
        update(status="paused", error=str(e))
    except worker.UpstreamError as e:
        update(status="failed", error=str(e))
    except (ValueError, KeyError, TypeError):
        update(status="failed", error="Profile list has missing or invalid metadata. Cursor retained; no posts skipped.")
    except Exception:
        update(status="failed", error="Profile check failed; cursor retained for retry.")
    finally:
        CHECK_LOCK.release()
        import durable_state
        durable_state.checkpoint()


def admit(job):
    """Reserve once per immutable job identity, BEFORE its first HTTP submit."""
    disk_guard()
    with store.LOCK, store.db() as c:
        if c.execute("SELECT 1 FROM import_admissions WHERE job_id=?", (job["id"],)).fetchone():
            return
        count = c.execute("SELECT count(*) FROM import_admissions WHERE admitted>?", (time.time() - 3600,)).fetchone()[0]
        if count >= settings()["hourly_limit"]:
            raise Paused("Imports paused: hourly import limit reached. Will resume automatically.")
        c.execute("INSERT INTO import_admissions VALUES(?,?)", (job["id"], time.time()))


def run():
    global LAST_HEARTBEAT
    while True:
        LAST_HEARTBEAT = time.time()
        try:
            s = settings()
            if (s["enabled"] or s["requested"]) and time.time() >= s["next_check"]:
                check(older=s["requested"] == "older")
        except Exception:
            update(status="failed", error="Watcher interrupted; retrying on the next scheduled check.")
        time.sleep(5)
