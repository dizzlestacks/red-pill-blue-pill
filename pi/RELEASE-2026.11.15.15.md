# Release 2026.11.15.15 — Publish to YouTube

Builds on 2026.11.15.14 (Twitter link removed).

## Publish to YouTube (Admin → Automatic)
- Connect once with Google's own sign-in. The panel shows "Connected as
  @wokearewe1" and a Disconnect button. The Pi keeps Google's permission in an
  owner-only file on the Pi; no password is ever asked for or stored.
- The Pi reads your channel and sorts every TikTok-sourced hub video into
  **Already on YouTube** (with the match, which you can change), **Needs your
  check** (unsure matches) and **Not on YouTube yet**.
- Nothing uploads until you choose who can see the uploads and press Start.
  An optional switch also uploads new TikTok imports.
- Each upload uses your saved copy, the video's title, the TikTok caption plus
  a wokearewe.com link, and the hashtags as tags. Category, "made for kids"
  and paid promotion are settings; short vertical videos are marked #Shorts.
  A separate switch can add the end card to YouTube uploads only (made by
  you2mp3, which uses your you2mp3 allowance).
- After each upload or approved match, the hub entry gets its YouTube link.
  Your own copy still plays first; YouTube is the fallback. The public site
  syncs.
- Daily limits: your chosen number per day, spread out, never above Google's
  100. When YouTube says a limit is reached the Pi waits until midnight Pacific
  time and continues. Uploads resume after restarts without duplicates.
- An honest note before Start explains Google's audit rule (since 2 November
  2026 it no longer forces uploads private; it only matters above 100 a day).

## Other fixes
- A TikTok entry that gets a YouTube link keeps its TikTok identity everywhere:
  its saved copy, duplicate checks, NAS library name, share link and "watched"
  mark don't change.

## Tested with
- A simulated YouTube channel (no real Google account): duplicate detection,
  daily quota exhaustion and next-day resume, a power cut mid-upload and just
  after finishing, an expired upload session, a channel that isn't
  @wokearewe1, and a removed Google permission.
