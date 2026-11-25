"""Isolated UI fixture: no upstream key, sync, existing queue or real downloads."""
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("YOU2MP3_API_KEY","GITHUB_SYNC_TOKEN","MIRROR_DIR","PI_PUBLIC_URL","ADMIN_DATA_DIR"):
    os.environ.pop(key, None)
os.environ["ADMIN_PASSWORD"] = "local-ui-fixture-only"
os.environ["STORAGE_BACKEND"] = "disk"
with tempfile.TemporaryDirectory(prefix="waw-ui-") as folder:
    os.environ["DATA_DIR"] = folder
    import server
    server.main()
