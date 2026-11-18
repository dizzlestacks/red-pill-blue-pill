"""Matrix Video Hub / admin release 2026.11.15.6."""
import hashlib
import hmac
import http.cookies
import http.server
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import urlparse, unquote
import admin_store as store
import media_worker as worker
import durable_state
import lite_site
import disk_storage
import tiktok_links
import public_sync
import public_catalog
import tiktok_watch

SESSIONS = {}
ATTEMPTS = []
AUTH_LOCK = threading.Lock()
MEDIA_LOCK = threading.Lock()
SESSION_SECONDS = 12 * 3600
PUBLIC_HTML = {"index.html", "choice.html", "videos.html", "learn.html", "readonly.html", "reset.html", "videos-backup.html"}


class Handler(http.server.SimpleHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(store.ROOT), **kwargs)

    def log_message(self, fmt, *args):
        # Do not log credentials, query strings, or request payloads.
        print(self.command, urlparse(self.path).path, flush=True)

    def end_headers(self):
        if urlparse(self.path).path == "/api/public/backups":
            origin = self.headers.get("Origin", "")
            allowed = os.environ.get("PUBLIC_SITE_ORIGIN", "https://wokearewe.com")
            if origin == allowed:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        if urlparse(self.path).path in ("/service-worker.js", "/offline.js", "/admin", "/admin.html", "/admin.js", "/admin.css"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if urlparse(self.path).path.startswith(("/admin", "/api/admin")):
            self.send_header("Content-Security-Policy", "frame-ancestors 'self' https://replit.com https://*.replit.com https://*.replit.dev")
        super().end_headers()

    def reply(self, data, status=200, cookie=None):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def session(self):
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not password:
            return None
        try:
            cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies["waw_admin"].value
        except (KeyError, http.cookies.CookieError):
            return None
        key = hashlib.sha256(token.encode()).hexdigest()
        with AUTH_LOCK:
            s = SESSIONS.get(key)
            if not s or s["expires"] < time.time() or s["password"] != hashlib.sha256(password.encode()).hexdigest():
                SESSIONS.pop(key, None)
                return None
        return s

    def authorized(self):
        s = self.session()
        if not s:
            self.reply({"error": "Admin password not configured" if not os.environ.get("ADMIN_PASSWORD") else "Please log in"}, 401)
            return False
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), s["csrf"]):
            self.reply({"error": "Session verification failed. Reload and try again."}, 403)
            return False
        return True

    def origin_ok(self):
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).netloc == self.headers.get("Host")

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/public/backups":
                base = public_catalog.pi_origin()
                cat, _, _ = store.hydrated()
                return self.reply({"backups": {str(v["id"]): base+v["backup_url"]
                    for v in cat["videos"] if base and v.get("backup_available") and v.get("backup_url")}})
            if path == "/healthz":
                alive = worker_thread is not None and worker_thread.is_alive()
                return self.reply({"status": "ok" if alive else "degraded", "release": store.RELEASE,
                                   "worker": "running" if alive else "stopped"}, 200 if alive else 503)
            if path in ("/editor.html", "/editor", "/admin/"):
                self.send_response(302)
                self.send_header("Location", "/admin")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/api/admin/session":
                s = self.session()
                return self.reply({"configured": bool(os.environ.get("ADMIN_PASSWORD")),
                                   "authenticated": bool(s), "csrf": s["csrf"] if s else None, "release": store.RELEASE})
            if path in ("/api/videos", "/videos.json"):
                cat, _, _ = store.hydrated()
                return self.reply(cat)
            if path.startswith("/api/admin/"):
                if not self.session():
                    return self.reply({"error": "Please log in"}, 401)
                if path == "/api/admin/sync":
                    return self.reply(public_sync.status())
                if path == "/api/admin/watcher":
                    return self.reply(tiktok_watch.status())
                if path == "/api/admin/catalog":
                    cat, revision, count = store.hydrated()
                    return self.reply({"catalog": cat, "revision": revision, "backed_up": count, "total": len(cat["videos"])})
                if path == "/api/admin/queue":
                    ready, message = worker.storage_status()
                    with store.db() as c:
                        rows = [dict(r) for r in c.execute("SELECT * FROM jobs ORDER BY created DESC")]
                        for r in rows:
                            r["request_locked"] = bool(r["payload"] or r["upstream_id"])
                            r["tags"] = json.loads(r["tags"])
                            r["preview_url"] = "/api/admin/media/" + r["id"] if r["status"] in ("imported", "published") else None
                            for key in ("payload", "idempotency", "upstream_id", "source_key"):
                                r.pop(key, None)
                        rows += [dict(r) for r in c.execute("SELECT * FROM events ORDER BY created DESC LIMIT 100")]
                    return self.reply({"items": rows, "connection_configured": bool(os.environ.get("YOU2MP3_API_KEY")),
                                       "storage_ready": ready, "storage_message": message,
                                       "state_storage_message": " ".join(filter(None, [durable_state.LAST_ERROR, disk_storage.MIRROR_ERROR, lite_site.LAST_ERROR]))})
                if path.startswith("/api/admin/media/"):
                    j = store.get_job(path.rsplit("/", 1)[1])
                    return self.serve_media(j["source_key"], head=head)
                return self.reply({"error": "Not found"}, 404)
            if path.startswith("/media/"):
                slug = path.split("/")[2]
                key = slug.replace("tt-", "tt:", 1) if slug.startswith("tt-") else slug.replace("yt-", "yt:", 1)
                cat, _, _ = store.hydrated()
                if not any(v.get("backup_url") == "/media/" + slug for v in cat["videos"]):
                    return self.reply({"error": "Not published"}, 404)
                return self.serve_media(key, download=path.endswith("/download"), head=head)
            if path == "/admin":
                self.path = "/admin.html"
            elif path.lstrip("/") in ("admin.html",):
                self.send_response(302)
                self.send_header("Location", "/admin")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            elif path not in ("/",) and path.lstrip("/") not in PUBLIC_HTML:
                target = (store.ROOT / path.lstrip("/")).resolve()
                safe = target.is_relative_to(store.ROOT) and not any(p.startswith(".") for p in Path(path).parts if p not in ("/",))
                allowed = target.suffix.lower() in (".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".svg", ".mp4", ".pdf", ".woff2")
                # Only explicitly public root assets and asset/pdf subdirectories.
                root_file = len(Path(path).parts) == 2
                subdir = path.startswith(("/assets/", "/pdfs/"))
                if not safe or not allowed or not (root_file or subdir) or not target.is_file():
                    return self.reply({"error": "Not found"}, 404)
            target = (store.ROOT / path.lstrip("/")).resolve()
            if target.suffix.lower() in (".mp4", ".pdf") and target.is_file():
                return self.serve_public_file(target, head)
            if head:
                return super().do_HEAD()
            return super().do_GET()
        except (LookupError, FileNotFoundError):
            self.reply({"error": "Not found"}, 404)
        except Exception:
            self.reply({"error": "Unable to read requested data"}, 500)

    def serve_public_file(self, file, head=False):
        size = file.stat().st_size
        start, end = 0, size - 1
        ranged = self.headers.get("Range")
        if ranged:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranged)
            if not match or not any(match.groups()):
                start = size
            elif match[1]:
                start = int(match[1])
                end = min(end, int(match[2])) if match[2] else end
            else:
                start = max(0, size - int(match[2]))
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", mimetypes.guess_type(str(file))[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not head:
            with file.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = source.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def serve_media(self, key, download=False, head=False):
        with store.db() as c:
            row = c.execute("SELECT * FROM media WHERE source_key=?", (key,)).fetchone()
        if not row:
            return self.reply({"error": "Backup unavailable"}, 404)
        cache = Path(tempfile.gettempdir()) / "waw-playback"
        cache.mkdir(exist_ok=True)
        file = cache / (row["sha256"] + ".mp4")
        with MEDIA_LOCK:
            if not file.exists() or file.stat().st_size != row["size"]:
                tmp = file.with_suffix(".tmp")
                try:
                    worker.storage_client().download_to_filename(row["object_key"], str(tmp))
                    digest = hashlib.sha256()
                    with tmp.open("rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            digest.update(chunk)
                    if tmp.stat().st_size != row["size"] or digest.hexdigest() != row["sha256"]:
                        raise ValueError("Storage checksum mismatch")
                    os.replace(tmp, file)
                except Exception:
                    tmp.unlink(missing_ok=True)
                    return self.reply({"error": "Permanent backup storage is currently unavailable"}, 503)
        size, start, end = row["size"], 0, row["size"] - 1
        ranged = self.headers.get("Range")
        if ranged:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", ranged)
            if not m or not any(m.groups()):
                return self.reply({"error": "Invalid range"}, 416)
            if m[1]:
                start = int(m[1])
                end = min(end, int(m[2])) if m[2] else end
            else:
                start = max(0, size - int(m[2]))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("Content-Disposition", ('attachment' if download else 'inline') + f'; filename="wokearewe-{key.replace(":", "-")}.mp4"')
        if ranged:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not head:
            with file.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.origin_ok():
            return self.reply({"error": "Cross-site request rejected"}, 403)
        if path != "/api/admin/login" and not self.authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 5 * 1024 * 1024:
                return self.reply({"error": "Invalid request size"}, 413)
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.reply({"error": "Expected JSON"}, 415)
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected an object")
            if path == "/api/admin/login":
                password = os.environ.get("ADMIN_PASSWORD")
                if not password:
                    return self.reply({"error": "Admin password not configured"}, 503)
                with AUTH_LOCK:
                    ATTEMPTS[:] = [t for t in ATTEMPTS if t > time.time() - 900]
                    if len(ATTEMPTS) >= 30:
                        return self.reply({"error": "Too many attempts. Try again in 15 minutes."}, 429)
                    if not isinstance(body.get("password"), str) or not hmac.compare_digest(body["password"].encode(), password.encode()):
                        ATTEMPTS.append(time.time())
                        return self.reply({"error": "Incorrect password"}, 401)
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    SESSIONS[hashlib.sha256(token.encode()).hexdigest()] = {
                        "csrf": csrf, "expires": time.time() + SESSION_SECONDS,
                        "password": hashlib.sha256(password.encode()).hexdigest()}
                secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https" else ""
                return self.reply({"csrf": csrf}, cookie=f"waw_admin={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS}{secure}")
            if path == "/api/admin/logout":
                s = self.session()
                with AUTH_LOCK:
                    for key, val in list(SESSIONS.items()):
                        if val is s:
                            SESSIONS.pop(key)
                return self.reply({"ok": True}, cookie="waw_admin=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            if path == "/api/videos":
                revision = store.save_catalog(body.get("catalog"), body.get("revision"))
                checkpointed = durable_state.checkpoint()
                return self.reply({"ok": True, "revision": revision, "durable_state": checkpointed})
            if path == "/api/admin/sync":
                public_sync.now()
                return self.reply(public_sync.status())
            if path == "/api/admin/watcher":
                result = tiktok_watch.configure(body)
                durable_state.checkpoint()
                return self.reply(result)
            if path in ("/api/admin/watcher/check", "/api/admin/watcher/older"):
                result = tiktok_watch.request(older=path.endswith("/older"))
                durable_state.checkpoint()
                return self.reply(result)
            if path == "/api/admin/queue":
                urls = body.get("urls", [])
                if not isinstance(urls, list) or not 1 <= len(urls) <= 500:
                    raise ValueError("Paste between 1 and 500 TikTok post links")
                deadline = time.monotonic() + 20
                validated = [store.source(tiktok_links.resolve(url, deadline)) for url in urls]
                if any(kind != "tiktok" for _, _, kind in validated):
                    raise ValueError("This import form accepts your TikTok links only")
                end_card = body.get("add_end_card", tiktok_watch.settings()["add_end_card"])
                if body.get("use_provider_default") is True:
                    end_card = None
                elif not isinstance(end_card, bool):
                    raise ValueError("Choose Add end card on/off, or explicitly accept the service default")
                results = [store.enqueue(url, add_end_card=end_card) for _, url, _ in validated]
                durable_state.checkpoint()
                return self.reply({"ok": True, "results": results})
            if path == "/api/admin/backup":
                cat, _, _ = store.hydrated()
                selected = [v for v in cat["videos"] if not v["backup_available"] and (body.get("all") is True or v["id"] == body.get("video_id"))]
                results = []
                for v in selected:
                    try:
                        results.append(store.enqueue(store.video_source(v), v["id"], v["title"], v.get("tags", [])))
                    except ValueError as e:
                        results.append(store.enqueue_unsupported(v, str(e)))
                durable_state.checkpoint()
                return self.reply({"ok": True, "results": results})
            m = re.fullmatch(r"/api/admin/queue/([a-f0-9]+)/(\w+)", path)
            if m:
                jid, action = m.groups()
                job = store.get_job(jid)
                if action == "review":
                    title, tags = body.get("title"), body.get("tags")
                    if not isinstance(title, str) or not title.strip() or len(title) > 1000 or not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
                        raise ValueError("Enter a title and valid hashtags")
                    with store.LOCK:
                        job = store.get_job(jid)
                        updates = dict(title=title.strip(), tags=json.dumps(tags))
                        if "add_end_card" in body or body.get("use_provider_default") is True:
                            choice = None if body.get("use_provider_default") is True else body["add_end_card"]
                            if choice is not None and not isinstance(choice, bool):
                                raise ValueError("Add end card must be on or off")
                            if (job["payload"] or job["upstream_id"]) and choice != job["add_end_card"]:
                                raise ValueError("This request has already been submitted; its end-card choice cannot change")
                            updates["add_end_card"] = choice
                        store.update_job(jid, **updates)
                elif action == "publish":
                    store.publish(jid)
                elif action == "retry":
                    if job["status"] not in ("failed", "blocked", "waiting for connection"):
                        raise ValueError("Only blocked, failed or waiting items can be retried")
                    if job["upstream_id"] and job["status"] == "failed":
                        # Retry is not idempotent; invoke only on explicit user action.
                        upstream = worker.api("/videos/" + str(job["upstream_id"]))
                        if upstream.get("status") in ("error", "cancelled"):
                            worker.api("/videos/" + str(job["upstream_id"]) + "/retry", {})
                    store.update_job(jid, status="queued", reason="")
                else:
                    return self.reply({"error": "Not found"}, 404)
                durable_state.checkpoint()
                return self.reply({"ok": True})
            self.reply({"error": "Not found"}, 404)
        except FileExistsError as e:
            self.reply({"error": str(e)}, 409)
        except (ValueError, TypeError, KeyError) as e:
            self.reply({"error": str(e)}, 400)
        except LookupError:
            self.reply({"error": "Item not found"}, 404)
        except worker.UpstreamError as e:
            self.reply({"error": str(e)}, 502)
        except Exception:
            self.reply({"error": "Operation failed. No success was recorded."}, 500)


def main():
    if not os.environ.get("DATA_DIR") and store.STATE == store.ROOT / ".private" and not (store.STATE / ".disk-layout").exists() and not store.CATALOG.exists() and (store.ROOT / "videos.json").exists():
        # One-time migration from the former root catalogue; never overwrite data.
        store.STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        disk_storage.atomic_copy(store.ROOT / "videos.json", store.CATALOG)
    durable_state.restore_if_needed()
    store.init()
    (store.STATE / ".disk-layout").touch()
    lite_site.generate()
    global worker_thread
    worker_thread = threading.Thread(target=worker.run, daemon=True)
    worker_thread.start()
    threading.Thread(target=public_sync.run, daemon=True).start()
    threading.Thread(target=tiktok_watch.run, daemon=True).start()
    threading.Thread(target=supervise_worker, daemon=True).start()
    http.server.ThreadingHTTPServer((os.environ.get("BIND_HOST", "0.0.0.0"), int(os.environ.get("PORT", "5000"))), Handler).serve_forever()


worker_thread = None


def supervise_worker():
    while True:
        time.sleep(10)
        if worker_thread is not None and not worker_thread.is_alive():
            print("Media worker stopped unexpectedly; exiting so the service manager restarts it", flush=True)
            os._exit(1)


if __name__ == "__main__":
    main()
