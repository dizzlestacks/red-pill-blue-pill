# Release 2026.11.15.13 — /admin looks exactly like the Video Hub

## One shared stylesheet
- The Video Hub's styles moved out of `videos.html` into `hub.css`, unchanged.
  The hub loads it in the same place, so it looks and behaves the same
  (checked by comparing 127 computed styles before and after).
- /admin now loads the same `hub.css`, so it uses the hub's real styles, not
  a copy that could drift.

## What /admin now shares with the hub
- Share Tech Mono everywhere, including form labels and body text (the
  rounded font is gone).
- Falling code, scanlines, scan bar, vignette, edge glow, flicker, green tint,
  HUD corners and the REC / status corner text.
- The hub's two top bars: links (Back to Video Hub, Read Only Version) and the
  colour palette menu with the same upper-case theme names.
- Same 30 themes and the same saved choice as the hub (default Terminal).
  Changing the theme on either page changes both.
- Page titles use the hub's glitch title and the bracketed signal line.
- Boxes use the hub's featured box, buttons its `.btn`, main actions its
  "I'm feeling lucky" button, chips its category chips, the bottom navigation
  its tabs, text fields its search box and pop-ups its pop-up box.

## Kept for phones
- Five-section bottom navigation, plain wording, one action per card,
  dismissible notifications and pop-ups at thumb height are unchanged.
- Form text is 16px (phones don't zoom in), buttons and chips are at least
  44px tall, fields 48px, with stronger text contrast than the hub's small
  grey text. No sideways scrolling at 320px or 390px wide.

## Tests
- The mobile pop-up test now waits for the opening to settle before simulating
  the keyboard (the page now animates like the hub).
