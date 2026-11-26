# Release 2026.11.15.12 — imports show everywhere, category pickers, matching style

## Published imports appear everywhere
- A published TikTok or YouTube import now shows on the Hub, Read Only,
  search, categories, **New** and "I'm feeling lucky", with its poster, and
  plays inline with sound.
- **New** lists videos by when they were published to the hub (newest first),
  not by the original post date. Existing videos keep their place.
- On the lite site, a video that only exists as your own copy plays from the
  Pi when the Pi is reachable. When it is not, the card stays listed and says
  "Temporarily unavailable" instead of showing a blank card.
- The video counter now counts exactly the videos on screen.
- The service-worker cache was renamed so phones drop old cached lists.
- Unknown pages on the Pi show the styled 404 page instead of raw JSON.

## Categories and hashtags you can tap
- Editing a video, reviewing an import and the bulk tools all use tappable
  chips: existing categories and hashtags are listed (most used first), the
  ones on the video start selected, and typing filters them or adds a new one.
  A video can have several categories.
- **Default categories per source**: Automatic → TikTok has "File new TikTok
  posts under"; each YouTube source has its own. New imports start with them
  and you can change them while reviewing.
- **Videos → Select several**: tick videos, then add or remove categories on
  all of them at once.
- **Videos → Possible duplicates**: finds entries with the same YouTube video,
  TikTok post or title. You choose to merge (categories and hashtags are
  combined, the earliest date is kept) or remove one. Nothing is removed
  without your confirmation, and NAS files are never deleted.

## Style
- /admin now uses the public site's font (Share Tech Mono), theme colours,
  falling code, scanlines and card style, while keeping large readable text.
- Public pages served by the Pi carry the same release footer as the lite
  site, so both are identical.
