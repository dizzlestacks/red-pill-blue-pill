# Release 2026.11.15.8

## Import previews that play on iPhone

**Cause:** the Pi delivered the saved files correctly. Through the public
address and Cloudflare, signed-in range requests got `206` with
`Content-Range`, `Accept-Ranges: bytes`, `video/mp4` and the correct length.
The problem was the video format. The waiting import was H.264 marked as level
6.2, which iPhones won't play. An earlier import was HEVC video with MP3 audio,
which many browsers can't play. Earlier releases saved and served the TikTok
download exactly as received.

**Fix:** each saved video is checked with ffprobe.
- If it is not H.264 (level 4.2 or lower, yuv420p) with AAC audio and the index
  at the start, a separate browser-friendly version is made automatically.
- Only the level marking too high: the level is rewritten without re-encoding
  (seconds).
- Other video codecs: re-encoded to H.264. On a Pi this takes several minutes
  per minute of video.
- Other audio codecs: re-encoded to AAC.
- Players use the browser-friendly version. Downloads still give the
  untouched original.
- Both copies are kept in the data folder and mirrored to the NAS.

Videos saved by earlier releases are converted one at a time in the background
after the update is installed.

**Admin preview:**
- Each import shows a poster image.
- If a preview cannot play, the import shows the reason in plain words: still
  processing, file missing, format not supported, connection problem, or not
  enough free space. It also offers **Retry preview**, which re-checks the saved
  file without re-importing.
- Publishing is blocked until the preview is playable.
- Automatic publishing waits for a playable preview.

**Video delivery changes:**
- Video files on disk are served directly from the data folder. They are no
  longer first copied to the temporary folder.
- Video responses include an ETag and `Cache-Control: private, no-cache`, so a
  browser notices when the browser-friendly version replaces the original.
- Range requests that cannot be read get a proper `416` response.

**Public hub:** saved copies show their poster and play the same
browser-friendly file.

**Captions made only of hashtags:** these no longer become titles. The post
becomes "Untitled <post date>" and is marked as needing a title. The hashtags
stay in the hashtags field. Unpublished queue items from earlier releases are
corrected on startup.

**Tests:** `tests/test_playback_routes.py` starts the real server. It saves
simulated incompatible downloads: H.264 level 6.2 with MP3 audio, and HEVC with
the index at the end. It then checks the admin preview and public `/media`
routes for range requests, posters and original downloads. It also checks the
missing-file reason and retry, and hashtag-only titles.
