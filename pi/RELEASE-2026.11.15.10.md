# Release 2026.11.15.10 — picture and sound checks

Builds on 2026.11.15.9. Requested for 2026.11.15.8, but .8 and .9 were
already on the lite-preview branch, so it ships as the next release.

## Picture and sound checks

Reported: an import called "tester" had no sound. Its saved file (27:19, HEVC
video with MP3 sound inside an MP4) does contain sound the whole way through,
but MP3-in-MP4 with HEVC is poorly supported by browsers and the sound is very
quiet (about -29.5 LUFS; phone videos are usually about -14 to -16).

**Every saved copy is now checked before it counts as verified:**
- It has a picture track and a sound track (unless you confirm the original
  has no sound).
- The sound is not silent for the whole video.
- Picture and sound last as long as each other, and as the previous good copy
  when downloaded again (the media service doesn't report the source length).
- The file players receive decodes from start to end without errors.
- Sound in formats browsers don't play is converted to AAC, and very quiet
  sound is raised, in the playback copy only. The original is kept unchanged.

**When a check fails:** the item shows "Problem: <reason>", can't be published,
and is downloaded again automatically up to 3 downloads in total, about 10
minutes apart. A re-download never replaces an existing saved copy with a worse
one. Published videos keep playing until a checked copy replaces them.

**/admin:** "Videos with a problem" on Home; a Problem label, **Download
again** and **Keep without sound** on queue items; and a **Saved video checks**
panel in Videos with progress, the problem list, **Check all saved videos** and
**Download all again**. All saved copies are checked once automatically after
this update.

**Players:** the admin preview, the public backup player and the hub's own
video player now explicitly start unmuted. On iPhone, volume is set with the
side buttons; the pages never change it.

**Tests:** `tests/test_media_checks.py` covers a file with no audio track, a
silent track, MP3 sound (gets an AAC copy, original kept), sound shorter than
the picture, a wrong length, very quiet sound (raised in the playback copy), a
damaged file, automatic re-downloads and their limit, Keep without sound, and
Check all / Download all again.

## Not verified
Real iPhone Safari, a physical Pi, and the live media service. No real imports
or downloads were made; the checks were run against simulated files and a local
copy of the "tester" download.

No new settings. Install with the existing `pi/update.sh` step. The first start
checks every saved copy once (a few minutes per long video on a Pi).
