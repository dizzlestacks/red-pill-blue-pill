"""Single-server recovery snapshots, separate from public media.

Disk storage is the default; optional Replit backend uses the same interface.
No secret/session credentials are included.
"""
import hashlib
import io
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import zipfile
import admin_store as store

SNAPSHOT = "wokearewe/state/current.zip"
_last_digest = None
_lock = threading.Lock()
_retry_at = 0
LAST_ERROR = ""


def checkpoint(required=False):
    global _last_digest, _retry_at, LAST_ERROR
    if not required and time.monotonic() < _retry_at:
        return False
    with _lock, store.LOCK:
        try:
            from media_worker import storage_client
            client = storage_client()
            # Check configuration before doing local snapshot work.
            client.exists(SNAPSHOT)
            with tempfile.TemporaryDirectory(prefix="waw-state-") as folder:
                target = Path(folder) / "admin.sqlite"
                with store.db() as original, sqlite3.connect(target) as dest:
                    original.backup(dest)
                contents = {"admin.sqlite": target.read_bytes(), "videos.json": store.CATALOG.read_bytes()}
                backup = Path(str(store.CATALOG) + ".bak")
                if backup.exists():
                    contents["videos.json.bak"] = backup.read_bytes()
                digest = hashlib.sha256(b"".join(contents.values())).hexdigest()
                if digest == _last_digest:
                    LAST_ERROR = ""
                    return True
                blob = io.BytesIO()
                with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as archive:
                    for name, content in contents.items():
                        archive.writestr(name, content)
                data = blob.getvalue()
                if digest != _last_digest:
                    # Immutable recovery copy first, then atomically replace current.
                    client.upload_from_bytes("wokearewe/state/history/" + str(time.time_ns()) + ".zip", data)
                    client.upload_from_bytes(SNAPSHOT, data)
                    _last_digest = digest
            LAST_ERROR = ""
            return True
        except Exception:
            LAST_ERROR = "Recovery snapshot failed. Check configured storage access and disk space; primary data has not been deleted."
            _retry_at = time.monotonic() + 60
            if required:
                raise ValueError("Permanent queue checkpoint failed; no download was submitted")
            return False


def restore_if_needed():
    """Restore on a new instance only; never overwrite an existing local DB."""
    if (store.STATE / "admin.sqlite").exists() and store.CATALOG.exists():
        return
    try:
        from media_worker import storage_client
        client = storage_client()
        if not client.exists(SNAPSHOT):
            return
        with tempfile.TemporaryDirectory(prefix="waw-restore-") as folder:
            file = Path(folder) / "state.zip"
            client.download_to_filename(SNAPSHOT, str(file))
            with zipfile.ZipFile(file) as archive:
                allowed = ("admin.sqlite", "videos.json", "videos.json.bak")
                contents = {}
                for name in allowed:
                    if name not in archive.namelist():
                        continue
                    if archive.getinfo(name).file_size > 100 * 1024 * 1024:
                        raise ValueError("Recovery state exceeds maximum size")
                    contents[name] = archive.read(name)
                if "admin.sqlite" not in contents or "videos.json" not in contents:
                    raise ValueError("Incomplete recovery state")
                import json
                store.clean_catalog(json.loads(contents["videos.json"]))
                store.STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
                store.CATALOG.write_bytes(contents["videos.json"])
                if "videos.json.bak" in contents:
                    Path(str(store.CATALOG) + ".bak").write_bytes(contents["videos.json.bak"])
                (store.STATE / "admin.sqlite").write_bytes(contents["admin.sqlite"])
    except Exception:
        # Fail startup on inaccessible configured recovery state: never silently
        # run an empty queue and resubmit work. Missing configuration is distinct.
        import os
        if os.environ.get("STORAGE_BACKEND", "disk") == "replit":
            from replit.object_storage.errors import DefaultBucketError
            import sys
            if isinstance(sys.exception(), DefaultBucketError):
                return
        raise RuntimeError("Unable to restore permanent admin state; refusing to start with potentially stale data")
