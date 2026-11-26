# Release 2026.11.15.16 — Card pictures and titles from TikTok captions

Builds on 2026.11.15.15. Publish to YouTube is unchanged and stays switched off.

## Card pictures (Video Hub, /admin and the public site)
- Every video card shows its picture straight away, like a TikTok profile grid.
  TikTok videos use the cover saved with the import (or a frame of the saved
  copy when there is no cover); YouTube videos use the YouTube thumbnail.
- Vertical covers keep their whole picture (title text on the cover is never
  cropped) over a blurred fill, with a play button. The video itself loads only
  when tapped, so the page stays fast with hundreds of videos.
- Pictures are small (360 px wide JPEG) and are published with the site in
  `thumbs/`, so they also show when the Pi can't be reached — including videos
  that only exist as your own copy ("Temporarily unavailable" stays on top).
- The Pi fills in missing pictures for everything already in the catalogue
  (20 per minute in the background). The public catalogue sync sends new
  pictures and videos.json together in one commit (up to 60 pictures per
  commit), so the site rebuilds once rather than once per picture.

## Titles from TikTok captions
- Every TikTok import — automatic or pasted by hand — takes the caption's words
  as the title and its hashtags as tags. Only tidied: spaces trimmed, very long
  captions cut at a word with "…", TikTok's "Replying to @name" label removed.
  Wording and capitals are never changed.
- When a caption has no words (hashtags only or empty), the Pi reads the text
  printed on the cover and offers it as the title, marked **Needs a title** for
  review. "Untitled <date>" is used only when neither gives a title.
- A title you typed is never changed automatically.

## Review titles (Admin → Videos)
- Each TikTok video's current title beside its TikTok caption (and the text read
  from the cover when the caption has no words). One tap: **Use caption**,
  **Keep current** or **Type a title**. Select several and **Use caption for
  selected**. **Read TikTok captions** fetches your post list so older videos
  can be matched.

## Pi setup
- `install.sh` now also installs `tesseract-ocr` (reads cover text). If it can't
  be installed the Pi carries on and uses "Untitled <date>" instead.
