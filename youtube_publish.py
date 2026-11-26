"""Publish TikTok-sourced hub videos to the owner's own YouTube channel.

Uses only the official YouTube Data API v3 with Google's OAuth sign-in. The
owner connects once from /admin; Google's lasting permission (refresh token) is
kept in a service-only file in the data folder, never in the recovery
snapshot, the NAS mirror or GitHub. No password is ever asked for or stored.

Nothing is uploaded until the owner is connected to the expected channel, has
reviewed the "already on YouTube" / "not on YouTube yet" lists, chosen who can
see the uploads, and pressed Start.

Never uploading twice:
  * every upload carries a marker (a "wokearewe-tt-<post id>" tag and the
    video's wokearewe.com link), so the channel itself records what came from
    here;
  * uploads are resumable; the upload address is saved before any bytes are
    sent, and after a restart the Pi asks YouTube how much arrived (or whether
    it already finished) instead of starting again;
  * a fresh upload is only started after reading the channel's newest videos
    for that marker, and an upload whose address was lost waits ten minutes
    and looks again before trying a second time.

Limits: the YouTube Data API gives a project 100 videos.insert calls a day
(own bucket, 1 each) and 10,000 units for other calls; both reset at midnight
Pacific time. The owner picks a lower daily number; uploads are spread through
the day and pause until the reset when YouTube says a limit was reached.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs, quote

import admin_store as store

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
SCOPES = (UPLOAD_SCOPE, READ_SCOPE)
CALLBACK = "/admin/youtube/callback"
CHUNK = 8 * 1024 * 1024  # A multiple of 256 KiB, as resumable uploads require.
DAILY_INSERTS = 100  # Default videos.insert bucket per project per day.
RECHECK_SECONDS = 600
VISIBILITY = ("public", "unlisted", "private")
CATEGORIES = {"1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals",
              "17": "Sports", "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs",
              "23": "Comedy", "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
              "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism"}
AUDIT_STATES = ("not requested", "requested", "passed")
DEFAULTS = dict(connected=False, channel=None, started=False, paused=False, auto_new=False, visibility="",
                category="22", made_for_kids=False, end_card=False, paid_promotion=False, per_day=6, start_after=0,
                audit="not requested", quota_day="", uploads_today=0, units_today=0, paused_until=0,
                pause_reason="", next_upload_at=0, last_scan=None, scan_status="never", scan_error="",
                scan_requested=False, scan_retry_at=0, error="", last_upload=None, private_results=0, started_at=None)

PRIVATE_LOCK = threading.RLock()
RUN_LOCK = threading.Lock()
PROGRESS = {}
LAST_HEARTBEAT = 0
_last_refresh = 0


class YouTubeError(Exception):
    """A problem with a plain reason the owner can act on."""


class AuthError(YouTubeError):
    pass


class QuotaPause(YouTubeError):
    def __init__(self, message, until):
        super().__init__(message)
        self.until = until


class ItemError(YouTubeError):
    """YouTube refused this one video (e.g. its title); the others carry on."""


class Retry(YouTubeError):
    """Temporary trouble; the same upload continues later."""


class Response:
    def __init__(self, status, headers, body):
        from requests.structures import CaseInsensitiveDict
        self.status, self.headers, self.body = status, CaseInsensitiveDict(headers or {}), body or b""

    def json(self):
        try:
            return json.loads(self.body or b"{}")
        except ValueError:
            return {}


class HTTP:
    def request(self, method, url, params=None, headers=None, data=None, json_body=None, form=None):
        import requests
        try:
            r = requests.request(method, url, params=params, headers=headers, data=form if form is not None else data,
                                 json=json_body, timeout=(15, 300), allow_redirects=False)
        except requests.RequestException:
            raise Retry("Couldn't reach YouTube. The Pi will try again shortly.")
        return Response(r.status_code, r.headers, r.content)


TRANSPORT = HTTP()


# ---------------------------------------------------------------- storage

def init():
    with store.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS yt_publish (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS yt_channel (video_id TEXT PRIMARY KEY, data TEXT NOT NULL,
            gone INTEGER NOT NULL DEFAULT 0, seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS yt_items (video_id TEXT PRIMARY KEY, source_key TEXT NOT NULL,
            state TEXT NOT NULL, match_video TEXT, match_kind TEXT NOT NULL DEFAULT '',
            candidates TEXT NOT NULL DEFAULT '[]', approved INTEGER NOT NULL DEFAULT 0,
            youtube_id TEXT, attempts INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
            retry_at REAL NOT NULL DEFAULT 0, recheck_at REAL NOT NULL DEFAULT 0,
            hub_set INTEGER NOT NULL DEFAULT 0, privacy TEXT, uploaded_at REAL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS yt_endcards (source_key TEXT PRIMARY KEY, idempotency TEXT NOT NULL,
            upstream_id INTEGER, object_key TEXT, size INTEGER, sha256 TEXT, status TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO yt_publish VALUES(1,?)", (json.dumps(DEFAULTS),))


def settings():
    with store.db() as c:
        row = c.execute("SELECT data FROM yt_publish WHERE id=1").fetchone()
    return dict(DEFAULTS, **json.loads(row[0])) if row else dict(DEFAULTS)


def update(**fields):
    with store.LOCK, store.db() as c:
        data = dict(DEFAULTS, **json.loads(c.execute("SELECT data FROM yt_publish WHERE id=1").fetchone()[0]))
        data.update(fields)
        c.execute("UPDATE yt_publish SET data=? WHERE id=1", (json.dumps(data),))
    return data


def private_path():
    return store.STATE / "youtube-connection.json"


def private():
    with PRIVATE_LOCK:
        try:
            return json.loads(private_path().read_text())
        except (OSError, ValueError):
            return {}


def save_private(data):
    """Service-only file (0600). Kept out of recovery snapshots, the NAS and GitHub."""
    with PRIVATE_LOCK:
        path = private_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".youtube-", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise


def edit_private(fn):
    with PRIVATE_LOCK:
        data = private()
        fn(data)
        save_private(data)
        return data


def item(video_id):
    with store.db() as c:
        row = c.execute("SELECT * FROM yt_items WHERE video_id=?", (video_id,)).fetchone()
    return dict(row) if row else None


def set_item(video_id, **fields):
    fields["updated"] = time.time()
    with store.db() as c:
        c.execute("UPDATE yt_items SET " + ",".join(f"{k}=?" for k in fields) + " WHERE video_id=?",
                  (*fields.values(), video_id))


def checkpoint():
    import durable_state
    durable_state.checkpoint()


# ---------------------------------------------------------------- time and limits

def pacific():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:
        return timezone(timedelta(hours=-8))


def pacific_day(now=None):
    return datetime.fromtimestamp(now or time.time(), pacific()).strftime("%Y-%m-%d")


def next_reset(now=None):
    """Epoch seconds of the next midnight Pacific time, when YouTube quotas reset."""
    local = datetime.fromtimestamp(now or time.time(), pacific())
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()


def spacing(per_day):
    """Spread the day's uploads out: never closer than 15 minutes."""
    return max(900, int(20 * 3600 / max(1, per_day)))


