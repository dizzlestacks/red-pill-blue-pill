"""Single, rate-limited worker. Only verified independently stored bytes count as backups."""
import email.utils
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.parse import quote
import requests
import admin_store as store
import tiktok_watch
import watch_provider
import media_check

BASE = "https://you2mp3.music/api/v1"
NETWORK_LOCK = threading.Lock()
LAST_REQUEST = 0.0
BACKOFF_UNTIL = 0.0
STORAGE_ERROR = ""


class UpstreamError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


MAX_RETRY_AFTER = 3600


def retry_after_seconds(value):
    """Retry-After as delta-seconds or HTTP-date; at least 60s, at most one hour."""
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, IndexError, OverflowError):
            delay = 60
    return min(MAX_RETRY_AFTER, max(60, delay))


def persisted_backoff(until=None):
    """Wall-clock Retry-After deadline shared across restarts (monotonic time is not)."""
    with store.db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS api_backoff (id INTEGER PRIMARY KEY CHECK(id=1), until REAL NOT NULL)")
        if until is not None:
            c.execute("INSERT INTO api_backoff VALUES(1,?) ON CONFLICT(id) DO UPDATE SET until=excluded.until", (until,))
            return until
        row = c.execute("SELECT until FROM api_backoff WHERE id=1").fetchone()
    # Never trust a deadline further out than the cap (e.g. a clock change).
    return min(row[0], time.time() + MAX_RETRY_AFTER) if row else 0


def api(path, payload=None, idempotency=None, binary=False):
    global LAST_REQUEST, BACKOFF_UNTIL
    # Single worker + shared gate: <= 30 requests/minute, leaving room for account use.
    with NETWORK_LOCK:
        time.sleep(max(0, LAST_REQUEST + 2.1 - time.monotonic(), BACKOFF_UNTIL - time.monotonic(),
                       persisted_backoff() - time.time()))
        headers = {"Authorization": "Bearer " + os.environ.get("YOU2MP3_API_KEY", "")}
        if idempotency:
            headers["Idempotency-Key"] = idempotency
        LAST_REQUEST = time.monotonic()
        try:
            r = requests.request("POST" if payload is not None else "GET", BASE + path,
                                 headers=headers, json=payload, timeout=(15, 90),
                                 stream=binary, allow_redirects=False)
        except requests.RequestException:
            raise UpstreamError(0, "Connection timed out or unavailable; request identity retained for safe retry")
        if r.status_code == 429:
            delay = retry_after_seconds(r.headers.get("Retry-After", 60))
            BACKOFF_UNTIL = time.monotonic() + delay
            persisted_backoff(time.time() + delay)
        if not r.ok or 300 <= r.status_code < 400:
            status = r.status_code
            r.close()
            messages = {401: "you2mp3 refused the connection key. Check YOU2MP3_API_KEY on the Pi.",
                        403: "This you2mp3 connection isn't allowed to do this.",
                        402: "Your you2mp3 download allowance for this month is used up.",
                        409: "you2mp3 couldn't confirm whether this download started.",
                        410: "you2mp3 no longer remembers this request.",
                        429: "you2mp3 asked the Pi to slow down. It will try again shortly."}
            raise UpstreamError(status, messages.get(status, f"Media service returned HTTP {status}"))
        if binary:
            return r
        try:
            return r.json()
        except ValueError:
            raise UpstreamError(502, "Media service returned an invalid response")
        finally:
            r.close()


def storage_client():
    if os.environ.get("STORAGE_BACKEND", "disk") == "replit":
        from replit.object_storage import Client
        return Client()
    if os.environ.get("STORAGE_BACKEND", "disk") != "disk":
        raise ValueError("Unsupported STORAGE_BACKEND")
    from disk_storage import DiskStorage
    return DiskStorage()


def storage_status():
    global STORAGE_ERROR
    try:
        client = storage_client()
        if os.environ.get("STORAGE_BACKEND", "disk") == "disk":
            import tempfile
            with tempfile.TemporaryFile(dir=store.STATE) as probe:
                probe.write(b"storage check")
                probe.flush()
                os.fsync(probe.fileno())
        else:
            client.exists("wokearewe/storage-check")
        STORAGE_ERROR = ""
        return True, "Permanent copies use " + ("the configured data folder" if os.environ.get("STORAGE_BACKEND", "disk") == "disk" else "App Storage (optional cloud backend)")
    except Exception:
        STORAGE_ERROR = "Storage unavailable. Check the data folder/mount, free space and permissions (or optional Replit bucket configuration). No backup will be marked complete until copying succeeds."
        return False, STORAGE_ERROR


