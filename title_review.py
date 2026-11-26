"""Review titles: compare each hub video's title with its TikTok caption.

Nothing here changes a title on its own; every change is the owner's tap.
"""
import json
import threading
import time
import admin_store as store
import captions
import thumbs

REFRESH = {"running": False, "pages": 0, "posts": 0, "error": "", "finished": None}
_refresh_lock = threading.Lock()


def _matches():
    with store.db() as c:
        return {r["video_id"]: r["source_key"] for r in c.execute(
            "SELECT * FROM backup_matches WHERE undone=0 ORDER BY rowid")}


def _post_for(v, matches, jobs):
    keys = [k for k in store.video_keys(v) if k.startswith("tt:")]
    if str(matches.get(v.get("id"), "")).startswith("tt:"):
        keys.append(matches[v["id"]])
    for key in keys:
        p = captions.post(key)
        if not p and key in jobs:
            p = captions.for_job(jobs[key])
        if p and p.get("caption") is not None:
            return key, p
    return None, None


def items():
    cat, _ = store.load_catalog()
    matches = _matches()
    with store.db() as c:
        jobs = {r["source_key"]: dict(r) for r in c.execute("SELECT * FROM jobs WHERE kind='tiktok'")}
        decided = {r["video_id"]: r["decision"] for r in c.execute("SELECT * FROM title_reviews")}
    out = []
    for v in cat["videos"]:
        key, p = _post_for(v, matches, jobs)
        if not key:
            continue
        words = captions.caption_words(p.get("caption"))
        cover = p.get("cover_text") or ""
        suggestion = words if captions.usable(words) else cover
        title = str(v.get("title") or "")
        n = thumbs.name(key)
        out.append({"id": v["id"], "title": title, "caption": p.get("caption") or "",
                    "suggestion": suggestion, "origin": "caption" if captions.usable(words) else ("cover" if cover else "none"),
                    "cover_text": cover, "same": bool(suggestion) and suggestion == title.strip(),
                    "decision": decided.get(v["id"]), "needs_title": store.needs_title(v),
                    "thumb": "/thumbs/" + n if n and thumbs.find(n) else None,
                    "tiktok_url": p.get("url") or (v.get("source_url") if "tiktok.com" in str(v.get("source_url") or "") else None)})
    out.sort(key=lambda i: (i["same"] or i["decision"] == "keep", not i["needs_title"]))
    return out


def summary():
    rows = items()
    return {"items": rows, "to_review": sum(1 for r in rows if not r["same"] and r["decision"] != "keep" and r["suggestion"]),
            "known_posts": _count_posts(), "refresh": dict(REFRESH), "thumbs": thumbs.status(),
            "ocr": captions.ocr_available()}


def _count_posts():
    with store.db() as c:
        return c.execute("SELECT count(*) FROM tiktok_posts").fetchone()[0]


def apply(body):
    """use: take the caption (or cover text) title; keep: leave the title; set: the owner's own words."""
    action, ids = body.get("action"), body.get("ids")
    if action not in ("use", "keep", "set"):
        raise ValueError("Choose use, keep or set")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 5000 or any(not isinstance(i, str) for i in ids):
        raise ValueError("Pick at least one video")
    typed = body.get("title")
    if action == "set":
        if len(ids) != 1 or not isinstance(typed, str) or not typed.strip() or len(typed) > 1000:
            raise ValueError("Type a title for this video")
        typed = " ".join(typed.split())
    wanted = set(ids)
    by_id = {i["id"]: i for i in items() if i["id"] in wanted}
    changed = 0
    with store.LOCK:
        cat, rev = store.load_catalog()
        for v in cat["videos"]:
            if v.get("id") not in wanted:
                continue
            if action == "set":
                v["title"], v["needs_title"] = typed, False
                changed += 1
            elif action == "use":
                row = by_id.get(v["id"])
                if not row or not row["suggestion"]:
                    continue
                v["title"], v["needs_title"] = row["suggestion"], False
                tags = list(v.get("tags") or []) if isinstance(v.get("tags"), list) else []
                v["tags"] = list(dict.fromkeys(tags + captions.caption_tags(row["caption"])))
                changed += 1
        if changed:
            store.save_catalog(cat, rev)
        with store.db() as c:
            for i in ids:
                c.execute("INSERT INTO title_reviews VALUES(?,?,?) ON CONFLICT(video_id) DO UPDATE SET "
                          "decision=excluded.decision, decided=excluded.decided", (i, action, time.time()))
    return {"ok": True, "changed": changed}


def refresh():
    """Read the whole TikTok post list once (captions for matching), then read cover text where captions have no words."""
    if not _refresh_lock.acquire(blocking=False):
        return dict(REFRESH)
    REFRESH.update(running=True, pages=0, posts=0, error="")

    def work():
        try:
            import tiktok_watch
            import watch_provider
            profile = tiktok_watch.settings()["profile"]
            cursor = None
            for _ in range(200):
                page = watch_provider.PROVIDER.page(profile, cursor, 100, older=True)
                captions.remember(page["posts"])
                REFRESH["pages"] += 1
                REFRESH["posts"] += len(page["posts"])
                if not page["has_more"] or page["next_cursor"] == cursor:
                    break
                cursor = page["next_cursor"]
        except Exception as e:
            REFRESH["error"] = str(e) if isinstance(e, ValueError) else "The TikTok post list couldn't be read. Try again later."
        try:
            read_covers()
        except Exception:
            pass
        REFRESH.update(running=False, finished=time.time())
        _refresh_lock.release()
    threading.Thread(target=work, daemon=True, name="title-refresh").start()
    return dict(REFRESH)


def read_covers(limit=500):
    """Cover text for hub videos whose caption has no words (needs a saved cover picture)."""
    if not captions.ocr_available():
        return 0
    with store.db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM tiktok_posts WHERE cover_text IS NULL")]
    done = 0
    for r in rows[:limit]:
        if captions.usable(captions.caption_words(r["caption"])):
            continue
        key = "tt:" + r["post_id"]
        path = thumbs.path_for(key)
        if not path or not path.exists():
            thumbs.make(key, r["thumbnail"] if str(r["thumbnail"] or "").startswith("https://") else None)
        if path and path.exists():
            captions.set_cover_text(key, captions.ocr_title(path))
            done += 1
    return done
