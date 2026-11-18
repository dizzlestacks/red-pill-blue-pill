# WokeAreWe Pi kit — release 2026.11.15.4

This release accepts TikTok short share links on vt.tiktok.com, vm.tiktok.com
and tiktok.com/t/. The server follows only HTTPS TikTok post/share redirects,
with bounded time and redirect limits, and still checks the owned account.
Resolution failures are shown explicitly; they are never treated as queued imports.
The admin now uses the public Matrix themes (terminal by default), mobile tap
targets, visible busy feedback and a pinned success/error message while scrolling.

## What this runs
The Pi runs the website, password admin and one background import/backup worker.
It starts at boot and restarts after a crash. It uses disk folders, not Replit.
Use Raspberry Pi OS **Bookworm or newer**, or Debian 12+, with Python 3.11+.
64-bit Pi OS is recommended. Installation requires internet and sudo.
No Node.js, browser, Docker, virtual environment, or cloud storage account is required.

The Pi is the same failure domain as you2mp3. Copies survive deletion of upstream
temporary files, but a broken Pi/disk affects both services. A separate mirror/NAS
and the static lite site protect against different parts of that risk.

## Install — one command
Extract the clean bundle, open a terminal inside it, and run:

    sudo bash pi/install.sh

The installer asks for the admin password and API key without displaying them.
It installs Python/requests/ffmpeg, a `wokearewe` service account, and a boot service.
Secrets are written only to **/etc/wokearewe/settings.env**, never into code or Git.
If you leave the API key blank, you can queue requests but downloads wait.

Default port is **5002**, listening only on the Pi itself, to avoid taking over
you2mp3's ports. Add a separate hostname/location to your existing HTTPS reverse
proxy, forwarding to `http://127.0.0.1:5002`. Forward `Host` and set
`X-Forwarded-Proto: https`; allow long video responses and byte ranges.
Do not overwrite the existing you2mp3 proxy configuration.
This kit does not change DNS, publish anything or obtain certificates.
Use a separate hostname for the complete Pi site if Netlify remains your main site.
The static Netlify lite site deliberately does not expose the Pi admin.

## Update — one command
Extract the new bundle somewhere separate and, from that folder, run:

    sudo bash pi/update.sh

Updates verify bundle checksums and switch the code release. They never replace
the settings file or overwrite the live catalogue/data folder. Old code releases
remain in `/opt/wokearewe/releases/`. Inspect failures with the logs below.
Before switching back to older code, confirm its database format is compatible.

## Settings / NAS later
Edit `sudo nano /etc/wokearewe/settings.env`, then
`sudo systemctl restart wokearewe`.

| Setting | Meaning |
|---|---|
| ADMIN_PASSWORD | Owner login password |
| YOU2MP3_API_KEY | Server-only media service token |
| DATA_DIR | Primary persistent data folder; default `/var/lib/wokearewe` |
| MIRROR_DIR | Optional second folder; empty disables it |
| PORT | Default `5002` |
| BIND_HOST | Default `127.0.0.1`; use `0.0.0.0` only if deliberately exposing the port |
| STORAGE_BACKEND | `disk` by default; leave this on the Pi |

Everything important is beneath DATA_DIR: `videos.json`, `admin.sqlite`,
`backups/`, `objects/` (media, posters, snapshots), and `lite/`.
Code is read-only to the service and is not the place to save live content.

**Recommended NAS setup:** keep DATA_DIR on the Pi's reliable local disk and set
MIRROR_DIR to a mounted NAS folder. It mirrors media, posters, recovery snapshots
and catalogue recovery copies. Missed copies retry every minute. Nothing is
deleted from the mirror automatically. An unavailable mirror shows a warning;
the primary copy continues to work.

For either location, mount the disk/NAS first, create the folder, and grant the
`wokearewe` user read/write access. Add a systemd drop-in with
`RequiresMountsFor=/your/mount/path` in `[Unit]` so it waits for the mount.
Test disconnect/reconnect before relying on it. Do not point at a directory that
silently becomes a normal local folder when the NAS is unmounted.
SQLite needs reliable POSIX locks, atomic rename and fsync. Not every SMB/NFS
mount provides those guarantees; use the NAS as a mirror unless verified.