def verify_media(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                            capture_output=True, timeout=60, check=True)
    info = json.loads(result.stdout)
    types = {s.get("codec_type") for s in info.get("streams", [])}
    if not {"video", "audio"} <= types:
        raise ValueError("Output must contain both playable video and audio streams")
    duration = float(info.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise ValueError("Output has no valid duration")
    # Decode a sample, rather than trusting the container metadata alone.
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-t", "2", "-f", "null", "-"],
                   capture_output=True, check=True, timeout=60)
    return duration


class Removed(Exception):
    """The owner removed this item while it was downloading."""


def file_path(file_url):
    """you2mp3's output.fileUrl ("/api/v1/files/...") as a path under the API base."""
    from urllib.parse import urlparse
    parsed = urlparse(str(file_url or ""))
    if parsed.scheme and (parsed.scheme != "https" or parsed.hostname != "you2mp3.music"):
        raise ValueError("Media service returned a file link on another site")
    path = parsed.path
    if path.startswith("/api/v1/"):
        path = path[len("/api/v1"):]
    if not path.startswith("/files/") or ".." in path.split("/") or len(path) > 400:
        raise ValueError("Media service returned an invalid file link")
    return path


def copy_media(job, filename=None, path=None):
    if path is None:
        if not isinstance(filename, str) or "/" in filename or "\\" in filename or len(filename) > 255:
            raise ValueError("Media service returned an invalid filename")
        path = "/files/" + quote(filename, safe="")
    tiktok_watch.disk_guard()
    with tempfile.TemporaryDirectory(prefix="waw-media-", dir=store.STATE) as folder:
        video = Path(folder) / "source.mp4"
        response = api(path, binary=True)
        digest, size = hashlib.sha256(), 0
        try:
            with video.open("wb") as f:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    # Reserve room for source, atomic durable copy and verification copy.
                    tiktok_watch.disk_guard(extra=len(chunk) + 2 * (size + len(chunk)))
                    size += len(chunk)
                    if size > 2 * 1024**3:
                        raise ValueError("Video exceeds the 2 GiB import limit")
                    digest.update(chunk)
                    f.write(chunk)
            expected = response.headers.get("Content-Length")
            if expected and size != int(expected):
                raise ValueError("Download byte count does not match")
        finally:
            response.close()
        import media_check
        with store.db() as c:
            existing = c.execute("SELECT * FROM media WHERE source_key=?", (job["source_key"],)).fetchone()
        expected = None
        if existing and existing["check_status"] in media_check.GOOD:
            expected = json.loads(existing["check_details"] or "{}").get("duration")
        problem = None
        try:
            # Picture, sound, silence and length are checked before anything is saved.
            duration = media_check.inspect(video, expected=expected, full_decode=False)["duration"]
        except media_check.Problem as issue:
            if existing:
                # Never replace a saved copy with a worse download.
                raise
            problem, duration = issue, issue.details.get("duration") or 0
        tiktok_watch.disk_guard(extra=2 * size + 1024 * 1024)
        client = storage_client()
        object_key = "wokearewe/backups/" + job["source_key"].replace(":", "-") + "/" + digest.hexdigest() + ".mp4"
        client.upload_from_filename(object_key, str(video))
        # Read back durable bytes to verify the copy, not just successful upload status.
        verify = Path(folder) / "verify.mp4"
        client.download_to_filename(object_key, str(verify))
        check = hashlib.sha256()
        with verify.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                check.update(chunk)
        if verify.stat().st_size != size or check.hexdigest() != digest.hexdigest():
            raise ValueError("Permanent storage verification failed")
        poster_key = None
        poster = Path(folder) / "poster.jpg"
        try:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=640:-2", str(poster)], check=True, capture_output=True, timeout=60)
            poster_key = object_key + ".jpg"
            client.upload_from_filename(poster_key, str(poster))
        except Exception:
            poster_key = None
        with store.LOCK, store.db() as c:
            if not c.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone():
                # Removed from the queue during the download: keep nothing.
                for key in (object_key, poster_key):
                    if key and not c.execute("SELECT 1 FROM media WHERE object_key=? OR poster_key=? OR play_key=?",
                                             (key, key, key)).fetchone():
                        try:
                            client.delete(key)
                        except Exception:
                            pass
                raise Removed()
            c.execute("""INSERT OR REPLACE INTO media
                (source_key,object_key,poster_key,size,sha256,duration,created) VALUES(?,?,?,?,?,?,?)""",
                      (job["source_key"], object_key, poster_key, size, digest.hexdigest(), duration, time.time()))
            if problem:
                # Kept so the owner can decide (for example "Keep without sound"), but never offered for publishing.
                c.execute("UPDATE media SET check_status=?, check_code=?, check_reason=?, check_details=?, check_updated=? WHERE source_key=?",
                          (media_check.PROBLEM, problem.code, problem.reason, json.dumps(problem.details), time.time(), job["source_key"]))
        if problem:
            raise problem


