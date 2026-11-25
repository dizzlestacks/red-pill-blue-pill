"""Atomic, verified filesystem object storage; no cloud account is needed."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import admin_store as store

MIRROR_ERROR = ""
MIRROR_STATUS = {"state": "not set up", "count": 0, "last_check": None}


def atomic_copy(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".copy-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as out, open(source, "rb") as inp:
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        def digest(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            return h.digest()
        if digest(source) != digest(temp):
            raise IOError("Copy verification failed")
        os.replace(temp, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temp).unlink(missing_ok=True)


def mirror_root():
    value = os.environ.get("MIRROR_DIR", "").strip()
    if not value:
        return None
    root = Path(value).resolve()
    # Never create a missing mount root and silently write to the system disk.
    if not root.is_dir():
        raise OSError("Mirror folder is unavailable; check its mount and permissions")
    if root == store.STATE.resolve() or root.is_relative_to(store.STATE.resolve()) or store.STATE.resolve().is_relative_to(root):
        raise OSError("Mirror folder must be separate from the primary data folder")
    return root


class DiskStorage:
    def __init__(self):
        if os.environ.get("DATA_DIR") and not store.STATE.is_dir():
            raise OSError("Configured data folder is unavailable; check its mount")
        self.root = store.STATE / "objects"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        p = PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or not p.parts:
            raise ValueError("Invalid storage object name")
        path = (self.root / str(p)).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("Invalid storage path")
        return path

    def exists(self, name):
        return self.path(name).is_file()

    def upload_from_filename(self, name, source):
        target = self.path(name)
        atomic_copy(source, target)
        # NAS I/O runs only on its own daemon. A stale/hung mount must never
        # hold up primary imports, snapshots, or publication.

    def upload_from_bytes(self, name, data):
        with tempfile.TemporaryDirectory(prefix="waw-write-") as folder:
            file = Path(folder) / "object"
            file.write_bytes(data)
            self.upload_from_filename(name, file)

    def download_to_filename(self, name, destination):
        atomic_copy(self.path(name), destination)


def reconcile_mirror():
    """Copy missed/new objects and catalogue recovery files, without deleting anything."""
    global MIRROR_ERROR, MIRROR_STATUS
    import time
    try:
        root = mirror_root()
        if not root:
            MIRROR_ERROR = ""
            MIRROR_STATUS = {"state": "not set up", "count": 0, "last_check": time.time()}
            return
        MIRROR_STATUS = {**MIRROR_STATUS, "state": "syncing"}
        import nas_library
        # The recovery copy lives in its own clearly named folder, beside the browsable library.
        nas_library.migrate_system(root)
        system = root / nas_library.SYSTEM
        for prefix in ("objects", "backups"):
            directory = store.STATE / prefix
            if not directory.exists():
                continue
            for source in directory.rglob("*"):
                if not source.is_file() or source.name.startswith("."):
                    continue
                relative = source.relative_to(store.STATE)
                dest = system / relative
                if dest.exists() and dest.stat().st_size == source.stat().st_size and dest.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                    continue
                atomic_copy(source, dest)
        MIRROR_ERROR = ""
        count = 0
        with store.db() as c:
            media = c.execute("SELECT * FROM media").fetchall()
        for item in media:
            path = system / "objects" / item["object_key"]
            if path.is_file() and path.stat().st_size == item["size"]:
                digest = hashlib.sha256()
                with path.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() == item["sha256"]:
                    count += 1
        MIRROR_STATUS = {"state": "available", "count": count, "last_check": time.time()}
    except Exception:
        MIRROR_ERROR = "Second-copy mirror unavailable. Primary data is intact; check the mirror mount and permissions."
        MIRROR_STATUS = {**MIRROR_STATUS, "state": "unavailable", "last_check": time.time()}


def mirror_status():
    with store.db() as c:
        total = c.execute("SELECT count(*) FROM media").fetchone()[0]
    import nas_library
    return {**MIRROR_STATUS, "total": total, "error": MIRROR_ERROR,
            "configured": bool(os.environ.get("MIRROR_DIR", "").strip()), "library": nas_library.status()}
