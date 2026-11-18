"""Single, rate-limited worker. Only verified independently stored bytes count as backups."""
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

BASE = "https://you2mp3.music/api/v1"
NETWORK_LOCK = threading.Lock()
LAST_REQUEST = 0.0
BACKOFF_UNTIL = 0.0
STORAGE_ERROR = ""


class UpstreamError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def api(path, payload=None, idempotency=None, binary=False):
    global LAST_REQUEST, BACKOFF_UNTIL
    # Single worker + shared gate: <= 30 requests/minute, leaving room for account use.
    with NETWORK_LOCK:
        time.sleep(max(0, LAST_REQUEST + 2.1 - time.monotonic(), BACKOFF_UNTIL - time.monotonic()))
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
            try:
                delay = max(60, float(r.headers.get("Retry-After", 60)))
            except ValueError:
                delay = 60
            BACKOFF_UNTIL = time.monotonic() + delay
        if not r.ok or 300 <= r.status_code < 400:
            status = r.status_code
            r.close()
            messages = {401: "Connection key expired or invalid", 403: "Connection does not permit this operation",
                        402: "Account download allowance or plan limit reached", 409: "Upstream request outcome is uncertain or conflicts; same request retained",
                        410: "Idempotency receipt expired; manual reconciliation required",
                        429: "Account rate limit reached; waiting before retry"}
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


def copy_media(job, filename):
    if not isinstance(filename, str) or "/" in filename or "\\" in filename or len(filename) > 255:
        raise ValueError("Media service returned an invalid filename")
    tiktok_watch.disk_guard()
    with tempfile.TemporaryDirectory(prefix="waw-media-", dir=store.STATE) as folder:
        video = Path(folder) / "source.mp4"
        response = api("/files/" + quote(filename, safe=""), binary=True)
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
        duration = verify_media(video)
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
        with store.db() as c:
            c.execute("INSERT OR REPLACE INTO media VALUES(?,?,?,?,?,?,?)",
                      (job["source_key"], object_key, poster_key, size, digest.hexdigest(), duration, time.time()))


def process(job):
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
        capabilities = api("/capabilities")
        restriction = capabilities.get("restriction")
        if job["kind"] == "youtube" and restriction == "tiktok-imports":
            store.update_job(job["id"], status="blocked", reason="This connection is TikTok-only and cannot fetch YouTube. Retry after authorized access is granted.")
            return
        if job["kind"] == "tiktok" and restriction not in (None, "tiktok-imports"):
            store.update_job(job["id"], status="blocked", reason="Connection restriction is not supported")
            return
        ready, message = storage_status()
        if not ready:
            store.update_job(job["id"], status="blocked", reason=message)
            return
        if not job["upstream_id"]:
            with store.LOCK:
                job = store.get_job(job["id"])
                payload = json.loads(job["payload"]) if job["payload"] else {
                    "url": job["url"], "format": "mp4", "container": "mp4", "quality": "best"}
                if not job["payload"] and job.get("add_end_card") is not None:
                    payload = watch_provider.PROVIDER.end_card_payload(payload, bool(job["add_end_card"]))
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
            store.update_job(job["id"], status="copying", reason="Verifying video and copying to permanent storage")
            copy_media(job, result["outputFilename"])
            store.update_job(job["id"], status="imported", reason="Verified permanent copy; ready for review")
            if job.get("automatic") and job.get("auto_publish"):
                store.publish(job["id"])
        elif status in ("error", "cancelled"):
            # Do not leak arbitrary upstream errors (which may include credentials or internal URLs).
            raise ValueError("Media service " + status + ". Check the source's availability and connection permissions.")
        else:
            store.update_job(job["id"], status="processing", reason="Media service: " + str(status or "pending")[:40])
    except (tiktok_watch.Paused, watch_provider.Unavailable) as e:
        store.update_job(job["id"], status="blocked", reason=str(e))
    except UpstreamError as e:
        retryable = e.status in (0, 429, 502, 503, 504)
        store.update_job(job["id"], status="processing" if retryable else ("blocked" if e.status in (401, 402, 403, 409, 410) else "failed"),
                         reason=str(e))
    except Exception as e:
        reason = str(e) if isinstance(e, ValueError) else "Copy or verification failed. Check storage access and retry; no backup was published."
        store.update_job(job["id"], status="failed", reason=reason)


LAST_HEARTBEAT = 0


def run():
    global LAST_HEARTBEAT
    last_mirror = 0
    while True:
        LAST_HEARTBEAT = time.time()
        try:
            with store.db() as c:
                job = c.execute("""SELECT * FROM jobs WHERE status IN ('queued','waiting for connection','processing','copying')
                  OR (status='imported' AND automatic=1 AND auto_publish=1)
                  OR (status='blocked' AND kind!='unsupported' AND updated < ? AND
                  (reason LIKE 'This connection is TikTok-only%' OR reason LIKE 'App Storage%' OR reason LIKE 'Storage unavailable%'
                   OR reason LIKE 'Imports paused:%' OR reason LIKE 'Per-request end cards%'))
                  ORDER BY updated LIMIT 1""", (time.time() - 60,)).fetchone()
            if job:
                process(dict(job))
        except Exception:
            pass  # Individual job failures are recorded; keep the worker alive.
        import durable_state
        durable_state.checkpoint()
        if time.monotonic() - last_mirror > 60:
            import disk_storage
            if os.environ.get("STORAGE_BACKEND", "disk") == "disk":
                disk_storage.reconcile_mirror()
            import lite_site
            try:
                lite_site.generate(store.load_catalog()[0])
                lite_site.LAST_ERROR = ""
            except Exception:
                lite_site.LAST_ERROR = "Lite fallback generation failed; check data folder access"
            last_mirror = time.monotonic()
        time.sleep(5)
