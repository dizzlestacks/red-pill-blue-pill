# Release 2026.11.15.11 — a video library you can browse on the NAS

When MIRROR_DIR points at a NAS folder, the Pi now keeps an organised library
there, beside its own recovery copy:

    _system (do not edit)/   the Pi's recovery copy (snapshots, saved media, catalogue copies)
    Videos/<category>/       "YYYY-MM-DD - Title.mp4" — the version that plays everywhere
    Originals/<category>/    the untouched original, same name, when it is different
    Removed from site/       videos deleted from the site (never erased)
    By source/               a list per TikTok account / YouTube source
    index.html               open in any browser; type to search every video
    index.csv                the same list for Excel, Numbers or Google Sheets
    README.txt               what each folder is for

- A video in several categories goes in its first category's folder. No
  category → "Uncategorised"; no real title yet → "Needs title".
- Names use plain letters only, stay short, and differ even on computers that
  ignore upper/lower case. A short id like "[a1b2c3]" is added only when two
  videos would otherwise share a name.
- The index lists title, categories, hashtags, published or not, post date,
  date added, folder, file, original file, original link, length and the
  picture-and-sound check result.
- Renames, category changes, publishing and deleting on /admin are followed
  within about a minute by moving files on the NAS, not copying them again.
- Nothing is ever deleted from the NAS. A file someone changed on the NAS is
  moved to `_system (do not edit)/library-set-aside` and a fresh copy is made.
- The first build runs gently (a few minutes of copying per minute, with
  pauses) and resumes where it left off. If the NAS is unreachable nothing
  else is affected; the library catches up when it is back.
- Only plain files, folders and renames are used — no links, permissions or
  timestamps — so ordinary SMB/NFS shares work.

**Settings → NAS and storage** in /admin shows the library's progress,
problems and a **Rebuild library** button (re-reads every file and copies
anything missing; nothing is deleted).

## After updating
Earlier releases put `objects/` and `backups/` at the top of the NAS folder.
On first start they are moved into `_system (do not edit)/` (a rename, no
copying). For manual recovery, copy `objects/` and `backups/` from inside that
folder. No settings change is needed.

# Also in 2026.11.15.11 — a tidier import queue and YouTube copies

## Remove anything from the import queue
- Every item in **Add → Imports and review** has a **Remove** button, whatever
  its state (waiting, downloading, needs help, failed, ready to publish,
  published). The confirmation says exactly what will happen.
- Removing something that was never published deletes its saved file from the
  Pi; its NAS copy moves to `Removed from site/` (nothing on the NAS is erased).
  A published video stays in the hub with its copy; only the queue entry goes.
- An open download at you2mp3 is cancelled. The cancel is retried in the
  background if you2mp3 can't be reached, so Remove always works straight away.
- **Need help** and **Failed** views have **Remove all in this list**.
- Removed TikTok posts and YouTube videos are never brought back by the
  watchers. Pasting the link again by hand brings it back.

## Clearer cards
- Each card shows one status, one plain reason and only the buttons that make
  sense. Items with no saved copy say "Not downloaded yet" and why, with the
  source thumbnail where one is available.
- The end-card note only appears on TikTok posts.
- "Upstream request outcome is uncertain…" is gone. The Pi now asks you2mp3
  again automatically (shown as **Checking**); if it still can't confirm, the
  card offers **Retry** (a fresh download) or **Remove**.

## YouTube
- Single YouTube links now use you2mp3's YouTube import API, so your own copy
  is kept (never with an end card unless you choose one).
- If the connection isn't allowed to copy YouTube yet the card says "YouTube
  copies are not switched on for this connection yet", offers **Add to hub
  (YouTube only)**, and retries by itself every 30 minutes.
- **Automatic → YouTube watcher** lists the channels and playlists chosen on
  the you2mp3 developers page. New videos can be added (hub entry plus a copy)
  or declined; each source can add new videos automatically. Videos already
  there when watching began are kept apart under "Already there when watching
  began" so they don't flood the New list.