To move DATA_DIR: stop the service, copy the **entire** old data directory with
permissions, change the setting, ensure ownership/access, then start it. Never
move a running SQLite database. No code change is needed.

The optional Replit backend remains lazy-loaded for existing Replit setups only.
It is not installed, contacted, or billed by the Pi disk configuration. Selecting
Replit storage may incur that provider's charges; it is not promised free.

## Check / logs

    curl -f http://127.0.0.1:5002/healthz
    sudo systemctl status wokearewe
    sudo journalctl -u wokearewe -n 80 --no-pager

Health returns 200 when the web process and worker thread are running. Admin
shows storage, mirror and generation errors separately. A 200 health response
does not prove that an upstream download or NAS mirror is currently working.
The worker makes at most about 30 media API calls/minute, leaving room beneath
the account's 60/minute limit; server Retry-After is respected.

## Recovery
Each checkpoint writes `objects/wokearewe/state/current.zip` and immutable history
copies. These contain a consistent SQLite snapshot, catalogue and last recovery
JSON. No passwords/sessions are in them. An optional mirror gets the same objects.

On a fresh/replacement data folder containing the `objects/` tree, startup can
restore missing catalogue/SQLite from current.zip. For manual disaster recovery:
stop the service; preserve the damaged folder elsewhere; copy the mirror's
`objects/` (and `backups/`) into an empty DATA_DIR; set ownership to `wokearewe`;
then start. Never replace a running database. Validate the restored catalogue
and media counts before allowing new imports. Keep independent backups of the
settings file using your own secure backup process.

## Netlify / GitHub and the lite public site
Your GitHub/Netlify setup remains supported. `netlify.toml` builds ONLY the lite
directory, never the repository root. Netlify cannot run this Python worker.
No DNS or Netlify deployment is performed by this kit.

On the Pi, **DATA_DIR/lite/** regenerates after each catalogue save and on startup.
It includes intro, pill choice, Video Hub, Scripture PDFs and Read Only.
Open `index.html` directly from disk or upload the whole folder to any static host.
YouTube needs internet; Pi-only clips say they need the Pi. Admin, saved-copy
playback and downloads are unavailable, with a visible limited-mode notice.

To manually regenerate:

    DATA_DIR=/var/lib/wokearewe python3 /opt/wokearewe/current/lite_site.py

To update Netlify, deploy the contents of DATA_DIR/lite as the static site using
your normal Netlify upload/deploy process. Alternatively, deliberately copy the
current DATA_DIR/videos.json into the GitHub source catalogue and commit/push;
Netlify's build generates its lite output from that committed catalogue.
**Generating on the Pi does not automatically push to GitHub or deploy Netlify.**
The root bundled videos.json is only an initial seed and can become stale.

## Returning visitors during a Pi outage
The HTTPS Pi site caches public pages and the catalogue in a service worker.
Visitors who loaded it successfully before can reopen the same URL and see a
limited hub if the Pi is unreachable. Admin/private APIs/media backups are never
cached. Intro video, PDFs and all range requests bypass the service worker entirely.
They are never pre-downloaded. Only small public resources use the short timeout.
The worker script is served with no-store headers and checks for updates on visits.
Old public caches are removed when the new worker activates.
If your reverse proxy overrides caching, exclude /service-worker.js from its cache.
An unavailable offline intro skips to the pill choice; stalled startup shows play
after three seconds.

Service workers require HTTPS (localhost is the development exception). Private
browsing, cleared/evicted storage, an unfinished first visit, or a different
hostname can prevent recovery. First-time visitors need the Netlify fallback URL;
this does not automatically change DNS or redirect an unreachable Pi.
With no internet at all, cached pages/catalogue remain visible, but YouTube cannot
play. Users may see browser-specific restrictions for YouTube embeds under
file://; Open on YouTube remains available.
