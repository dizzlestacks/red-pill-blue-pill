# Release 2026.11.15.5

See [automatic watching and mobile dialog release notes](RELEASE-2026.11.15.5.md), including the upstream documentation limitation and initial settings. Existing install/update steps below are unchanged.

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
YouTube needs internet. Admin and saved-copy downloads are hidden. Optional
configured Pi backup playback is available inline while the Pi is reachable.
The notice says only “Limited view”; unavailable controls are hidden.

To manually regenerate:

    DATA_DIR=/var/lib/wokearewe python3 /opt/wokearewe/current/lite_site.py

To update Netlify, deploy the contents of DATA_DIR/lite as the static site using
your normal Netlify upload/deploy process. Alternatively, deliberately copy the
current DATA_DIR/videos.json into the GitHub source catalogue and commit/push;
Netlify's build generates its lite output from that committed catalogue.
Generating a lite folder alone does not deploy it. Configure automatic catalogue
sync below to make Pi edits trigger your GitHub-connected Netlify build.
The root bundled videos.json is only an initial seed and can become stale.

## Automatic public-site sync (optional)

Visitors use wokearewe.com. The Pi is the owner/admin and backup engine.
On GitHub, open your avatar → Settings → Developer settings → Personal access
tokens → Fine-grained tokens → Generate new token. Choose the repository owner,
**Only select repositories**, and `red-pill-blue-pill`. Set **Contents: Read and
write**; Metadata read is automatic. No workflow, administration or other
repository access is needed. Set an expiry and keep the token private.

On the Pi, edit `sudo nano /etc/wokearewe/settings.env`. Add these settings
(installer updates preserve your existing file, so add missing keys yourself):

    GITHUB_SYNC_REPO="dizzlestacks/red-pill-blue-pill"
    GITHUB_SYNC_BRANCH="the-branch-Netlify-actually-builds"
    GITHUB_SYNC_TOKEN=""
    PI_PUBLIC_URL="https://your-pi-backup-hostname.example"
    PUBLIC_SITE_ORIGIN="https://wokearewe.com"

Paste the token between its quotes **on the Pi**, not in GitHub, chat, or source
files. Use the branch shown in Netlify's production branch setting; do not assume
it is main. Test a disposable branch first. Restart:

    sudo systemctl restart wokearewe

The public sync panel in /admin shows not set up, pending, synced or failed,
the last successful time and a Sync now button. Missing settings do not prevent
editing or importing. Changes are detected within three seconds, grouped after
15 seconds without another change, and retried with increasing waits up to
15 minutes. Sync now bypasses the wait, **not** conflict protection.
Only videos.json is committed. Only public display fields and the public Pi
origin are exported, never notes, source URLs, queues, media records or secrets.
Netlify performs its normal build after the commit; “synced” means GitHub
accepted the catalogue, not that Netlify's build has finished.

The first sync expects GitHub's public catalogue to match the bundled seed or
the current Pi public catalogue. Subsequent updates compare GitHub's file SHA
with the last acknowledged SHA and use it in the write. Newer GitHub changes
stop the sync. Review both copies: apply the reviewed GitHub public fields to
the Pi using the admin forms, then Sync now to acknowledge an identical public
catalogue before making further edits. Do not blindly paste the stripped public
JSON over your private source metadata. Conflicts are never force-overwritten.
Unrelated GitHub code changes do not block a catalogue sync.

PI_PUBLIC_URL must be an HTTPS origin without a path or credentials, reachable
from visitors' browsers. Your reverse proxy must preserve video byte-range
requests. PUBLIC_SITE_ORIGIN permits the public site's cross-origin availability
check; use a preview origin only while testing, then restore wokearewe.com.
When reachable, published saved copies can play inline on the Video Hub, including
YouTube-failure fallback. When unreachable the backup controls disappear quietly;
the notice remains just “Limited view.” This does not relay files through Netlify:
the browser streams directly from the Pi while the visitor stays on the public site.
Admin APIs and cookies are never used for public playback.

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
