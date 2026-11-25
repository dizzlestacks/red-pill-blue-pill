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
