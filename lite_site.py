"""Generate a standalone file:// and static-host compatible public fallback."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import admin_store as store

PAGES = ("index.html", "choice.html", "videos.html", "learn.html", "readonly.html", "reset.html")
ASSETS = ("404.html", "themes.css", "videos.js", "offline.js", "backup-player.js", "public-backups.js", "The-Matrix-Blue-Pill-or-Red-Pill.mp4")
LAST_ERROR = ""
_lock = threading.Lock()


def limited_catalog(catalog):
    import public_catalog
    result = public_catalog.export(catalog)
    # Netlify has no Pi environment settings; use the explicit public origin
    # from the already-sanitized GitHub catalogue.
    if not result["public_pi_url"]:
        from urllib.parse import urlparse
        origin = catalog.get("public_pi_url", "")
        p = urlparse(origin)
        if p.scheme == "https" and p.hostname and not p.username and not p.password and not p.path and not p.query and not p.fragment:
            result["public_pi_url"] = origin
    for video in result.get("videos", []):
        for name in ("backup", "backup_url", "backup_available", "backup_status"):
            video.pop(name, None)
        video.pop("url", None)
        if not video.get("youtube_id"):
            video["limited_unavailable"] = True
        if str(video.get("thumb", "")).startswith(("/", "http://localhost", "http://127.")):
            video.pop("thumb", None)
    return result


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".lite-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def generate(catalog=None, output=None):
    with _lock:
        if catalog is None:
            catalog = store.load_catalog()[0]
        dest = Path(output) if output else store.STATE / "lite"
        dest.mkdir(parents=True, exist_ok=True)
        for name in PAGES:
            text = (store.ROOT / name).read_text()
            if name == "videos.html":
                text = text.replace('<script src="videos.js"></script>', '<script src="videos.js"></script><script src="public-backups.js"></script>')
            text = text.replace("<head>", '<head>\n<script src="lite-data.js"></script>')
            write_atomic(dest / name, text.encode())
        for name in ASSETS:
            src, target = store.ROOT / name, dest / name
            if not target.exists() or target.stat().st_mtime_ns != src.stat().st_mtime_ns or target.stat().st_size != src.stat().st_size:
                shutil.copy2(src, target)
        for folder in ("assets", "pdfs"):
            for src in (store.ROOT / folder).rglob("*"):
                if src.is_file() and src.suffix.lower() in (".png", ".jpg", ".ico", ".svg", ".webp", ".pdf"):
                    target = dest / src.relative_to(store.ROOT)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists() or target.stat().st_mtime_ns != src.stat().st_mtime_ns or target.stat().st_size != src.stat().st_size:
                        shutil.copy2(src, target)
        data = limited_catalog(catalog)
        payload = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
        # Embedded data avoids fetch() restrictions when HTML is opened from disk.
        write_atomic(dest / "lite-data.js", ("window.MATRIX_LITE=true;window.MATRIX_LITE_CATALOG=" + payload + ";").encode())
        write_atomic(dest / "videos.json", (payload + "\n").encode())
        write_atomic(dest / "README.txt", b"Open index.html directly, or upload this entire folder to any static host.\nYouTube needs internet. Optional configured Pi backups play inline when reachable; no admin access is included.\n")
        return dest


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", default=str(store.CATALOG if store.CATALOG.exists() else store.ROOT / "videos.json"))
    parser.add_argument("--output")
    args = parser.parse_args()
    print(generate(json.loads(Path(args.catalog).read_text()), args.output))
