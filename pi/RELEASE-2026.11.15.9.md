# Release 2026.11.15.9 — TikTok-watching task wrap-up

Builds on 2026.11.15.8 without changing watcher behaviour, end-card policy
(TikTok imports never request end cards, as decided in .7), playback copies,
back-catalogue runs or public sync.

## Fixes
- **Confirmation sheets keep keyboard focus inside.** Tab and Shift+Tab now cycle
  between Cancel and Confirm instead of escaping to the page or browser.
- **"Saved" messages stay in view.** After confirming an action far down the
  page (for example replacing the catalogue in Settings), the message was placed
  at the top of the tab, thousands of pixels above. It now appears next to the
  action and is scrolled into view.
- **Rate-limit waits survive restarts.** When the media service answers 429 with
  Retry-After, the deadline is stored in the private database (and therefore in
  recovery snapshots). A restart no longer lets the Pi call again early. Both
  seconds and HTTP-date formats are accepted; waits are at least 60 seconds and
  at most one hour.

## Checks
- `python3 -m unittest discover -s tests`: Retry-After seconds/date/bounds and
  restart persistence use simulated responses. A test that depended on run order
  (hashtag-only captions) now sets up its own private data folder.
- `tests/mobile-dialogs.cjs` (Chromium, phone emulation, against
  `tests/preview_server.py` with temporary data and a test-only password):
  signed-out 401 and missing-CSRF 403; 390×844, 320×568, 390×300 and 844×390;
  deep scroll restored after Cancel; focus starts on Cancel, stays in the sheet and
  returns to the trigger; actions at least 48 px and on screen; long text scrolls
  inside the sheet; a simulated smaller visual viewport (keyboard) keeps actions
  visible; Escape cancels; Delete video, Publish this import (simulated queue
  item) and Replace catalogue sheets; saved message visible.

## Not verified
WebKit, real iPhone Safari browser bars and on-screen keyboard, a physical Pi,
and the live media service. No real imports or downloads were made.

No new settings. Install with the existing `pi/update.sh` step.