def process(job):
    import back_catalog
    import media_check
    if not back_catalog.allowed(job):
        return
    if job.get("automatic"):
        job["auto_publish"] = tiktok_watch.settings()["auto_publish"]
    if job["kind"] == "unsupported":
        store.update_job(job["id"], status="blocked", reason="Only individual YouTube videos and your canonical TikTok posts can be backed up; playlists and other sources are not supported.")
        return
    if not os.environ.get("YOU2MP3_API_KEY"):
        store.update_job(job["id"], status="waiting for connection", reason="YOU2MP3_API_KEY is not configured")
        return
    try:
        if job["status"] == "imported" and job.get("automatic") and job.get("auto_publish"):
            store.publish(job["id"])
            return
        tiktok_watch.disk_guard()
        youtube_import = job["kind"] == "youtube" and (not job["upstream_id"] or job.get("upstream_kind") == YT_IMPORT)
        capabilities = {} if youtube_import else api("/capabilities")
        restriction = capabilities.get("restriction")
        if job["kind"] == "youtube" and not youtube_import and restriction == "tiktok-imports":
            store.update_job(job["id"], status="blocked", reason=YT_OFF)
            return
        if job["kind"] == "tiktok" and restriction not in (None, "tiktok-imports"):
            store.update_job(job["id"], status="blocked", reason="Connection restriction is not supported")
            return
        ready, message = storage_status()
        if not ready:
            store.update_job(job["id"], status="blocked", reason=message)
            return
        if job["status"] == "problem":
            # Automatic or requested re-download: a fresh request to the media service, same settings.
            job = media_check.prepare_redownload(job)
            youtube_import = job["kind"] == "youtube"
        if youtube_import:
            process_youtube(job)
            return
        if not job["upstream_id"]:
            with store.LOCK:
                job = store.get_job(job["id"])
                payload = json.loads(job["payload"]) if job["payload"] else {
                    "url": job["url"], "format": "mp4", "container": "mp4", "quality": "best"}
                if not job["payload"] and job.get("add_end_card") is not None:
                    payload = watch_provider.PROVIDER.end_card_payload(payload, bool(job["add_end_card"]))
                if not job["payload"] and job["kind"] == "tiktok":
                    payload["addEndCard"] = False
                tiktok_watch.admit(job)
                # Persist before network I/O; a crash replays exactly the same intended request.
                store.update_job(job["id"], payload=json.dumps(payload), status="processing", reason="Submitting source")
            import durable_state
            durable_state.checkpoint(required=True)
            result = api("/videos", payload, job["idempotency"])
            if not isinstance(result.get("id"), int):
                raise ValueError("Media service did not return a job ID")
            job["upstream_id"] = result["id"]
            store.update_job(job["id"], upstream_id=result["id"])
        result = api("/videos/" + str(job["upstream_id"]))
        status = result.get("status")
        if status == "done" and result.get("outputFilename"):
            cards = result.get("endCards", [])
            submitted = json.loads(store.get_job(job["id"])["payload"] or "{}")
            if submitted.get("addEndCard") is True and not any(
                    card.get("filename") == result["outputFilename"] and card.get("status") == "added"
                    for card in cards):
                if any(card.get("status") == "failed" for card in cards):
                    raise ValueError("The service could not add the end card. Review the job before retrying.")
                store.update_job(job["id"], status="processing", reason="Waiting for confirmed end-card output readiness")
                return
            if any(card.get("status") == "failed" for card in cards):
                raise ValueError("The service could not add the end card. Review the job before retrying.")
            if not watch_provider.PROVIDER.final_ready(result):
                store.update_job(job["id"], status="processing", reason="Waiting for the final end-card output")
                return
            save_copy(job, filename=result["outputFilename"])
        elif status in ("error", "cancelled"):
            # Do not leak arbitrary upstream errors (which may include credentials or internal URLs).
            raise ValueError("Media service " + status + ". Check the source's availability and connection permissions.")
        else:
            store.update_job(job["id"], status="processing", reason="Media service: " + str(status or "pending")[:40])
    except media_check.Problem as p:
        media_check.flag_problem(store.get_job(job["id"]), p.reason)
    except (tiktok_watch.Paused, watch_provider.Unavailable) as e:
        store.update_job(job["id"], status="blocked", reason=str(e))
    except Removed:
        return
    except UpstreamError as e:
        if e.status in (409, 410):
            uncertain(job)
            return
        retryable = e.status in (0, 429, 502, 503, 504)
        store.update_job(job["id"], status="processing" if retryable else ("blocked" if e.status in (401, 402, 403, 409, 410) else "failed"),
                         reason=str(e))
    except Exception as e:
        reason = str(e) if isinstance(e, ValueError) else "Copy or verification failed. Check storage access and retry; no backup was published."
        store.update_job(job["id"], status="failed", reason=reason)