def roll_day(s):
    today = pacific_day()
    if s["quota_day"] != today:
        s = update(quota_day=today, uploads_today=0, units_today=0)
    return s


def count_units(n):
    s = roll_day(settings())
    update(units_today=s["units_today"] + n)


# ---------------------------------------------------------------- Google sign-in

def client():
    cid = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
    secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else None


def expected_handle():
    handle = os.environ.get("YOUTUBE_CHANNEL_HANDLE", "@wokearewe1").strip().lower()
    return handle if handle.startswith("@") else "@" + handle


def redirect_uri(origin=None, host=None):
    explicit = os.environ.get("YOUTUBE_REDIRECT_URI", "").strip()
    if explicit:
        return explicit
    pi = os.environ.get("PI_PUBLIC_URL", "").strip().rstrip("/")
    if pi:
        return pi + CALLBACK
    if origin and host:
        p = urlparse(origin)
        if p.netloc == host and (p.scheme == "https" or p.hostname in ("localhost", "127.0.0.1")) and not p.path.strip("/"):
            return f"{p.scheme}://{p.netloc}{CALLBACK}"
    return None


def begin_connect(origin=None, host=None):
    creds = client()
    if not creds:
        raise ValueError("Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to the Pi settings file first, then restart the service. The setup steps below explain where they come from.")
    uri = redirect_uri(origin, host)
    if not uri:
        raise ValueError("Google needs a secure return address. Set PI_PUBLIC_URL (or YOUTUBE_REDIRECT_URI) in the Pi settings file, or open /admin over HTTPS.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    def add(data):
        pending = {k: v for k, v in data.get("pending", {}).items() if v["created"] > time.time() - 900}
        pending = dict(sorted(pending.items(), key=lambda kv: kv[1]["created"])[-4:])
        pending[state] = {"verifier": verifier, "created": time.time(), "redirect_uri": uri}
        data["pending"] = pending
    edit_private(add)
    params = {"client_id": creds[0], "redirect_uri": uri, "response_type": "code", "scope": " ".join(SCOPES),
              "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    return {"url": AUTH_URL + "?" + urlencode(params), "redirect_uri": uri}


def finish_connect(state, code=None, error=None):
    taken = {}

    def take(data):
        taken.update(data.get("pending", {}).pop(state, None) or {}) if isinstance(state, str) else None
    edit_private(take)  # Single use, even if the rest fails.
    if not taken or taken["created"] < time.time() - 900:
        raise ValueError("This sign-in link has expired or was already used. Press Connect YouTube again.")
    if error or not code:
        raise ValueError("Google sign-in was cancelled, so nothing changed.")
    creds = client()
    if not creds:
        raise ValueError("The Pi's Google client settings are missing.")
    r = TRANSPORT.request("POST", TOKEN_URL, form={
        "code": code, "client_id": creds[0], "client_secret": creds[1], "redirect_uri": taken["redirect_uri"],
        "grant_type": "authorization_code", "code_verifier": taken["verifier"]})
    token = r.json()
    if r.status != 200 or not token.get("access_token"):
        raise ValueError("Google didn't accept the sign-in. Press Connect YouTube again.")
    granted = set(str(token.get("scope", "")).split())
    if not set(SCOPES) <= granted:
        revoke(token.get("refresh_token") or token["access_token"])
        raise ValueError("Both permissions are needed: upload videos, and see your channel's videos (to avoid duplicates). Press Connect YouTube again and allow both.")
    if not token.get("refresh_token"):
        raise ValueError("Google didn't give a lasting permission. Remove this app under your Google Account's third-party access, then connect again.")

    def keep(data):
        data.update(refresh_token=token["refresh_token"], access_token=token["access_token"],
                    access_expires=time.time() + int(token.get("expires_in", 3600)) - 120)
    edit_private(keep)
    try:
        channel = fetch_channel()
    except YouTubeError as e:
        disconnect()
        raise ValueError(str(e))
    update(connected=True, channel=channel, error="", scan_requested=True, scan_status="waiting", paused_until=0,
           pause_reason="", scan_retry_at=0)
    checkpoint()
    return status()


def finish_from_pasted(url):
    if not isinstance(url, str) or len(url) > 4000:
        raise ValueError("Paste the full address Google sent you to")
    q = parse_qs(urlparse(url.strip()).query)
    return finish_connect(q.get("state", [""])[0], q.get("code", [None])[0], q.get("error", [None])[0])


def revoke(token):
    try:
        TRANSPORT.request("POST", REVOKE_URL, form={"token": token})
    except Exception:
        pass  # Disconnecting locally still removes the Pi's permission.


def disconnect():
    data = private()
    if data.get("refresh_token"):
        revoke(data["refresh_token"])

    def clear(d):
        for key in ("refresh_token", "access_token", "access_expires"):
            d.pop(key, None)
    edit_private(clear)
    update(connected=False, started=False, channel=None, error="")
    PROGRESS.clear()
    checkpoint()
    return status()


def access_token(force=False):
    data = private()
    if not force and data.get("access_token") and data.get("access_expires", 0) > time.time():
        return data["access_token"]
    creds = client()
    if not data.get("refresh_token") or not creds:
        update(connected=False)
        raise AuthError("YouTube isn't connected. Press Connect YouTube.")
    r = TRANSPORT.request("POST", TOKEN_URL, form={"client_id": creds[0], "client_secret": creds[1],
                                                   "refresh_token": data["refresh_token"], "grant_type": "refresh_token"})
    token = r.json()
    if r.status in (400, 401) and token.get("error") in ("invalid_grant", "unauthorized_client", "invalid_client"):
        edit_private(lambda d: [d.pop(k, None) for k in ("refresh_token", "access_token", "access_expires")])
        update(connected=False, error="Google's permission for YouTube was removed or has expired. Press Connect YouTube to sign in again. "
               "If this happens every week, set the Google project's publishing status to In production (see setup steps).")
        raise AuthError("YouTube permission expired")
    if r.status != 200 or not token.get("access_token"):
        raise Retry("Google sign-in service didn't answer properly. Trying again shortly.")
    edit_private(lambda d: d.update(access_token=token["access_token"],
                                    access_expires=time.time() + int(token.get("expires_in", 3600)) - 120))
    return token["access_token"]


def _reason(r):
    try:
        err = r.json().get("error", {})
        errors = err.get("errors") or [{}]
        return str(errors[0].get("reason") or err.get("status") or ""), str(err.get("message") or "")
    except AttributeError:
        return "", ""


def raise_for(r, item_level=False):
    reason, message = _reason(r)
    if r.status == 401:
        edit_private(lambda d: d.update(access_expires=0))  # Refresh on the next try.
        raise Retry("YouTube sign-in needed renewing. Trying again.")
    if reason in ("quotaExceeded", "dailyLimitExceeded"):
        raise QuotaPause("Your Google project's daily YouTube allowance is used up. Uploads continue after it resets at midnight Pacific time.", next_reset() + 300)
    if reason == "uploadLimitExceeded":
        raise QuotaPause("Your channel reached YouTube's own daily upload limit. Uploads continue tomorrow (after midnight Pacific time).", next_reset() + 300)
    if reason in ("rateLimitExceeded", "userRateLimitExceeded") or r.status == 429:
        raise QuotaPause("YouTube asked the Pi to slow down. Uploads continue in 15 minutes.", time.time() + 900)
    if reason == "youtubeSignupRequired":
        raise AuthError("This Google account has no YouTube channel. Disconnect and sign in with the account that owns your channel.")
    if r.status == 403:
        raise AuthError("YouTube refused permission (" + (reason or "forbidden") + "). Disconnect and connect again, allowing both permissions.")
    if r.status >= 500 or r.status == 0:
        raise Retry("YouTube had a temporary problem. The Pi will continue shortly.")
    if item_level and r.status == 400:
        plain = {"invalidTitle": "YouTube didn't accept this video's title. Edit the title in Videos, then press Try again.",
                 "invalidDescription": "YouTube didn't accept the description made from this caption.",
                 "invalidTags": "YouTube didn't accept this video's hashtags. Edit them in Videos, then press Try again.",
                 "invalidCategoryId": "YouTube didn't accept the chosen category. Pick another in the upload settings.",
                 "mediaBodyRequired": "The saved copy couldn't be read for upload."}
        raise ItemError(plain.get(reason, "YouTube refused this video (" + (reason or "bad request") + ")."))
    raise YouTubeError("YouTube answered with an error (" + (reason or f"HTTP {r.status}") + ").")


def api_get(path, params, units=1):
    for attempt in (0, 1):
        r = TRANSPORT.request("GET", API + path, params=params,
                              headers={"Authorization": "Bearer " + access_token(force=attempt == 1)})
        count_units(units)
        if r.status == 401 and attempt == 0:
            continue
        if r.status != 200:
            raise_for(r)
        return r.json()


# ---------------------------------------------------------------- channel inventory

def fetch_channel():
    data = api_get("/channels", {"part": "snippet,contentDetails", "mine": "true"})
    items = data.get("items") or []
    if not items:
        raise YouTubeError("This Google account has no YouTube channel. Sign in with the account that owns " + expected_handle() + ".")
    ch = items[0]
    snippet = ch.get("snippet", {})
    handle = str(snippet.get("customUrl") or "")
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    return {"id": ch.get("id"), "title": snippet.get("title", ""), "handle": handle,
            "uploads": ch.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")}


def right_channel(s):
    return bool(s.get("channel")) and str(s["channel"].get("handle", "")).lower() == expected_handle()


def iso_seconds(value):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(value or ""))
    if not m:
        return None
    d, h, mi, s = (float(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def markers(snippet):
    tt, hub = None, None
    for tag in snippet.get("tags") or []:
        m = re.fullmatch(r"wokearewe-tt-(\d{8,25})", str(tag))
        if m:
            tt = m[1]
    desc = str(snippet.get("description") or "")
    m = re.search(r"tiktok\.com/@[\w.-]+/video/(\d{8,25})", desc) if not tt else None
    if m:
        tt = m[1]
    m = re.search(r"#video=([\w-]{1,100})", desc)
    if m:
        hub = m[1]
    return tt, hub


def record_channel(videos):
    now = time.time()
    with store.db() as c:
        for v in videos:
            snippet, status = v.get("snippet", {}), v.get("status", {})
            tt, hub = markers(snippet)
            data = {"title": str(snippet.get("title", ""))[:300], "duration": iso_seconds(v.get("contentDetails", {}).get("duration")),
                    "privacy": status.get("privacyStatus"), "upload_status": status.get("uploadStatus"),
                    "published": snippet.get("publishedAt"), "marker_tt": tt, "marker_hub": hub}
            c.execute("""INSERT INTO yt_channel(video_id,data,gone,seen) VALUES(?,?,0,?)
                         ON CONFLICT(video_id) DO UPDATE SET data=excluded.data, gone=0, seen=excluded.seen""",
                      (v["id"], json.dumps(data), now))


def scan(full=True, max_pages=None):
    """Read the channel's uploads (newest first). A full scan also notices deleted videos."""
    s = settings()
    uploads = (s.get("channel") or {}).get("uploads")
    if not uploads:
        s = update(channel=fetch_channel())
        uploads = s["channel"]["uploads"]
    token, seen, pages = None, set(), 0
    started = time.time()
    while True:
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if token:
            params["pageToken"] = token
        try:
            page = api_get("/playlistItems", params)
        except YouTubeError as e:
            if "playlistNotFound" in str(e):
                page = {"items": []}  # A channel with no uploads yet.
            else:
                raise
        ids = [it.get("contentDetails", {}).get("videoId") for it in page.get("items", [])]
        ids = [i for i in ids if isinstance(i, str) and re.fullmatch(r"[\w-]{6,20}", i)]
        if ids:
            record_channel(api_get("/videos", {"part": "snippet,contentDetails,status", "id": ",".join(ids)}).get("items", []))
        seen.update(ids)
        pages += 1
        token = page.get("nextPageToken")
        if not token or (max_pages and pages >= max_pages):
            break
    if full and not token:
        with store.db() as c:
            c.execute("UPDATE yt_channel SET gone=1 WHERE seen<?", (started,))
        update(last_scan=time.time(), scan_status="done", scan_error="", scan_requested=False)
    return seen


def channel_videos():
    with store.db() as c:
        return {r["video_id"]: json.loads(r["data"]) for r in c.execute("SELECT * FROM yt_channel WHERE gone=0")}


# ---------------------------------------------------------------- matching

def normalized(text):
    text = re.sub(r"(?<!\w)#\w+", " ", str(text or "").lower())
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def title_score(ours, theirs):
    a, b = normalized(ours), normalized(theirs)
    if not a or not b:
        return 0.0
    if len(b) >= 85 and len(a) > len(b):
        a = a[:len(b)]  # YouTube titles stop at 100 characters.
    return SequenceMatcher(None, a, b).ratio()


def close_length(a, b):
    return bool(a) and bool(b) and abs(a - b) <= max(2.0, 0.05 * max(a, b))


def tiktok_entries():
    """Hub entries that came from a TikTok post, with what's needed to match and upload them."""
    cat, _ = store.load_catalog()
    with store.db() as c:
        media = {r["source_key"]: dict(r) for r in c.execute("SELECT * FROM media")}
        jobs = {r["source_key"]: dict(r) for r in c.execute("SELECT * FROM jobs WHERE kind='tiktok'")}
    out = []
    for v in cat["videos"]:
        key = next((k for k in store.video_keys(v) if k.startswith("tt:")), None)
        if not key:
            continue
        m, j = media.get(key), jobs.get(key) or {}
        out.append({"id": v["id"], "key": key, "title": v.get("title", ""), "caption_title": j.get("title") or "",
                    "added": v.get("added") or "", "youtube_id": v.get("youtube_id") or "", "video": v,
                    "duration": (m or {}).get("duration") or None, "media": m, "job": j})
    return out


def refresh_items():
    """Sort every TikTok-sourced video into on YouTube / needs a check / not on YouTube yet."""
    s = settings()
    entries = tiktok_entries()
    channel = channel_videos()
    with store.db() as c:
        rows = {r["video_id"]: dict(r) for r in c.execute("SELECT * FROM yt_items")}
    ids = {e["id"] for e in entries}
    with store.db() as c:
        for vid, r in rows.items():
            if vid not in ids:
                c.execute("DELETE FROM yt_items WHERE video_id=?", (vid,))  # Removed from the site.
                PROGRESS.pop(vid, None)
    fixed = {"uploading", "uploaded", "failed"}
    claimed = {}
    for r in rows.values():
        if r["video_id"] in ids and r["match_video"] and (r["state"] in fixed or r["match_kind"] in ("owner", "hub", "marker", "uploaded")):
            claimed[r["match_video"]] = r["video_id"]
    by_tt = {d["marker_tt"]: vid for vid, d in channel.items() if d.get("marker_tt")}
    by_hub = {d["marker_hub"]: vid for vid, d in channel.items() if d.get("marker_hub")}
    now = time.time()
    decided = []
    # Exact links first (hub YouTube id, our upload marker), then title and length.
    for e in sorted(entries, key=lambda e: (not e["youtube_id"], e["added"])):
        r = rows.get(e["id"])
        if r and (r["state"] in fixed or r["match_kind"] == "owner"):
            if r["match_kind"] == "owner" and r["state"] == "on_youtube" and r["match_video"] not in channel and s["last_scan"] \
                    and r["match_video"] != e["youtube_id"]:
                decided.append((e, r, dict(state="check", match_kind="", reason="The matched YouTube video is no longer on your channel.")))
            continue
        state, kind, match, cands, reason = "missing", "", None, [], ""
        tt = e["key"][3:]
        if e["youtube_id"]:
            state, kind, match = "on_youtube", "hub", e["youtube_id"]
        elif by_tt.get(tt) or by_hub.get(e["id"]):
            match = by_tt.get(tt) or by_hub.get(e["id"])
            state, kind = ("on_youtube", "marker") if claimed.get(match, e["id"]) == e["id"] else ("check", "")
        else:
            scored = []
            for vid, d in channel.items():
                score = max(title_score(e["title"], d["title"]), title_score(e["caption_title"], d["title"]))
                short = len(normalized(e["title"])) < 12
                if (score >= 0.999 if short else score >= 0.9):
                    scored.append((score, close_length(e["duration"], d.get("duration")), vid, d))
            scored.sort(key=lambda x: (-x[1], -x[0]))
            cands = [{"id": vid, "title": d["title"], "duration": d.get("duration"), "score": round(score, 2),
                      "same_length": same} for score, same, vid, d in scored[:5]]
            strong = [x for x in scored if x[1] and x[2] not in claimed]
            long_enough = len(normalized(e["title"])) >= 12
            if len(strong) == 1 and long_enough and sum(1 for x in scored if x[1]) == 1:
                state, kind, match = "on_youtube", "auto", strong[0][2]
            elif scored:
                state = "check"
                reason = ("Similar titles but different lengths." if not any(x[1] for x in scored)
                          else "More than one possible match." if len(scored) > 1 else "Title is too short to be sure.")
        if match and state == "on_youtube":
            claimed[match] = e["id"]
        decided.append((e, r, dict(state=state, match_kind=kind, match_video=match, candidates=json.dumps(cands), reason=reason)))
    with store.db() as c:
        for e, r, fields in decided:
            if r is None:
                c.execute("""INSERT OR IGNORE INTO yt_items(video_id,source_key,state,approved,updated)
                             VALUES(?,?,?,?,?)""", (e["id"], e["key"], fields["state"], int(s["started"] and s["auto_new"]), now))
            fields["updated"] = now
            c.execute("UPDATE yt_items SET " + ",".join(f"{k}=?" for k in fields) + " WHERE video_id=?",
                      (*fields.values(), e["id"]))
    link_hub_entries()


def link_hub_entries():
    """Approved matches and finished uploads give the hub entry its YouTube id (the own copy still plays first)."""
    with store.db() as c:
        rows = [dict(r) for r in c.execute("""SELECT * FROM yt_items WHERE hub_set=0 AND match_video IS NOT NULL
                  AND ((state='on_youtube' AND approved=1) OR state='uploaded')""")]
    if not rows:
        return 0
    changed = 0
    with store.LOCK:
        cat, rev = store.load_catalog()
        index = {v["id"]: v for v in cat["videos"]}
        done = []
        for r in rows:
            v = index.get(r["video_id"])
            if not v:
                continue
            if not v.get("youtube_id"):
                v["youtube_id"] = r["match_video"]
                changed += 1
            done.append(r["video_id"])
        if changed:
            store.save_catalog(cat, rev)
        with store.db() as c:
            c.executemany("UPDATE yt_items SET hub_set=1 WHERE video_id=?", [(x,) for x in done])
    if changed:
        import public_sync
        public_sync.now()
    return changed


def set_hub_youtube(video_id, old, new):
    """Change a hub entry's YouTube link the Pi set from a match, if it still points at that match."""
    with store.LOCK:
        cat, rev = store.load_catalog()
        v = next((x for x in cat["videos"] if x["id"] == video_id), None)
        if not v or (v.get("youtube_id") or None) not in (old, None, ""):
            return False
        if new:
            v["youtube_id"] = new
        else:
            v["youtube_id"] = ""
            if not v.get("url"):
                return False  # Would leave the entry with nothing to play.
        store.save_catalog(cat, rev)
    import public_sync
    public_sync.now()
    return True


# ---------------------------------------------------------------- uploading

def saved_copy(entry):
    """(path, size, sha256, cleanup) of the owner's saved playback copy, or None while there isn't a checked one."""
    m = entry["media"]
    if not m or m.get("check_status") not in ("ok", "accepted"):
        return None
    if m.get("play_status") == "ready" and m.get("play_key"):
        key, size, sha = m["play_key"], m["play_size"], m["play_sha256"]
    else:
        key, size, sha = m["object_key"], m["size"], m["sha256"]
    return local_file(key, size, sha)


def local_file(object_key, size, sha):
    import media_worker as worker
    client = worker.storage_client()
    if hasattr(client, "path"):
        path = client.path(object_key)
        if not path.is_file() or path.stat().st_size != size:
            raise ItemError("The saved copy is missing or incomplete. Use Saved video checks to download it again.")
        return path, size, sha
    folder = store.STATE / "youtube-upload-cache"
    folder.mkdir(exist_ok=True)
    path = folder / (sha + ".mp4")
    if not path.is_file() or path.stat().st_size != size:
        client.download_to_filename(object_key, str(path))
    return path, size, sha


def shorts_ready(path, duration):
    try:
        import playback
        info = playback.probe(path)
        video = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        rotation = abs(int((video.get("tags") or {}).get("rotate") or 0))
        if rotation in (90, 270):
            w, h = h, w
        seconds = duration or float(info.get("format", {}).get("duration") or 0)
        return h >= w > 0 and 0 < seconds <= 180
    except Exception:
        return False


def clean_text(text, limit, unit="chars"):
    text = re.sub(r"[<>]", "", str(text or "")).strip()
    if unit == "bytes":
        while len(text.encode()) > limit:
            text = text[:-1]
        return text
    if len(text) > limit:
        cut = text[:limit - 1].rsplit(" ", 1)[0]
        text = (cut if len(cut) > limit // 2 else text[:limit - 1]) + "…"
    return text


def metadata(entry, s, shorts):
    v = entry["video"]
    tt = entry["key"][3:]
    try:
        meta = json.loads(entry["job"].get("metadata") or "null") or {}
    except ValueError:
        meta = {}
    caption = str(meta.get("caption") or "").strip() or v.get("title", "")
    site = os.environ.get("PUBLIC_SITE_ORIGIN", "https://wokearewe.com").rstrip("/")
    link = site + "/videos.html#video=" + quote(str(v["id"]), safe="")
    description = caption + "\n\nWatch more on wokearewe.com: " + link
    if shorts and "#shorts" not in description.lower():
        description += "\n#Shorts"
    tags, total = ["wokearewe-tt-" + tt], len("wokearewe-tt-" + tt)
    for t in v.get("tags") or []:
        t = re.sub(r"[<>,\"]", "", str(t).lstrip("#")).strip()[:100]
        cost = len(t) + (2 if " " in t else 0) + 1
        if t and t not in tags and total + cost <= 480:
            tags.append(t)
            total += cost
    return {"snippet": {"title": clean_text(v.get("title"), 100), "description": clean_text(description, 4900, "bytes"),
                        "tags": tags, "categoryId": s["category"]},
            "status": {"privacyStatus": s["visibility"], "selfDeclaredMadeForKids": bool(s["made_for_kids"]),
                       "embeddable": True},
            # Since 16 Nov 2026 YouTube no longer assumes "no paid promotion"; say it explicitly.
            "paidProductPlacementDetails": {"hasPaidProductPlacement": bool(s["paid_promotion"])}}


def initiate(meta, size):
    for attempt in (0, 1):
        r = TRANSPORT.request("POST", UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status,paidProductPlacementDetails"},
                              headers={"Authorization": "Bearer " + access_token(force=attempt == 1),
                                       "Content-Type": "application/json; charset=UTF-8",
                                       "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/mp4"},
                              json_body=meta)
        if r.status == 401 and attempt == 0:
            continue
        if r.status != 200 or not r.headers.get("Location"):
            raise_for(r, item_level=True)
        return r.headers["Location"]


def session_state(uri, size):
    """Ask YouTube how much of an upload arrived. Returns ("done", video) / ("offset", n) / ("gone", None)."""
    r = TRANSPORT.request("PUT", uri, headers={"Authorization": "Bearer " + access_token(),
                                               "Content-Length": "0", "Content-Range": f"bytes */{size}"})
    return _upload_answer(r)


def _upload_answer(r):
    if r.status in (200, 201):
        return "done", r.json()
    if r.status == 308:
        m = re.fullmatch(r"bytes=0-(\d+)", r.headers.get("Range", ""))
        return "offset", int(m[1]) + 1 if m else 0
    if r.status in (404, 410):
        return "gone", None
    raise_for(r, item_level=True)


def send(item_id, uri, path, size, offset):
    with open(path, "rb") as f:
        while True:
            s = settings()
            if s["paused"] or not s["connected"] or not s["started"]:
                return "stopped", None
            f.seek(offset)
            chunk = f.read(CHUNK)
            end = offset + len(chunk) - 1
            r = TRANSPORT.request("PUT", uri, headers={"Authorization": "Bearer " + access_token(),
                                                       "Content-Length": str(len(chunk)),
                                                       "Content-Range": f"bytes {offset}-{end}/{size}"}, data=chunk)
            outcome, value = _upload_answer(r)
            if outcome == "done":
                return outcome, value
            if outcome == "gone":
                return outcome, None
            offset = value
            PROGRESS[item_id] = {"sent": offset, "size": size, "at": time.time()}


def can_upload(s, now=None):
    now = now or time.time()
    if not (s["connected"] and right_channel(s) and s["started"]) or s["paused"]:
        return False, ""
    if s["visibility"] not in VISIBILITY:
        return False, "Choose who can see the uploads first."
    if now < (s["start_after"] or 0):
        return False, "Waiting for your chosen start time."
    if now < (s["paused_until"] or 0):
        return False, s["pause_reason"]
    return True, ""


def next_item(entries):
    with store.db() as c:
        rows = {r["video_id"]: dict(r) for r in c.execute("SELECT * FROM yt_items WHERE state IN ('uploading','missing')")}
    now = time.time()
    flying = [e for e in entries if rows.get(e["id"], {}).get("state") == "uploading"]
    if flying:
        return flying[0], rows[flying[0]["id"]]
    ready = [e for e in entries if rows.get(e["id"], {}).get("state") == "missing" and rows[e["id"]]["approved"]
             and rows[e["id"]]["retry_at"] <= now and e["media"] and e["media"].get("check_status") in ("ok", "accepted")
             and not store.needs_title(e["video"])]
    ready.sort(key=lambda e: e["added"])
    return (ready[0], rows[ready[0]["id"]]) if ready else (None, None)


def find_on_channel(entry):
    tt = entry["key"][3:]
    for vid, d in channel_videos().items():
        if d.get("marker_tt") == tt or d.get("marker_hub") == entry["id"]:
            return vid, d
    return None, None


def finish(entry, video_id, privacy=None, adopted=False):
    s = settings()
    note = ""
    if privacy and s["visibility"] in ("public", "unlisted") and privacy == "private":
        note = "YouTube made this video private. Check YouTube Studio, and the Google project's audit state."
        update(private_results=s["private_results"] + 1)
    set_item(entry["id"], state="uploaded", match_video=video_id, youtube_id=video_id, match_kind="uploaded",
             privacy=privacy, uploaded_at=time.time(), reason=note or ("Found the earlier upload on your channel." if adopted else ""),
             retry_at=0, recheck_at=0)
    edit_private(lambda d: d.get("sessions", {}).pop(entry["id"], None))
    PROGRESS.pop(entry["id"], None)
    now = time.time()
    update(last_upload=now, next_upload_at=now + spacing(s["per_day"]))
    link_hub_entries()
    checkpoint()


def upload_tick():
    s = roll_day(settings())
    ok, why = can_upload(s)
    if not ok:
        return why
    entries = tiktok_entries()
    entry, row = next_item(entries)
    if not entry:
        return "nothing waiting"
    now = time.time()
    if row["state"] == "missing" and now < (s["next_upload_at"] or 0):
        return "spacing"
    if row["retry_at"] > now:
        return "retry later"
    try:
        if s["end_card"]:
            copy = endcard_copy(entry)
            if copy is None:
                return "end card"
        else:
            copy = saved_copy(entry)
        if copy is None:
            set_item(entry["id"], reason="Waiting for its saved copy to pass the picture and sound check.")
            return "no copy"
        path, size, sha = copy
        session = private().get("sessions", {}).get(entry["id"])
        outcome, value = None, None
        if session and session.get("sha256") == sha and session.get("size") == size:
            outcome, value = session_state(session["uri"], size)
            if outcome == "gone":
                edit_private(lambda d: d.get("sessions", {}).pop(entry["id"], None))
            if outcome == "done":
                v = value or {}
                return finish(entry, v.get("id"), (v.get("status") or {}).get("privacyStatus")) or "uploaded"
        if outcome != "offset":
            # Before any fresh upload, look on the channel for an earlier one of this video.
            scan(full=False, max_pages=1)
            found, data = find_on_channel(entry)
            if found:
                return finish(entry, found, data.get("privacy"), adopted=True) or "adopted"
            if row["state"] == "uploading":
                if row["attempts"] >= 3:
                    edit_private(lambda d: d.get("sessions", {}).pop(entry["id"], None))
                    set_item(entry["id"], state="failed", retry_at=0, recheck_at=0,
                             reason="Three uploads were interrupted before finishing. Check YouTube Studio for a partial upload, then press Try again.")
                    PROGRESS.pop(entry["id"], None)
                    checkpoint()
                    return "failed"
                if not row["recheck_at"]:
                    set_item(entry["id"], recheck_at=now + RECHECK_SECONDS, retry_at=now + RECHECK_SECONDS,
                             reason="An upload was interrupted. Checking your channel again before trying once more, so it can't appear twice.")
                    return "recheck"
                if now < row["recheck_at"]:
                    return "recheck"
            if s["uploads_today"] >= min(s["per_day"], DAILY_INSERTS):
                update(paused_until=next_reset() + 300, pause_reason="Today's uploads are done. More start after midnight Pacific time.")
                return "daily limit"
            update(uploads_today=s["uploads_today"] + 1)  # Counted before asking, so a crash never under-counts.
            uri = initiate(metadata(entry, s, shorts_ready(path, entry["duration"])), size)
            edit_private(lambda d: d.setdefault("sessions", {}).__setitem__(
                entry["id"], {"uri": uri, "size": size, "sha256": sha, "started": time.time()}))
            set_item(entry["id"], state="uploading", attempts=row["attempts"] + 1, reason="", recheck_at=0)
            checkpoint()
            value = 0
        PROGRESS[entry["id"]] = {"sent": value, "size": size, "at": time.time()}
        set_item(entry["id"], state="uploading", reason="")
        outcome, video = send(entry["id"], uri if outcome != "offset" else session["uri"], path, size, value)
        if outcome == "done":
            return finish(entry, video.get("id"), (video.get("status") or {}).get("privacyStatus")) or "uploaded"
        if outcome == "gone":
            edit_private(lambda d: d.get("sessions", {}).pop(entry["id"], None))
            set_item(entry["id"], retry_at=0)
            return "session gone"
        return outcome
    except QuotaPause as e:
        update(paused_until=e.until, pause_reason=str(e))
        return "quota"
    except AuthError as e:
        if settings()["connected"]:
            update(error=str(e), paused_until=time.time() + 1800, pause_reason="Waiting: " + str(e))
        return "auth"
    except ItemError as e:
        edit_private(lambda d: d.get("sessions", {}).pop(entry["id"], None))
        set_item(entry["id"], state="failed", reason=str(e), retry_at=0)
        PROGRESS.pop(entry["id"], None)
        checkpoint()
        return "failed"
    except (Retry, YouTubeError) as e:
        tries = (item(entry["id"]) or {}).get("attempts", 0)
        set_item(entry["id"], reason=str(e) + " Any upload already started continues from where it stopped.",
                 retry_at=time.time() + min(3600, 60 * 2 ** min(6, tries)))
        return "retry"


# ---------------------------------------------------------------- optional end-card version (from you2mp3)

def endcard_copy(entry):
    """A separate copy of this TikTok post with the you2mp3 end card, used only for YouTube."""
    import media_worker as worker
    key = entry["key"]
    with store.db() as c:
        row = c.execute("SELECT * FROM yt_endcards WHERE source_key=?", (key,)).fetchone()
        if not row:
            c.execute("INSERT INTO yt_endcards(source_key,idempotency,status,updated) VALUES(?,?,?,?)",
                      (key, "waw-ytend-" + key[3:], "requested", time.time()))
            row = c.execute("SELECT * FROM yt_endcards WHERE source_key=?", (key,)).fetchone()
    row = dict(row)

    def save(**f):
        f["updated"] = time.time()
        with store.db() as c:
            c.execute("UPDATE yt_endcards SET " + ",".join(f"{k}=?" for k in f) + " WHERE source_key=?", (*f.values(), key))
    if row["status"] == "ready":
        return local_file(row["object_key"], row["size"], row["sha256"])
    if row["status"] == "failed":
        set_item(entry["id"], state="failed", reason=row["reason"])
        return None
    if not os.environ.get("YOU2MP3_API_KEY"):
        set_item(entry["id"], reason="End-card versions come from you2mp3, and its connection key isn't set on the Pi.")
        return None
    try:
        if not row["upstream_id"]:
            url = entry["job"].get("url") or entry["video"].get("source_url")
            result = worker.api("/videos", {"url": url, "format": "mp4", "container": "mp4", "quality": "best",
                                            "addEndCard": True}, row["idempotency"])
            if not isinstance(result.get("id"), int):
                raise ValueError("you2mp3 didn't return a request number")
            save(upstream_id=result["id"], status="processing")
            row["upstream_id"] = result["id"]
        result = worker.api("/videos/" + str(int(row["upstream_id"])))
        if result.get("status") in ("error", "cancelled") or any(c.get("status") == "failed" for c in result.get("endCards", [])):
            save(status="failed", reason="you2mp3 couldn't make the end-card version. Turn end cards off for YouTube, or press Try again later.")
            return None
        name = result.get("outputFilename")
        if result.get("status") != "done" or not name or not any(
                c.get("filename") == name and c.get("status") == "added" for c in result.get("endCards", [])):
            set_item(entry["id"], reason="Waiting for the end-card version from you2mp3.")
            return None
        if "/" in name or "\\" in name:
            raise ValueError("you2mp3 sent an invalid file name")
        with tempfile.TemporaryDirectory(prefix="waw-ytend-", dir=store.STATE) as folder:
            video = Path(folder) / "endcard.mp4"
            response = worker.api("/files/" + quote(name, safe=""), binary=True)
            digest, size = hashlib.sha256(), 0
            try:
                with video.open("wb") as f:
                    for chunk in response.iter_content(1024 * 1024):
                        digest.update(chunk)
                        size += len(chunk)
                        f.write(chunk)
            finally:
                response.close()
            import media_check
            media_check.inspect(video, expected=None, full_decode=False)
            object_key = "wokearewe/youtube-endcards/" + key.replace(":", "-") + "/" + digest.hexdigest() + ".mp4"
            worker.storage_client().upload_from_filename(object_key, str(video))
        save(status="ready", object_key=object_key, size=size, sha256=digest.hexdigest(), reason="")
        return local_file(object_key, size, digest.hexdigest())
    except worker.UpstreamError as e:
        set_item(entry["id"], reason=str(e), retry_at=time.time() + 600)
        return None
    except Exception as e:
        import media_check
        if isinstance(e, media_check.Problem):
            save(status="failed", reason="The end-card version from you2mp3 didn't pass the picture and sound check.")
            set_item(entry["id"], state="failed", reason="The end-card version from you2mp3 didn't pass the picture and sound check.")
            return None
        reason = str(e) if isinstance(e, ValueError) else "The end-card version couldn't be saved."
        set_item(entry["id"], reason=reason, retry_at=time.time() + 600)
        return None


# ---------------------------------------------------------------- owner actions

def configure(body):
    allowed = {"auto_new", "visibility", "category", "made_for_kids", "end_card", "paid_promotion", "per_day", "start_after", "audit"}
    if not isinstance(body, dict) or not body.keys() <= allowed:
        raise ValueError("Unknown YouTube upload setting")
    for name in ("auto_new", "made_for_kids", "end_card", "paid_promotion"):
        if name in body and not isinstance(body[name], bool):
            raise ValueError("Switches must be on or off")
    if "visibility" in body and body["visibility"] not in VISIBILITY:
        raise ValueError("Choose public, unlisted or private")
    if "category" in body and body["category"] not in CATEGORIES:
        raise ValueError("Choose a YouTube category")
    if "per_day" in body and (type(body["per_day"]) is not int or not 1 <= body["per_day"] <= DAILY_INSERTS):
        raise ValueError(f"Uploads per day must be between 1 and {DAILY_INSERTS}")
    if "start_after" in body and (type(body["start_after"]) not in (int, float) or body["start_after"] < 0):
        raise ValueError("Choose a start date and time")
    if "audit" in body and body["audit"] not in AUDIT_STATES:
        raise ValueError("Unknown audit state")
    update(**body, next_upload_at=0)
    checkpoint()
    return status()


def start():
    s = settings()
    if not s["connected"]:
        raise ValueError("Connect YouTube first")
    if not right_channel(s):
        raise ValueError("You're signed in to a different channel than " + expected_handle() + ". Disconnect and sign in to the right one.")
    if s["visibility"] not in VISIBILITY:
        raise ValueError("Choose who can see the uploads first")
    if s["scan_status"] != "done":
        raise ValueError("Wait for the channel check to finish, so nothing already there is uploaded again")
    refresh_items()
    with store.db() as c:
        c.execute("UPDATE yt_items SET approved=1 WHERE state IN ('missing','on_youtube')")
    update(started=True, paused=False, started_at=time.time(), error="")
    link_hub_entries()
    checkpoint()
    return status()


def pause(on):
    if not isinstance(on, bool):
        raise ValueError("Pause must be on or off")
    update(paused=on)
    checkpoint()
    return status()


def stop():
    update(started=False)
    checkpoint()
    return status()


def correct(video_id, action, youtube_id=None):
    r = item(video_id)
    if not r:
        raise LookupError("Video not found")
    if r["state"] == "uploading":
        raise ValueError("This video is uploading now. Pause uploads first.")
    if action == "match":
        if not isinstance(youtube_id, str) or youtube_id not in channel_videos():
            raise ValueError("Choose one of your channel's videos")
        taken = None
        with store.db() as c:
            taken = c.execute("SELECT video_id FROM yt_items WHERE match_video=? AND video_id!=? AND state IN ('on_youtube','uploaded')",
                              (youtube_id, video_id)).fetchone()
        if taken:
            raise ValueError("That YouTube video is already matched to another site video")
        linked = bool(r["hub_set"]) or r["match_kind"] == "hub"
        if linked and r["match_video"] and r["match_video"] != youtube_id:
            if not set_hub_youtube(video_id, r["match_video"], youtube_id):
                raise ValueError("This video's YouTube link was changed in Videos. Edit it there.")
        set_item(video_id, state="on_youtube", match_video=youtube_id, match_kind="owner", approved=1, reason="",
                 hub_set=int(linked and bool(r["match_video"])))
    elif action == "upload":
        if r["state"] == "uploaded":
            raise ValueError("This video was already uploaded")
        if r["state"] == "on_youtube" and (r["hub_set"] or r["match_kind"] == "hub") and r["match_video"]:
            if not set_hub_youtube(video_id, r["match_video"], None):
                raise ValueError("This video's YouTube link was changed in Videos, or it only plays from YouTube. Edit it there.")
        set_item(video_id, state="missing", match_video=None, match_kind="owner", approved=1, reason="", retry_at=0,
                 recheck_at=0, hub_set=0, attempts=0 if r["state"] == "failed" else r["attempts"])
    elif action == "skip":
        set_item(video_id, state="skipped", match_kind="owner", approved=0, reason="")
    elif action == "reset":
        set_item(video_id, match_kind="", state="missing", match_video=None, reason="", retry_at=0)
    else:
        raise ValueError("Unknown choice")
    if action != "reset":
        link_hub_entries()
    else:
        refresh_items()
    checkpoint()
    return status()


def request_scan():
    s = settings()
    if not s["connected"]:
        raise ValueError("Connect YouTube first")
    update(scan_requested=True, scan_status="waiting")
    return status()


# ---------------------------------------------------------------- status and loop

def status(origin=None, host=None):
    s = roll_day(settings())
    entries = {e["id"]: e for e in tiktok_entries()}
    channel = channel_videos()
    with store.db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM yt_items")]
    groups = {"on_youtube": [], "check": [], "missing": [], "failed": [], "skipped": []}
    for r in rows:
        e = entries.get(r["video_id"])
        if not e:
            continue
        match = channel.get(r["match_video"] or "") or {}
        out = {"id": r["video_id"], "title": e["title"], "added": e["added"], "duration": e["duration"],
               "state": r["state"], "kind": r["match_kind"], "approved": bool(r["approved"]), "reason": r["reason"],
               "youtube_id": r["match_video"], "match_title": match.get("title"), "match_duration": match.get("duration"),
               "privacy": r["privacy"] or match.get("privacy"), "candidates": json.loads(r["candidates"] or "[]"),
               "has_copy": bool(e["media"] and e["media"].get("check_status") in ("ok", "accepted")),
               "needs_title": store.needs_title(e["video"]), "progress": PROGRESS.get(r["video_id"])}
        group = {"uploaded": "on_youtube", "uploading": "missing"}.get(r["state"], r["state"])
        groups.setdefault(group, []).append(out)
    for g in groups.values():
        g.sort(key=lambda x: x["added"])
    waiting = [x for x in groups["missing"] if x["state"] == "missing" and x["approved"]]
    ok, why = can_upload(s)
    return {
        "configured": bool(client()), "connected": s["connected"], "channel": s["channel"],
        "expected_handle": expected_handle(), "right_channel": right_channel(s),
        "redirect_uri": redirect_uri(origin, host), "callback_path": CALLBACK,
        "settings": {k: s[k] for k in ("auto_new", "visibility", "category", "made_for_kids", "end_card", "paid_promotion", "per_day",
                                       "start_after", "audit")},
        "categories": CATEGORIES, "started": s["started"], "paused": s["paused"], "can_upload": ok, "blocked": why,
        "paused_until": s["paused_until"], "pause_reason": s["pause_reason"] if time.time() < (s["paused_until"] or 0) else "",
        "next_upload_at": s["next_upload_at"], "last_upload": s["last_upload"], "error": s["error"],
        "scan": {"status": s["scan_status"], "error": s["scan_error"], "last": s["last_scan"], "videos": len(channel)},
        "quota": {"uploads_today": s["uploads_today"], "per_day": s["per_day"], "units_today": s["units_today"],
                  "resets_at": next_reset(), "insert_limit": DAILY_INSERTS},
        "private_results": s["private_results"], "waiting": len(waiting),
        "counts": {k: len(v) for k, v in groups.items()}, "groups": groups,
        "channel_videos": sorted(({"id": k, "title": d["title"], "duration": d.get("duration"), "privacy": d.get("privacy")}
                                  for k, d in channel.items()), key=lambda x: x["title"].lower())[:5000],
        "heartbeat": LAST_HEARTBEAT,
    }


def tick():
    global _last_refresh
    s = roll_day(settings())
    if not s["connected"]:
        return
    if s["scan_requested"] or not s["last_scan"] or s["last_scan"] < time.time() - 86400:
        if s["scan_requested"] or time.time() >= max(s["paused_until"] or 0, s["scan_retry_at"] or 0):
            try:
                update(scan_status="scanning")
                scan(full=True)
            except QuotaPause as e:
                update(scan_status="failed", scan_error=str(e), scan_requested=False, paused_until=e.until, pause_reason=str(e))
            except YouTubeError as e:
                update(scan_status="failed", scan_error=str(e), scan_requested=False, scan_retry_at=time.time() + 900)
            _last_refresh = 0
    if time.time() - _last_refresh > 30:
        refresh_items()
        _last_refresh = time.time()
    upload_tick()


def run():
    global LAST_HEARTBEAT
    while True:
        LAST_HEARTBEAT = time.time()
        if RUN_LOCK.acquire(blocking=False):
            try:
                tick()
            except Exception:
                update(error="The YouTube uploader hit an unexpected problem and will try again shortly.")
            finally:
                RUN_LOCK.release()
        time.sleep(5)
