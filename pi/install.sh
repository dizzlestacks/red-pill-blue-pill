#!/usr/bin/env bash
# Raspberry Pi OS Bookworm/Trixie or Debian 12+, systemd. Run with sudo.
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo "Run: sudo bash pi/install.sh"; exit 1; }
SOURCE="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-install}"
[[ "$MODE" == install || "$MODE" == --update ]] || exit 1
command -v apt-get >/dev/null || { echo "Requires Raspberry Pi OS or Debian with apt."; exit 1; }
command -v systemctl >/dev/null || { echo "Requires systemd."; exit 1; }
[[ -f "$SOURCE/bundle-manifest.json" ]] || { echo "Use the clean Pi bundle, not an arbitrary source checkout."; exit 1; }
apt-get update
apt-get install -y python3 python3-requests ffmpeg ca-certificates curl
python3 -c 'import sys; assert sys.version_info >= (3,11), "Use Raspberry Pi OS Bookworm or newer"'
id wokearewe >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/wokearewe --shell /usr/sbin/nologin wokearewe
install -d -m 750 -o root -g wokearewe /etc/wokearewe
install -d -m 750 -o wokearewe -g wokearewe /var/lib/wokearewe
install -d -m 755 /opt/wokearewe/releases
if [[ "$MODE" == --update && ! -f /etc/wokearewe/settings.env ]]; then
  echo "No existing install settings. Run sudo bash pi/install.sh first."; exit 1
fi
if [[ ! -f /etc/wokearewe/settings.env ]]; then
  echo "These values are saved only in /etc/wokearewe/settings.env, outside the code."
  read -r -s -p "New admin password (at least 12 characters): " ADMIN_PASSWORD; echo
  [[ ${#ADMIN_PASSWORD} -ge 12 ]] || { echo "Password is too short."; exit 1; }
  read -r -s -p "you2mp3 API key (Enter to configure later): " YOU2MP3_API_KEY; echo
  export ADMIN_PASSWORD YOU2MP3_API_KEY
  python3 - <<'PY'
import json,os
values={"ADMIN_PASSWORD":os.environ["ADMIN_PASSWORD"],"YOU2MP3_API_KEY":os.environ["YOU2MP3_API_KEY"],
        "DATA_DIR":"/var/lib/wokearewe","MIRROR_DIR":"","STORAGE_BACKEND":"disk","PORT":"5002","BIND_HOST":"127.0.0.1",
        "GITHUB_SYNC_REPO":"","GITHUB_SYNC_BRANCH":"","GITHUB_SYNC_TOKEN":"",
        "PI_PUBLIC_URL":"","PUBLIC_SITE_ORIGIN":"https://wokearewe.com",
        "YOUTUBE_CLIENT_ID":"","YOUTUBE_CLIENT_SECRET":"","YOUTUBE_CHANNEL_HANDLE":"@wokearewe1",
        "TIKTOK_PROFILE":"https://www.tiktok.com/@wokearewe1","TIKTOK_WATCH_ENABLED":"true",
        "TIKTOK_CHECK_MINUTES":"15","IMPORTS_PER_HOUR":"6","MIN_FREE_GB":"5",
        "TIKTOK_AUTO_PUBLISH":"false","TIKTOK_ADD_END_CARD":"true"}
with open("/etc/wokearewe/settings.env","w") as f:
    f.write("# Root-owned systemd settings. Edit with sudo; restart the service after changes.\n")
    for key,value in values.items(): f.write(key+"="+json.dumps(value,ensure_ascii=False)+"\n")
PY
  unset ADMIN_PASSWORD YOU2MP3_API_KEY
  chown root:wokearewe /etc/wokearewe/settings.env
  chmod 640 /etc/wokearewe/settings.env
fi
DEST="/opt/wokearewe/releases/$(date -u +%Y%m%dT%H%M%S)-$$"
export SOURCE DEST
python3 - <<'PY'
import hashlib,json,os,pathlib,shutil
source=pathlib.Path(os.environ["SOURCE"]); dest=pathlib.Path(os.environ["DEST"])
manifest=json.loads((source/"bundle-manifest.json").read_text())
for name,expected in manifest["files"].items():
    p=pathlib.PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts: raise SystemExit("Unsafe bundle path")
    f=source/name
    if f.is_symlink() or hashlib.sha256(f.read_bytes()).hexdigest()!=expected: raise SystemExit("Bundle verification failed: "+name)
for name in manifest["files"]:
    target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/name,target)
print("Bundle checksum verification passed.")
PY
systemctl stop wokearewe 2>/dev/null || true
PREVIOUS="$(readlink -f /opt/wokearewe/current 2>/dev/null || true)"
ln -s "$DEST" /opt/wokearewe/current.next
mv -Tf /opt/wokearewe/current.next /opt/wokearewe/current
install -m 644 "$DEST/pi/wokearewe.service" /etc/systemd/system/wokearewe.service
systemctl daemon-reload
systemctl enable --now wokearewe
if ! python3 - <<'PY'
import json,time,urllib.request
settings={}
for line in open("/etc/wokearewe/settings.env"):
    if "=" in line and not line.lstrip().startswith("#"):
        k,v=line.strip().split("=",1)
        settings[k]=json.loads(v) if v.startswith('"') else v
url="http://127.0.0.1:"+settings.get("PORT","5002")+"/healthz"
for _ in range(30):
    try:
        with urllib.request.urlopen(url,timeout=2) as r:
            if json.load(r).get("worker")=="running":raise SystemExit(0)
    except Exception:pass
    time.sleep(2)
raise SystemExit(1)
PY
then
  echo "Service did not start. Inspect: sudo journalctl -u wokearewe -n 50"
  echo "Settings/data were not replaced. Previous code: ${PREVIOUS:-none}"
  exit 1
fi
echo "Installed and running. Local default health: http://127.0.0.1:5002/healthz"
echo "Configure your existing HTTPS reverse proxy to 127.0.0.1:5002. No DNS/proxy settings were changed."
echo "Settings: /etc/wokearewe/settings.env"
