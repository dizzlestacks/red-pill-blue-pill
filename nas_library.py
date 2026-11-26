"""A browsable library of the owner's videos on the NAS, kept in step with the site.

Layout inside MIRROR_DIR:

    _system (do not edit)/   recovery copy (objects/, backups/), unchanged from earlier releases
    Videos/<category>/<YYYY-MM-DD - Title>.mp4     the version that plays everywhere
    Originals/<category>/<same name>.mp4           untouched original, when it differs
    Removed from site/Videos|Originals/...         videos deleted from the site (never erased)
    By source/<source>/index.html, index.csv       lists per TikTok account / YouTube source
    index.html, index.csv, README.txt              searchable list of every video

Rules:
- Nothing is ever deleted from the NAS. Renames and category changes move files
  (a plain rename on the same share) rather than copying them again.
- Only plain files, folders and renames are used: no links, permissions, file
  locks, extended attributes or timestamps, which network shares often lack.
- Names use letters, digits and simple punctuation only, are short, and are
  unique without regard to upper/lower case (a short id is added only when needed).
- If the NAS is unavailable nothing else is affected; the next pass catches up.
"""
import csv
import hashlib
import html
import io
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

import admin_store as store

SYSTEM = "_system (do not edit)"
VIDEOS, ORIGINALS, REMOVED, BY_SOURCE = "Videos", "Originals", "Removed from site", "By source"
UNCATEGORISED, NEEDS_TITLE = "Uncategorised", "Needs title"
MOVING = SYSTEM + "/library-moving"
SET_ASIDE = SYSTEM + "/library-set-aside"
TITLE_LIMIT = 80
FOLDER_LIMIT = 60
COPY_BUDGET = 180          # Seconds of copying per pass; moves and the index always run.
PAUSE_BETWEEN_COPIES = 2   # Seconds; keeps the Pi and the network responsive.
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
LETTERS = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ß": "ss", "đ": "d",
                         "Đ": "D", "ł": "l", "Ł": "L", "þ": "th", "Þ": "Th", "—": "-", "–": "-", "’": "'", "‘": "'",
                         "“": "'", "”": "'"})
WAKE = threading.Event()
LOCK = threading.Lock()
STATUS = {"state": "not set up", "total": 0, "done": 0, "copying": None, "problems": [],
          "last_run": None, "last_error": "", "rebuild": False}


def init(c):
    c.execute("""CREATE TABLE IF NOT EXISTS library_files (path TEXT PRIMARY KEY, source_key TEXT NOT NULL,
                 role TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, verified REAL)""")
    c.execute("CREATE TABLE IF NOT EXISTS library_state (key TEXT PRIMARY KEY, value TEXT)")
    # Last details seen on the site, so a deleted video keeps its name in "Removed from site".
    c.execute("CREATE TABLE IF NOT EXISTS library_memory (source_key TEXT PRIMARY KEY, details TEXT NOT NULL)")


def wake():
    WAKE.set()


def request_rebuild():
    with store.db() as c:
        c.execute("INSERT OR REPLACE INTO library_state VALUES('rebuild',?)", (str(time.time()),))
    STATUS["rebuild"] = True
    WAKE.set()


# --- names ------------------------------------------------------------------------------