YT_IMPORT = "youtube-import"
YT_OFF = "YouTube copies are not switched on for this connection yet."
CHECKING = "Checking with you2mp3 whether this download started."
UNCONFIRMED = "you2mp3 couldn't confirm whether this download started. Retry starts a fresh download."


def save_copy(job, filename=None, path=None):
    """Copy the finished file, check picture and sound, then mark it ready (and publish if automatic)."""
    import media_check
    store.update_job(job["id"], status="copying", reason="Copying the video to the Pi")
    if path is None:
        copy_media(job, filename)
    else:
        copy_media(job, path=path)
    store.update_job(job["id"], status="copying", reason="Checking picture and sound play from start to end")
    outcome = media_check.verify(job["source_key"])
    if not media_check.finish_import(store.get_job(job["id"]), outcome):
        return
    if job.get("automatic") and job.get("auto_publish") and store.get_job(job["id"])["status"] == "imported":
        store.publish(job["id"])


def uncertain(job):
    """you2mp3 couldn't confirm a request: ask again a few times (the same request), then offer Retry/Remove."""
    checks = (store.get_job(job["id"]).get("uncertain_checks") or 0) + 1
    if checks < 4:
        store.update_job(job["id"], status="blocked", reason=CHECKING, uncertain_checks=checks)
    else:
        store.update_job(job["id"], status="failed", reason=UNCONFIRMED, uncertain_checks=checks, fresh_retry=1)


def youtube_payload(job):
    meta = json.loads(job.get("metadata") or "null") or {}
    if meta.get("youtube_watch") and not meta.get("manual_fallback"):
        return {"mode": "watched", "videoId": job["source_key"][3:], "requestId": job["idempotency"], "addEndCard": False}
    return {"mode": "manual", "url": job["url"], "requestId": job["idempotency"], "addEndCard": False}


