"""TikTok captions become titles: tidy only, never reworded.

The words of a caption are the title and its hashtags are tags. A caption with
no words (empty or hashtags only) falls back to the text printed on the cover
image, offered as a suggestion that needs review, then to "Untitled <date>".
"""
import json
import re
import shutil
import subprocess
import time
import admin_store as store

HASHTAG = re.compile(r"(?<!\w)#(\w+)")
MAX_TITLE = 100


def tidy(text, limit=MAX_TITLE):
    """Trim spaces and cut long text at a word boundary. Capitalisation and wording are kept."""
    text = " ".join(str(text or "").split())
    # Separators left dangling once hashtags are moved to tags ("Ruth and Naomi -").
    text = re.sub(r"(?:\s*[-–—|·•,;:]+)+$", "", text).strip()
    text = re.sub(r"^[-–—|·•,;:]+\s*", "", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space >= limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:-–—|·•.") + "…"


def caption_tags(caption, extra=()):
    tags = list(extra or []) + HASHTAG.findall(str(caption or ""))
    return list(dict.fromkeys(t.lstrip("#").strip() for t in tags if isinstance(t, str) and t.strip("# ")))


# Display names can have spaces ("@WOKE ARE WE"): up to three words when only hashtags follow.
REPLY = re.compile(r"^\s*Replying to @(?:[^\s#@]+(?:[ \t]+[^\s#@]+){0,2}(?=\s*(?:#|$))|\S+)", re.I)


def caption_words(caption):
    """The caption without hashtags or TikTok's "Replying to @name" label."""
    text = REPLY.sub(" ", str(caption or ""), count=1)
    return tidy(HASHTAG.sub(" ", text))


def usable(text):
    return bool(re.search(r"[^\W\d_]", text or "")) or bool(re.search(r"\w{2,}", text or ""))


def untitled(date=None):
    return "Untitled " + ((date or "")[:10] or time.strftime("%Y-%m-%d", time.gmtime()))


def ocr_available():
    return bool(shutil.which("tesseract"))


def clean_ocr(raw):
    """Keep the cover's title lines: real words, no stray symbols from the picture.

    Cover titles are usually in capitals; when they are, scraps the reader
    picked out of the video frame ("sYd", "Aot") are mixed case and dropped.
    """
    candidates = []
    for line in str(raw or "").splitlines():
        line = " ".join(line.split()).strip(" |_~=*`'\"-")
        words = re.findall(r"[A-Za-z][A-Za-z'’]+", line)
        letters = sum(ch.isalpha() for ch in line)
        if len(words) < 1 or letters < 3 or letters < 0.6 * len(line.replace(" ", "")):
            continue
        if len(words) == 1 and len(words[0]) < 3:
            continue
        candidates.append((line, words))
    upper = lambda text: sum(ch.isupper() for ch in text) / max(1, sum(ch.isalpha() for ch in text))
    main = "".join(line for line, words in candidates if len(words) >= 2)
    if main and upper(main) >= 0.7:
        candidates = [(line, words) for line, words in candidates
                      if upper(line) >= 0.8 and (len(words) >= 2 or len(words[0]) >= 4)]
    lines = []
    for line, _ in candidates:
        lines.append(line)
        if len(" ".join(lines)) >= MAX_TITLE:
            break
    return tidy(" ".join(lines))


def ocr_title(image_path):
    """Text printed on the cover image, or "" when it can't be read (no tesseract, no text)."""
    if not image_path or not ocr_available():
        return ""
    try:
        out = subprocess.run(["tesseract", str(image_path), "stdout", "-l", "eng", "--psm", "11"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    text = clean_ocr(out.stdout)
    return text if usable(text) and len(text) >= 4 else ""


def suggest(caption, hashtags=(), date=None, cover=None):
    """{title, tags, origin, needs_review} for a TikTok post. origin: caption, cover or none."""
    tags = caption_tags(caption, hashtags)
    words = caption_words(caption)
    if usable(words):
        return dict(title=words, tags=tags, origin="caption", needs_review=False)
    read = ocr_title(cover) if cover else ""
    if read:
        return dict(title=read, tags=tags, origin="cover", needs_review=True)
    return dict(title=untitled(date), tags=tags, origin="none", needs_review=True)


# ---------------------------------------------------------------- post list

def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS tiktok_posts (
        post_id TEXT PRIMARY KEY, url TEXT NOT NULL, caption TEXT, hashtags TEXT NOT NULL DEFAULT '[]',
        thumbnail TEXT, posted_at TEXT, cover_text TEXT, seen REAL NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS title_reviews (
        video_id TEXT PRIMARY KEY, decision TEXT NOT NULL, decided REAL NOT NULL)""")


def remember(posts):
    """Keep every post the watcher has listed, so imports by hand and older videos can use its caption."""
    now = time.time()
    with store.db() as c:
        for p in posts:
            if not isinstance(p, dict) or not re.fullmatch(r"\d{8,25}", str(p.get("id", ""))):
                continue
            caption = p.get("caption") if isinstance(p.get("caption"), str) else None
            tags = [t for t in (p.get("hashtags") or []) if isinstance(t, str)]
            thumb = p.get("thumbnail") if isinstance(p.get("thumbnail"), str) and len(p["thumbnail"]) < 2000 else None
            c.execute("""INSERT INTO tiktok_posts(post_id,url,caption,hashtags,thumbnail,posted_at,seen) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(post_id) DO UPDATE SET url=excluded.url, caption=excluded.caption, hashtags=excluded.hashtags,
                thumbnail=COALESCE(excluded.thumbnail, tiktok_posts.thumbnail), posted_at=COALESCE(excluded.posted_at, tiktok_posts.posted_at),
                seen=excluded.seen""",
                      (str(p["id"]), str(p.get("url") or ""), caption, json.dumps(tags), thumb, p.get("posted_at"), now))


def post(source_key):
    if not str(source_key or "").startswith("tt:"):
        return None
    with store.db() as c:
        row = c.execute("SELECT * FROM tiktok_posts WHERE post_id=?", (source_key[3:],)).fetchone()
    if not row:
        return None
    row = dict(row)
    row["hashtags"] = json.loads(row["hashtags"] or "[]")
    return row


def set_cover_text(source_key, text):
    with store.db() as c:
        c.execute("UPDATE tiktok_posts SET cover_text=? WHERE post_id=?", (text or "", source_key[3:]))


def for_job(job):
    """The post details for a queued TikTok job: from its own metadata, else the remembered post list."""
    try:
        meta = json.loads(job.get("metadata") or "null") or {}
    except ValueError:
        meta = {}
    if isinstance(meta, dict) and "caption" in meta:
        return dict(caption=meta.get("caption"), hashtags=meta.get("hashtags") or [],
                    thumbnail=meta.get("thumbnail"), posted_at=meta.get("posted_at"))
    return post(job.get("source_key"))


def placeholder(title):
    title = str(title or "").strip()
    return not title or store.needs_title({"title": title})


def fill_job(job):
    """A TikTok job still called "Untitled" takes its caption (or cover text) as the title.

    Only placeholder titles change; anything the owner typed is kept.
    """
    if job.get("kind") != "tiktok" or not placeholder(job.get("title")):
        return None
    p = for_job(job)
    if not p:
        return None
    cover = None
    if not usable(caption_words(p.get("caption"))):
        import thumbs
        cover = thumbs.path_for(job["source_key"])
        cover = cover if cover and cover.exists() else None
    s = suggest(p.get("caption"), p.get("hashtags"), job.get("source_date") or p.get("posted_at"), cover)
    if s["origin"] == "none" and job.get("title", "").startswith("Untitled 2"):
        return None
    tags = list(dict.fromkeys(json.loads(job.get("tags") or "[]") + s["tags"]))
    store.update_job(job["id"], title=s["title"], tags=json.dumps(tags), needs_title=int(s["needs_review"]))
    if s["origin"] == "cover":
        set_cover_text(job["source_key"], s["title"])
    return s
