"""Durable debounced public-catalogue sync using GitHub Contents API CAS."""
import base64
import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import quote
import requests
import admin_store as store
import public_catalog

LOCK = threading.Lock()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config():
    repo = os.environ.get("GITHUB_SYNC_REPO", "").strip()
    branch = os.environ.get("GITHUB_SYNC_BRANCH", "").strip()
    token = os.environ.get("GITHUB_SYNC_TOKEN", "")
    if not token or not repo or not branch:
        return None
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or len(branch) > 200 or any(c.isspace() for c in branch):
        raise ValueError("Check GITHUB_SYNC_REPO and GITHUB_SYNC_BRANCH settings")
    return repo, branch, token


def state():
    with store.db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS public_sync (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
        row = db.execute("SELECT value FROM public_sync WHERE id=1").fetchone()
    return json.loads(row[0]) if row else {}


def save(value):
    with store.db() as db:
        db.execute("INSERT INTO public_sync VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (json.dumps(value),))


def status():
    s = state()
    try:
        configured = bool(config())
    except ValueError as e:
        return {"status": "failed", "reason": str(e), "last_success": s.get("last_success")}
    return {"status": s.get("status", "pending") if configured else "not set up",
            "reason": s.get("reason", "") if configured else "Configure the GitHub repository, branch and token on the Pi.",
            "last_success": s.get("last_success"), "next_try": s.get("next_try"),
            "pending": s.get("pending") != s.get("sent")}


def now():
    if not LOCK.acquire(blocking=False):
        return
    try:
        s = state()
        s.update(force=True, next_try=0)
        save(s)
    finally:
        LOCK.release()


def blob_sha(data):
    """Git's id for file content (what GitHub reports as a file's sha)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


MAX_THUMBS_PER_COMMIT = 60


def pending_thumbs(exported, repo, branch, token, ref):
    """Thumbnails the catalogue refers to that GitHub doesn't have yet (name -> bytes)."""
    import thumbs
    remote = {}
    try:
        listing = request("GET", "/repos/" + repo + "/contents/thumbs?ref=" + quote(ref, safe=""), token, missing_ok=True)
    except ValueError:
        raise
    for entry in listing if isinstance(listing, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            remote[entry["name"]] = entry.get("sha")
    want = {}
    for v in exported["videos"]:
        thumb = v.get("thumb")
        if not thumb:
            continue
        name = thumb[len("thumbs/"):]
        src = thumbs.find(name)
        if not src:
            v.pop("thumb", None)
            continue
        data = src.read_bytes()
        if remote.get(name) == blob_sha(data):
            continue
        want[name] = data
    return want


def commit_with_thumbs(repo, branch, token, data, remote_sha, files):
    """One commit with the new thumbnails and videos.json, so Netlify builds once.
    It only fast-forwards: if the branch moved meanwhile, nothing is overwritten."""
    base = "/repos/" + repo + "/git"
    head = request("GET", base + "/ref/heads/" + quote(branch, safe=""), token)["object"]["sha"]
    tree = request("GET", base + "/commits/" + head, token)["tree"]["sha"]
    current = request("GET", "/repos/" + repo + "/contents/videos.json?ref=" + head, token)
    if current["sha"] != remote_sha:
        raise ValueError("Conflict: GitHub changed since the last sync. Nothing was overwritten. Review both copies; match the reviewed GitHub catalogue on the Pi and Sync now before further edits.")
    entries = []
    for name, raw in sorted(files.items()):
        blob = request("POST", base + "/blobs", token, json={"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        entries.append({"path": "thumbs/" + name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    entries.append({"path": "videos.json", "mode": "100644", "type": "blob", "content": data.decode()})
    new_tree = request("POST", base + "/trees", token, json={"base_tree": tree, "tree": entries})["sha"]
    commit = request("POST", base + "/commits", token, json={"message": "Update public video catalogue and card pictures from Pi",
                                                             "tree": new_tree, "parents": [head]})["sha"]
    request("PATCH", base + "/refs/heads/" + quote(branch, safe=""), token, json={"sha": commit, "force": False})
    return blob_sha(data)


def request(method, path, token, missing_ok=False, **kwargs):
    try:
        response = requests.request(method, "https://api.github.com" + path,
            headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"}, timeout=(10, 25),
            allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise ValueError("GitHub is unreachable; automatic retry is scheduled")
    if response.status_code in (409, 422):
        raise ValueError("Conflict: GitHub changed. Nothing was overwritten. Review both catalogues before retrying.")
    if response.status_code in (401, 403):
        raise ValueError("GitHub denied access. Check the token expiry and repository Contents write permission.")
    if response.status_code == 404 and missing_ok:
        return None
    if response.status_code == 404:
        raise ValueError("GitHub repository, branch or videos.json was not found, or the token cannot access it.")
    if response.status_code not in (200, 201):
        raise ValueError(f"GitHub returned HTTP {response.status_code}; automatic retry is scheduled")
    return response.json()


def tick():
    with LOCK:
        s = state()
        try:
            settings = config()
            if not settings:
                return
            repo, branch, token = settings
            target = repo + ":" + branch
            if s.get("target") != target:
                s = {"target": target, "force": s.get("force", False)}
            exported = public_catalog.export(store.load_catalog()[0])
            data = encode(exported)
            current = digest(data)
            stamp = time.time()
            if s.get("pending") != current:
                s.update(pending=current, changed=stamp, status="pending", reason="", next_try=0)
                save(s)
            force = s.pop("force", False)
            if s.get("sent") == current and not force:
                return
            if not force and (stamp < s.get("next_try", 0) or stamp-s.get("changed", stamp) < 15):
                return
            s.update(status="pending", reason="Sending public catalogue")
            save(s)
            path = "/repos/" + repo + "/contents/videos.json"
            remote = request("GET", path + "?ref=" + quote(branch, safe=""), token)
            raw = base64.b64decode(remote["content"])
            remote_json = encode(json.loads(raw))
            if remote_json == data:
                s.update(base_sha=remote["sha"], sent=current, last_success=stamp, status="synced", reason="", attempts=0)
            else:
                if not s.get("base_sha"):
                    original_seed = json.loads((store.ROOT / "videos.json").read_text())
                    seed = public_catalog.export(original_seed, configured_origin=False)
                    if remote_json not in (encode(original_seed), encode(seed)):
                        raise ValueError("Conflict: GitHub differs from the release seed. Review it; make the Pi catalogue match GitHub and Sync now to establish a baseline before editing.")
                    s["base_sha"] = remote["sha"]
                if remote["sha"] != s["base_sha"]:
                    raise ValueError("Conflict: GitHub changed since the last sync. Nothing was overwritten. Review both copies; match the reviewed GitHub catalogue on the Pi and Sync now before further edits.")
                files = pending_thumbs(exported, repo, branch, token, branch) if any(v.get("thumb") for v in exported["videos"]) else {}
                if files:
                    # Card pictures go first (in the same commit), so the catalogue never points at a missing file.
                    later = sorted(files)[MAX_THUMBS_PER_COMMIT:]
                    for v in exported["videos"]:
                        if v.get("thumb", "")[len("thumbs/"):] in later:
                            v.pop("thumb", None)
                    files = {n: files[n] for n in sorted(files)[:MAX_THUMBS_PER_COMMIT]}
                    sent_data = encode(exported)
                    new_sha = commit_with_thumbs(repo, branch, token, sent_data, remote["sha"], files)
                    # With more pictures still to send, the next tick sends them (the full catalogue differs).
                    s.update(base_sha=new_sha, sent=digest(sent_data) if later else current, last_success=time.time(),
                             status="synced", reason="", attempts=0, next_try=0, changed=0)
                    save(s)
                    return
                result = request("PUT", path, token, json={"message": "Update public video catalogue from Pi",
                    "branch": branch, "sha": s["base_sha"], "content": base64.b64encode(data).decode()})
                s.update(base_sha=result["content"]["sha"], sent=current, last_success=time.time(), status="synced", reason="", attempts=0)
            s["next_try"] = 0
            save(s)
        except Exception as e:
            attempts = s.get("attempts", 0) + 1
            s.update(status="failed", reason=str(e) if isinstance(e, ValueError) else "Sync failed; check GitHub configuration and retry.",
                     attempts=attempts, next_try=time.time()+min(900, 15*2**min(attempts, 6)))
            save(s)


def run():
    while True:
        try:
            tick()
        except Exception:
            pass  # No private upstream response or credentials enter logs.
        time.sleep(3)
