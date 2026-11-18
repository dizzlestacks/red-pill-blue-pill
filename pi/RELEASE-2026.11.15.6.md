# Release 2026.11.15.6 — live profile connection

Uses the documented GET /api/v1/tiktok-watch/posts?after=N&limit=25, with integer discovery cursors (NOT publication timestamps). Maps postId, caption, hashtags, postedAt, thumbnail and firstSeenAt. Submissions send addEndCard true/false, preserving the original payload on retry. The shared rate gate, disk reserve, hourly limit, independent permanent copies and review-first default remain.

## Safe first run
On first upgrade from .5 a watching-start timestamp is persisted in the private database. Restarting does not change it. Enabling watching after it was off resets the timestamp; changing profiles resets cursors too. Only posts with a known original publication date strictly newer than that timestamp are automatically queued. Discovery pages are scanned in bounded batches without downloading the old inventory.

The Imports tab shows the discovered inventory count (313 at verification) and the watching start time. **Import older posts** explicitly queues at most 25 listed posts per press, using a separate persistent cursor. Duplicates are skipped. A count of discovered posts is not a claim that TikTok history is complete.

Nullable metadata is supported: absent caption is visibly labelled, unknown publication dates never qualify for automatic imports. Explicit older batches can include those posts for review. Catalogue added date for an unknown source date remains the local publication date, not a claimed original TikTok date.

## Documentation and checks
Authenticated read-only checks of /tiktok-watch and a one-post page succeeded and reported 313 posts. No real import/download was initiated during verification. Contract fixtures cover exact live fields, paging, nullable metadata, old/new cutoff, end-card true/false and readiness states. Existing phone, recovery, disk and hourly safeguards are regression tested.

The OpenAPI version still says 1.1.0 despite the new routes. It calls inventory completeness best-effort; it does not guarantee all historical posts. The integration HTML retrieved during this build still contained older handoff text, while the developer page is a client-rendered shell; the updated OpenAPI and actual authenticated responses were authoritative. No blocker remains for discovery.

Final copies wait for job status done, an output filename and terminal successful end-card states. OpenAPI does not explicitly promise when endCards appears relative to done. Actual end-card-enabled media completion and playback on the Pi still require one real-post acceptance test.

Install using the existing Pi update step. No new secret is needed. Do not lower the disk reserve merely to pass a check; free space or move DATA_DIR to suitable storage instead.
