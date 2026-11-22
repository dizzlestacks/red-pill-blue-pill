"""Build allowlisted, secret-free Pi and lite release archives."""
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import shutil
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import admin_store
import lite_site

FILES = [
    "server.py", "admin_store.py", "media_worker.py", "durable_state.py", "tiktok_watch.py", "watch_provider.py", "back_catalog.py",
    "disk_storage.py", "tiktok_links.py", "public_sync.py", "public_catalog.py", "public-backups.js", "lite_site.py", "admin.html", "admin.css", "admin.js",
    "editor.html", "index.html", "choice.html", "videos.html", "learn.html",
    "readonly.html", "reset.html", "404.html", "videos.js", "themes.css", "backup-player.js",
    "offline.js", "service-worker.js", "videos.json",
    "The-Matrix-Blue-Pill-or-Red-Pill.mp4", "netlify.toml",
    "pi/install.sh", "pi/update.sh", "pi/wokearewe.service", "pi/README.md",
    "pi/build_bundle.py", "pi/RELEASE-2026.11.15.5.md", "pi/RELEASE-2026.11.15.6.md", "pi/RELEASE-2026.11.15.7.md",
    "tests/lite-smoke.cjs", "package.json", "pi/LITE-RELEASE-CHECKS.md",
]


def build():
    release = admin_store.RELEASE
    output = ROOT / "generated-artifacts"
    output.mkdir(exist_ok=True)
    lite_folder = lite_site.generate(json.loads((ROOT / "videos.json").read_text()), output / "netlify-lite")
    # Check before writing either deliverable, not after an unchecked Pi archive.
    subprocess.run(["node", str(ROOT / "tests/lite-smoke.cjs"), "--dir", str(lite_folder)], check=True, cwd=ROOT)
    files = FILES + [str(p.relative_to(ROOT)) for folder in ("assets", "pdfs")
                    for p in (ROOT / folder).rglob("*") if p.is_file()
                    and p.suffix.lower() in (".png", ".jpg", ".ico", ".svg", ".webp", ".pdf")]
    with tempfile.TemporaryDirectory() as temp:
        stage = Path(temp) / "wokearewe"
        manifest = {"release": release, "files": {}}
        for name in files:
            source = ROOT / name
            if source.is_symlink():
                raise ValueError("Symlink in release")
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            manifest["files"][name] = hashlib.sha256(dest.read_bytes()).hexdigest()
        (stage / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2))
        pi = output / f"wokearewe-pi-{release}.tar.gz"
        with tarfile.open(pi, "w:gz") as archive:
            archive.add(stage, arcname="wokearewe")
    lite = output / f"wokearewe-lite-{release}.tar.gz"
    with tarfile.open(lite, "w:gz") as archive:
        archive.add(lite_folder, arcname="wokearewe-lite")
    for file in (pi, lite):
        checksum = hashlib.sha256(file.read_bytes()).hexdigest()
        Path(str(file) + ".sha256").write_text(f"{checksum}  {file.name}\n")
        print(file, file.stat().st_size, checksum)


if __name__ == "__main__":
    build()
