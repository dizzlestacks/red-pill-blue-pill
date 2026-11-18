# Release 2026.11.15.5

## Included
Persistent TikTok watcher settings, scheduled checks, manual check and bounded older-post batches; automatic queue creation with caption-derived titles, hashtags and original publication dates; review-first by default or explicit auto-publication after permanent-copy verification. Duplicate detection covers queue and catalogue.

End-card choices are saved per import and immutable after submission. The shared API rate gate is unchanged (about 28 requests/minute); hourly admission defaults to six and survives restarts. The worker checks free disk space before submitting and throughout copying, preserving a default 5 GiB reserve. It never deletes files to make room. Temporary media now uses the data filesystem.

Phone confirmations use a fixed bottom sheet, visual-viewport positioning, safe areas, locked background scrolling and focus restoration. Notices remain visible beside modal actions or above the keyboard.

## Important upstream limitation
At build time the live developer handoff still showed revision 4 and OpenAPI 1.1.0. Neither documented the watched-profile list nor an end-card input field. Live watching therefore says **watching not available yet**. This release does not guess routes or scrape TikTok.

The complete local flow is tested with a normalized simulated provider. To enable the real adapter in watch_provider.py, confirm:
- authenticated list/registration route and capability signal for restricted keys;
- post ID, canonical URL, caption, hashtag, timezone-aware publication date and thumbnail fields;
- incremental and history cursors, ordering, limits and end-of-history semantics;
- exact per-request end-card field, on/off/default semantics, idempotency and final-output readiness.

The internal test page shape is `{posts:[{id,url,caption,hashtags,posted_at,thumbnail}], next_cursor, has_more}`. This is NOT a claimed you2mp3 wire format. A small adapter update is required once the contract is published; installing the upstream update alone does not enable an undocumented endpoint here.

Explicit end-card on/off imports wait honestly for that adapter. Manual paste remains available through **Use service default**, an explicit acknowledgement that no end-card choice is guaranteed. Existing submitted requests keep their exact payload and identity.

## Settings and recovery
The install settings file remains `/etc/wokearewe/settings.env`, outside code. Initial defaults:
```
TIKTOK_PROFILE=https://www.tiktok.com/@wokearewe1
TIKTOK_WATCH_ENABLED=true
TIKTOK_CHECK_MINUTES=15
IMPORTS_PER_HOUR=6
MIN_FREE_GB=5
TIKTOK_AUTO_PUBLISH=false
TIKTOK_ADD_END_CARD=true
```
These seed a new installation. After first start, use the Imports tab to change them: persisted admin choices take precedence over seed environment defaults. Existing installs receive defaults without replacing their settings or secrets. Watcher settings/cursors, post metadata and hourly admissions live in DATA_DIR/admin.sqlite and participate in existing recovery snapshots and optional mirroring.

Update with the existing `sudo bash pi/update.sh` from the extracted verified Pi bundle. Netlify still receives only the public catalogue; no watcher metadata, queue, credentials or permanent media is pushed.

## Verification scope
Automated backend tests use simulated discovery and submission, not bulk live imports. Phone-sized Chromium tests exercise admin actions, settings, bottom-sheet position, short viewport, safe focus and scrolling. Physical iPhone browser bars/keyboard, Pi disk/mount behavior, the new live provider contract and an actual owned-post end-to-end import remain deployment acceptance checks.