def safe(text, limit):
    """A name that works on Windows, macOS, Linux and network shares."""
    text = str(text or "").translate(LETTERS)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode()
    text = re.sub(r"(?<!\w)#\w+", " ", text)               # hashtags belong in the index, not the name
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " - ", text)
    text = re.sub(r"[^\w .,'()&+!@=\[\]{}-]", " ", text)
    text = re.sub(r"\s*-\s*(-\s*)+", " - ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    if len(text) > limit:
        cut = text[:limit]
        text = (cut.rsplit(" ", 1)[0] if " " in cut[limit // 2:] else cut).rstrip(" .,-")
    if text.split(".")[0].lower() in RESERVED:
        text += " (folder)"
    return text


def short_id(key):
    return hashlib.sha256(key.encode()).hexdigest()[:6]


def day(value):
    value = str(value or "")
    return value[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value[:10]) else ""


def categories(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return [value.strip()] if isinstance(value, str) and value.strip() else []


# --- what should be on the NAS ----------------------------------------------------------

def items():
    """Every saved copy with the details shown on the site."""
    cat, _ = store.load_catalog()
    with store.db() as c:
        media = {r["source_key"]: dict(r) for r in c.execute("SELECT * FROM media")}
        jobs = {r["source_key"]: dict(r) for r in c.execute("SELECT * FROM jobs")}
        matches = {r["video_id"]: r["source_key"] for r in c.execute(
            "SELECT * FROM backup_matches WHERE undone=0 ORDER BY rowid")}
        memory = {r["source_key"]: json.loads(r["details"]) for r in c.execute("SELECT * FROM library_memory")}
    on_site = {}
    for v in cat["videos"]:
        try:
            key = store.source(store.video_source(v))[0]
        except ValueError:
            key = ""
        if key not in media and v.get("id") in matches:
            key = matches[v["id"]]
        if key in media and key not in on_site:
            on_site[key] = v
    remember = []
    for key, v in on_site.items():
        details = {k: v.get(k) for k in ("title", "category", "tags", "added", "youtube_id", "source_url", "url", "needs_title")}
        if memory.get(key) != details:
            remember.append((key, json.dumps(details)))
    if remember:
        with store.db() as c:
            c.executemany("INSERT OR REPLACE INTO library_memory VALUES(?,?)", remember)
    result = []
    for key, m in media.items():
        v, job = on_site.get(key), jobs.get(key) or {}
        gone = memory.get(key) if not v else None
        if v:
            title, cats, tags = v.get("title"), categories(v.get("category")), v.get("tags") or []
            untitled = store.needs_title(v)
            published = "Yes"
            link = job.get("url") or store.video_source(v)
            if link.startswith("/"):
                link = job.get("url") or ""
        elif gone:
            title, cats, tags = gone.get("title"), categories(gone.get("category")), gone.get("tags") or []
            untitled = store.needs_title(gone)
            published = "Removed from site"
            link = job.get("url") or store.video_source(gone)
            if link.startswith("/"):
                link = ""
        else:
            title, cats = job.get("title") or "", []
            try:
                tags = json.loads(job.get("tags") or "[]")
            except ValueError:
                tags = []
            untitled = not job or store.needs_title(job)
            # Saved but never published, or deleted from the site after publishing.
            published = "No" if job.get("status") not in (None, "published") else "Removed from site"
            link = job.get("url") or ""
        v = v or gone
        posted = day(job.get("source_date")) or day(v.get("added") if v else "") or day(
            time.strftime("%Y-%m-%d", time.gmtime(job.get("created") or m["created"])))
        added = day(v.get("added")) if v and v.get("added") else time.strftime("%Y-%m-%d", time.gmtime(job.get("created") or m["created"]))
        result.append({"key": key, "media": m, "title": "" if untitled else str(title or ""), "categories": cats,
                       "tags": [str(t) for t in tags], "link": link, "posted": posted, "added": added,
                       "published": published, "kind": job.get("kind") or ("youtube" if key.startswith("yt:") else "tiktok")})
    return result


def plan(entries):
    """Decide every file's folder and name. Deterministic, so unchanged videos keep their names."""
    folder_names = {}
    for item in entries:
        if item["published"] == "Removed from site":
            item["folder"] = None
        elif not item["title"]:
            item["folder"] = NEEDS_TITLE
        elif item["categories"]:
            item["folder"] = item["categories"][0]
        else:
            item["folder"] = UNCATEGORISED
    # Category folder names: safe, and different categories never share a folder.
    taken = {UNCATEGORISED.lower(): UNCATEGORISED, NEEDS_TITLE.lower(): NEEDS_TITLE}
    for name in sorted({i["folder"] for i in entries if i["folder"] not in (None, UNCATEGORISED, NEEDS_TITLE)}):
        candidate = safe(name, FOLDER_LIMIT) or "Category"
        if candidate.lower() in taken and taken[candidate.lower()] != name:
            candidate = f"{candidate} ({short_id(name)})"
        taken[candidate.lower()] = name
        folder_names[name] = candidate
    folder_names.update({UNCATEGORISED: UNCATEGORISED, NEEDS_TITLE: NEEDS_TITLE})
    groups = {}
    for item in sorted(entries, key=lambda i: (i["media"]["created"], i["key"])):
        base = f"{item['posted']} - {safe(item['title'], TITLE_LIMIT) or 'Untitled'}"
        item["area"] = REMOVED if item["folder"] is None else None
        item["subfolder"] = "" if item["folder"] is None else folder_names[item["folder"]]
        group = (item["area"], item["subfolder"], base.lower())
        groups.setdefault(group, []).append(item)
        item["base"] = base
    for group in groups.values():
        for n, item in enumerate(group):
            # The first keeps the plain name; later duplicates get a short id. Untitled always do.
            if n or not item["title"]:
                item["base"] = f"{item['base']} [{short_id(item['key'])}]"
    for item in entries:
        m = item["media"]
        play_ready = m.get("play_status") == "ready" and m.get("play_key")
        same = not play_ready or m["play_key"] == m["object_key"]
        files = []
        top = (REMOVED + "/" + VIDEOS) if item["area"] else VIDEOS
        orig_top = (REMOVED + "/" + ORIGINALS) if item["area"] else ORIGINALS
        sub = (item["subfolder"] + "/") if item["subfolder"] else ""
        if play_ready:
            files.append({"role": "play", "path": f"{top}/{sub}{item['base']}.mp4", "key": m["play_key"],
                          "sha256": m.get("play_sha256") or m["sha256"], "size": m.get("play_size") or m["size"]})
        if not same or not play_ready:
            # The original goes to Videos until a playback version exists, and to Originals after.
            where = f"{orig_top}/{sub}{item['base']}.mp4" if play_ready else f"{top}/{sub}{item['base']}.mp4"
            files.append({"role": "original", "path": where, "key": m["object_key"], "sha256": m["sha256"], "size": m["size"]})
        item["files"] = files
    return entries


# --- file operations safe on network shares ---------------------------------------------

def _copy(source, dest, sha, size):
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.parent / f".partial-{uuid.uuid4().hex}"
    try:
        digest = hashlib.sha256()
        with open(source, "rb") as inp, open(temp, "wb") as out:
            for chunk in iter(lambda: inp.read(1024 * 1024), b""):
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        with open(temp, "rb") as check:
            for chunk in iter(lambda: check.read(1024 * 1024), b""):
                digest.update(chunk)
        if temp.stat().st_size != size or digest.hexdigest() != sha:
            raise OSError("The copy on the NAS doesn't match the saved video")
        if dest.exists():
            raise FileExistsError(str(dest))
        os.rename(temp, dest)
    finally:
        if temp.exists():
            temp.unlink()  # Only ever our own unfinished temporary file.


def _sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path, text):
    """Replace a small index file. Falls back to writing in place where a share can't rename over a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    temp = path.parent / f".partial-{uuid.uuid4().hex}"
    temp.write_bytes(data)
    try:
        os.replace(temp, path)
    except OSError:
        path.write_bytes(data)
        temp.unlink(missing_ok=True)


def _tidy(root, relative):
    """Remove the now-empty folder a file was moved out of (only if empty; never files)."""
    folder = (root / relative).parent
    while folder != root and folder.name not in (VIDEOS, ORIGINALS, REMOVED, SYSTEM):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


def _free_name(root, relative, key):
    """A name nobody else uses: adds the short id, then a counter, before .mp4."""
    path = PurePosixPath(relative)
    for n in range(100):
        suffix = f" [{short_id(key)}]" if n == 0 else f" [{short_id(key)}-{n}]"
        candidate = str(path.with_name(path.stem + suffix + path.suffix))
        if not (root / candidate).exists():
            return candidate
    raise OSError("No free file name")


# --- moving the old flat recovery layout into its own folder ------------------------------

def migrate_system(root):
    """Earlier releases kept objects/ and backups/ at the top of the NAS folder. Move them, never delete."""
    for name in ("objects", "backups"):
        old, new = root / name, root / SYSTEM / name
        if not old.is_dir():
            continue
        (root / SYSTEM).mkdir(exist_ok=True)
        if not new.exists():
            os.rename(old, new)
            continue
        for source in sorted(old.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if source.is_file():
                target = new / source.relative_to(old)
                if target.exists():
                    target = root / SET_ASIDE / "old-layout" / name / source.relative_to(old)
                    if target.exists():
                        continue
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, target)
        for folder in sorted((p for p in old.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                folder.rmdir()  # Only folders that are now empty.
            except OSError:
                pass
        try:
            old.rmdir()
        except OSError:
            pass


# --- the sync pass ----------------------------------------------------------------------

def sync(root, client, deadline=None):
    """One pass: moves first (fast), then copies within a time budget, then the index files."""
    deadline = deadline or (time.monotonic() + COPY_BUDGET)
    entries = plan(items())
    with store.db() as c:
        started = c.execute("SELECT value FROM library_state WHERE key='rebuild'").fetchone()
        rebuild = float(started[0]) if started else None
        records = {r["path"]: dict(r) for r in c.execute("SELECT * FROM library_files")}
    problems = []
    by_key = {}
    for path, r in records.items():
        by_key.setdefault(r["source_key"], []).append(r)

    def record(c, path, key, role, sha, size):
        c.execute("INSERT OR REPLACE INTO library_files VALUES(?,?,?,?,?,?)", (path, key, role, sha, size, time.time()))

    # Check what we recorded is still there; a rebuild also re-reads every byte, a share at a time.
    unchecked = 0
    for path, r in list(records.items()):
        file = root / path
        recheck = rebuild is not None and (r["verified"] or 0) < rebuild
        if recheck and time.monotonic() > deadline:
            recheck = False
            unchecked += 1
        ok = file.is_file() and file.stat().st_size == r["size"] and (not recheck or _sha(file) == r["sha256"])
        if ok and recheck:
            with store.db() as c:  # Re-read during this rebuild; not again until the next one.
                c.execute("UPDATE library_files SET verified=? WHERE path=?", (time.time(), path))
        if not ok:
            if file.is_file():
                # Changed by someone on the NAS: set it aside rather than overwrite or delete it.
                aside = f"{SET_ASIDE}/changed/{path}"
                (root / aside).parent.mkdir(parents=True, exist_ok=True)
                if not (root / aside).exists():
                    os.rename(file, root / aside)
                problems.append({"title": path, "reason": "This file was changed on the NAS, so it was moved to "
                                 + SET_ASIDE + " and a fresh copy will be made."})
            with store.db() as c:
                c.execute("DELETE FROM library_files WHERE path=?", (path,))
            records.pop(path)
            by_key[r["source_key"]].remove(r)

    # Moves: match each wanted file to a recorded file with the same content.
    moves = []
    for e in entries:
        have = list(by_key.get(e["key"], []))
        for f in e["files"]:
            match = next((r for r in have if r["path"] == f["path"] and r["sha256"] == f["sha256"]), None) or \
                    next((r for r in have if r["sha256"] == f["sha256"]), None)
            if match:
                have.remove(match)
                f["recorded"] = match
                if match["path"] != f["path"]:
                    moves.append((match, f, e["key"]))
        for leftover in have:
            # Content no longer wanted for this video (for example replaced by a re-download): set aside.
            moves.append((leftover, {"path": f"{SET_ASIDE}/replaced/{leftover['path']}", "role": "set-aside",
                                     "sha256": leftover["sha256"], "size": leftover["size"]}, e["key"]))
    # Saved copies removed from the Pi (for example removed from the import list before
    # publishing): move their NAS files to "Removed from site" and stop tracking them.
    wanted = {e["key"] for e in entries}
    for key, have in by_key.items():
        if key in wanted:
            continue
        for r in have:
            if r["path"].startswith(REMOVED + "/"):
                with store.db() as c:
                    c.execute("DELETE FROM library_files WHERE path=?", (r["path"],))
                continue
            moves.append((r, {"path": f"{REMOVED}/{r['path']}", "role": "set-aside",
                              "sha256": r["sha256"], "size": r["size"]}, key))
    # Two steps so swaps and chains of renames never collide.
    for r, f, key in moves:
        temp = f"{MOVING}/{uuid.uuid4().hex}.mp4"
        (root / temp).parent.mkdir(parents=True, exist_ok=True)
        os.rename(root / r["path"], root / temp)
        _tidy(root, r["path"])
        with store.db() as c:
            c.execute("UPDATE library_files SET path=? WHERE path=?", (temp, r["path"]))
        r["path"] = temp
    for r, f, key in moves:
        target = f["path"]
        if (root / target).exists():
            target = _free_name(root, target, key)
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        os.rename(root / r["path"], root / target)
        _tidy(root, r["path"])
        with store.db() as c:
            if f["role"] == "set-aside":
                c.execute("DELETE FROM library_files WHERE path=?", (r["path"],))  # Kept on the NAS, no longer tracked.
            else:
                c.execute("UPDATE library_files SET path=?, role=? WHERE path=?", (target, f["role"], r["path"]))
        f["final"] = target

    # Copies for anything not on the NAS yet.
    done = total = 0
    STATUS["copying"] = None
    for e in sorted(entries, key=lambda e: e["media"]["created"]):
        for f in e["files"]:
            total += 1
            if f.get("recorded"):
                done += 1
                continue
            if time.monotonic() > deadline:
                continue
            try:
                source = client.path(f["key"])
                if not source.is_file() or source.stat().st_size != f["size"]:
                    problems.append({"title": e["title"] or e["base"], "reason": "The saved video file is missing on the Pi, so it can't be copied."})
                    continue
                target = f["path"]
                if (root / target).exists():
                    existing = root / target
                    if existing.stat().st_size == f["size"] and _sha(existing) == f["sha256"]:
                        with store.db() as c:   # Same file already there (for example after a restore): adopt it.
                            record(c, target, e["key"], f["role"], f["sha256"], f["size"])
                        f["final"] = target
                        done += 1
                        continue
                    target = _free_name(root, target, e["key"])
                STATUS["copying"] = e["title"] or e["base"]
                _copy(source, root / target, f["sha256"], f["size"])
                with store.db() as c:
                    record(c, target, e["key"], f["role"], f["sha256"], f["size"])
                f["final"] = target
                done += 1
                time.sleep(PAUSE_BETWEEN_COPIES)
            except FileNotFoundError:
                problems.append({"title": e["title"] or e["base"], "reason": "The saved video file is missing on the Pi."})
            finally:
                STATUS["copying"] = None
    if rebuild is not None and done == total and not unchecked:
        with store.db() as c:
            c.execute("DELETE FROM library_state WHERE key='rebuild'")
        STATUS["rebuild"] = False
    write_indexes(root, entries)
    return {"total": total, "done": done, "problems": problems}


# --- index files ------------------------------------------------------------------------

COLUMNS = ["Title", "Categories", "Hashtags", "Published", "Post date", "Added", "Folder", "File",
           "Original file", "Original link", "Length", "Sound check"]


def rows(root, entries):
    with store.db() as c:
        recorded = {}
        for r in c.execute("SELECT * FROM library_files"):
            recorded.setdefault((r["source_key"], r["role"]), r["path"])
    out = []
    for e in sorted(entries, key=lambda e: (e["posted"], e["title"].lower()), reverse=True):
        play = recorded.get((e["key"], "play"))
        original = recorded.get((e["key"], "original"))
        main = play or original
        if play and original:
            orig = original
        elif original and not play:
            orig = "(same file - no separate original)"
        else:
            orig = "(same file - the original already plays everywhere)" if play else ""
        seconds = int(round(e["media"].get("duration") or 0))
        out.append({"Title": e["title"] or "(needs a title)", "Categories": ", ".join(e["categories"]),
                    "Hashtags": " ".join("#" + t.lstrip("#") for t in e["tags"]), "Published": e["published"],
                    "Post date": e["posted"], "Added": e["added"],
                    "Folder": str(PurePosixPath(main).parent) if main else "(copying soon)",
                    "File": PurePosixPath(main).name if main else "(copying soon)",
                    "Original file": orig, "Original link": e["link"],
                    "Length": f"{seconds // 60}:{seconds % 60:02d}" if seconds else "",
                    "Sound check": {"ok": "Passed", "accepted": "No sound (confirmed)", "problem": "Problem"}.get(
                        e["media"].get("check_status") or "", "Not checked yet"),
                    "_path": main, "_kind": e["kind"], "_link": e["link"]})
    return out


def csv_text(table):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in table:
        # Spreadsheet apps treat a leading = + - @ as a formula; keep it as text.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in row.items()})
    return "\ufeff" + buffer.getvalue()


def html_page(title, table, prefix):
    def link(path):
        return "/".join(quote_part(p) for p in (prefix + path).split("/"))
    body = []
    for r in table:
        name = html.escape(r["File"])
        cell = f'<a href="{html.escape(link(r["_path"]))}">{name}</a>' if r["_path"] else name
        source = f'<a href="{html.escape(r["_link"])}" rel="noreferrer">link</a>' if r["_link"].startswith("http") else ""
        body.append("<tr>" + "".join(f"<td>{html.escape(str(r[c]))}</td>" for c in ("Title", "Categories", "Hashtags", "Published", "Post date", "Added", "Folder"))
                    + f"<td>{cell}</td><td>{html.escape(r['Original file'])}</td><td>{source}</td><td>{html.escape(r['Length'])}</td><td>{html.escape(r['Sound check'])}</td></tr>")
    head = "".join(f"<th>{html.escape(c)}</th>" for c in COLUMNS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font:15px system-ui,sans-serif;margin:16px;color:#122}}h1{{font-size:20px}}input{{font:inherit;padding:8px;width:min(100%,420px)}}
table{{border-collapse:collapse;margin-top:12px;width:100%}}th,td{{border-bottom:1px solid #ccd;padding:6px 8px;text-align:left;vertical-align:top}}
th{{position:sticky;top:0;background:#eef}}tr.hidden{{display:none}}</style></head><body>
<h1>{html.escape(title)}</h1><p>{len(table)} videos. Type to search titles, categories, hashtags and dates.</p>
<input id="q" type="search" placeholder="Search" aria-label="Search"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>
<script>q.oninput=()=>{{const t=q.value.toLowerCase();for(const r of document.querySelectorAll('tbody tr'))r.className=r.textContent.toLowerCase().includes(t)?'':'hidden'}}</script>
</body></html>
"""


def quote_part(part):
    from urllib.parse import quote
    return quote(part)


README = """This folder is your WokeAreWe video library, kept up to date by the Pi.

Videos/             Every saved video, in a folder per site category. Files are named
                    "post date - title". This is the version that plays everywhere.
Originals/          The untouched original download, when it is different.
Removed from site/  Videos you deleted from the site. Nothing is ever erased here.
By source/          A list per TikTok account or YouTube source.
index.html          Open in any browser to search every video.
index.csv           The same list for Excel, Numbers or Google Sheets.

_system (do not edit)/  The Pi's recovery copy. Don't change or move anything in it.

Renaming or moving files here yourself is safe for you, but the Pi keeps its own
names: when you rename or recategorise a video on the site, the files here follow.
"""


def write_indexes(root, entries):
    table = rows(root, entries)
    _write_text(root / "index.csv", csv_text(table))
    _write_text(root / "index.html", html_page("WokeAreWe video library", table, ""))
    _write_text(root / "README.txt", README)
    sources = {}
    for r in table:
        name = "YouTube videos"
        found = re.search(r"tiktok\.com/@([\w.]+)", r["_link"] or "")
        if found:
            name = "TikTok @" + found.group(1)
        elif r["_kind"] == "tiktok":
            name = "TikTok"
        sources.setdefault(name, []).append(r)
    for name, part in sources.items():
        folder = root / BY_SOURCE / safe(name, FOLDER_LIMIT)
        _write_text(folder / "index.csv", csv_text(part))
        _write_text(folder / "index.html", html_page(name, part, "../../"))


# --- background loop and status ---------------------------------------------------------

def run_once():
    import disk_storage
    import media_worker as worker
    with LOCK:
        try:
            root = disk_storage.mirror_root()
        except OSError:
            STATUS.update(state="waiting for NAS", last_error="The NAS folder can't be reached. The library catches up when it's back.",
                          last_run=time.time())
            return
        if not root:
            STATUS.update(state="not set up", last_error="", last_run=time.time())
            return
        try:
            STATUS.update(state="updating")
            migrate_system(root)
            result = sync(root, worker.storage_client())
            STATUS.update(state="up to date" if result["done"] == result["total"] else "copying",
                          total=result["total"], done=result["done"], problems=result["problems"][:50],
                          last_error="", last_run=time.time())
        except Exception as e:
            STATUS.update(state="waiting for NAS", last_run=time.time(),
                          last_error="The library update stopped (" + (str(e)[:160] or type(e).__name__)
                          + "). Nothing was removed; it tries again shortly.")


def status():
    with store.db() as c:
        rebuild = bool(c.execute("SELECT 1 FROM library_state WHERE key='rebuild'").fetchone())
    return {**STATUS, "rebuild": rebuild}