def process_youtube(job):
    """Single YouTube videos through you2mp3's YouTube import API (manual links and watched sources)."""
    import durable_state
    if not job["upstream_id"]:
        with store.LOCK:
            job = store.get_job(job["id"])
            payload = json.loads(job["payload"]) if job["payload"] else None
            if not payload or "mode" not in payload or payload.get("requestId") != job["idempotency"]:
                payload = youtube_payload(job)
            tiktok_watch.admit(job)
            store.update_job(job["id"], payload=json.dumps(payload), status="processing", reason="Asking you2mp3 for a copy")
        durable_state.checkpoint(required=True)
        try:
            result = api("/youtube-watch/imports", payload, job["idempotency"])
        except UpstreamError as e:
            if e.status == 403 and payload["mode"] == "watched":
                # No longer listed by a watched source: ask for it as a single video instead.
                meta = json.loads(job.get("metadata") or "null") or {}
                with store.db() as c:
                    c.execute("UPDATE jobs SET metadata=?, payload=NULL WHERE id=?",
                              (json.dumps(dict(meta, manual_fallback=True)), job["id"]))
                store.update_job(job["id"], status="queued", reason="")
                return
            if e.status in (403, 404, 501):
                store.update_job(job["id"], status="blocked", reason=YT_OFF)
                return
            if e.status == 400:
                store.update_job(job["id"], status="failed", reason="you2mp3 only copies single public YouTube videos, not channels or playlists.")
                return
            raise
        item = result.get("import") if isinstance(result.get("import"), dict) else {}
        number = result.get("id", item.get("id"))
        if type(number) is not int:
            raise ValueError("you2mp3 didn't return an import number")
        store.update_job(job["id"], upstream_id=number, upstream_kind=YT_IMPORT)
        job = store.get_job(job["id"])
    data = api(f"/youtube-watch/imports/{int(job['upstream_id'])}")
    item = data.get("import") if isinstance(data.get("import"), dict) else data
    status = str(item.get("status") or "")
    why = str(item.get("reason") or item.get("error") or "")[:200]
    if item.get("transferReady") is True:
        save_copy(job, path=file_path((item.get("output") or {}).get("fileUrl")))
    elif status == "rejected":
        store.update_job(job["id"], status="failed", reason="YouTube won't allow a copy of this video" + (f" ({why})." if why else "."))
    elif status == "error":
        store.update_job(job["id"], status="failed", reason="you2mp3 couldn't download it" + (f" ({why})." if why else ".") + " Retry tries again.")
    elif status in ("revoked", "cancelled"):
        store.update_job(job["id"], status="failed", reason="The request was cancelled at you2mp3. Retry asks again.", fresh_retry=1)
    else:
        waiting = {"pending": "Waiting its turn at you2mp3", "admitting": "Starting at you2mp3", "admitted": "Downloading at you2mp3",
                   "done": "Finishing at you2mp3"}.get(status, "Waiting at you2mp3")
        store.update_job(job["id"], status="processing", reason=waiting + (f": {why}" if why and status == "pending" else ""))


LAST_HEARTBEAT = 0


def run():
    global LAST_HEARTBEAT
    last_mirror = 0
    import disk_storage
    import threading
    def mirror_loop():
        while True:
            import nas_library
            if os.environ.get("STORAGE_BACKEND", "disk") == "disk":
                disk_storage.reconcile_mirror()
                nas_library.run_once()
            # A rename, category change, publish or delete on the site wakes this early.
            nas_library.WAKE.wait(60)
            nas_library.WAKE.clear()
    threading.Thread(target=mirror_loop, daemon=True, name="nas-mirror").start()
    while True:
        LAST_HEARTBEAT = time.time()
        try:
            with store.db() as c:
                job = c.execute("""SELECT * FROM jobs WHERE status IN ('queued','waiting for connection','processing','copying')
                  OR (status='imported' AND automatic=1 AND auto_publish=1
                      AND source_key IN (SELECT source_key FROM media WHERE play_status='ready'
                                         AND check_status IN ('ok','accepted')))
                  OR (status='problem' AND download_attempts < ? AND updated < ?)
                  OR (status='blocked' AND kind!='unsupported' AND updated < ? AND
                  (reason LIKE 'This connection is TikTok-only%' OR reason LIKE 'App Storage%' OR reason LIKE 'Storage unavailable%'
                   OR reason LIKE 'Imports paused:%' OR reason LIKE 'Per-request end cards%' OR reason LIKE 'Checking with you2mp3%'))
                  OR (status='blocked' AND reason LIKE 'YouTube copies are not switched on%' AND updated < ?)
                  ORDER BY updated LIMIT 1""", (media_check.MAX_DOWNLOADS, time.time() - media_check.RETRY_DELAY,
                                                time.time() - 60, time.time() - 1800)).fetchone()
            import queue_actions
            queue_actions.run_cancels()
            if job:
                process(dict(job))
                # Paused run jobs must not starve independent new-post work.
                import back_catalog
                if not back_catalog.allowed(dict(job)):
                    with store.db() as c:
                        c.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), job["id"]))
        except Exception:
            pass  # Individual job failures are recorded; keep the worker alive.
        import durable_state
        durable_state.checkpoint()
        if time.monotonic() - last_mirror > 60:
            import disk_storage
            import lite_site
            try:
                lite_site.generate(store.load_catalog()[0])
                lite_site.LAST_ERROR = ""
            except Exception:
                lite_site.LAST_ERROR = "Lite fallback generation failed; check data folder access"
            last_mirror = time.monotonic()
        time.sleep(5)
