
# 📁 Video Hub - README

This guide explains how to manage videos for your `extra.html` video hub, including how to:

- ✅ Add new videos
- ⭐ Set a featured video
- 🔍 Tag and filter content
- 📦 Use Dropbox, Cloudflare, YouTube, or self-hosted videos

---

## 📄 `videos.json` Structure

Each video is an object in a JSON array. Supported fields:

```json
{
  "title": "Your Video Title",
  "video_id": "Cloudflare_ID",        // Optional: for Cloudflare Stream
  "youtube_id": "YouTubeVideoID",     // Optional: for YouTube
  "url": "videos/myfile.mp4",         // Optional: for local video file
  "dropbox_url": "https://...&raw=1", // Optional: for Dropbox-hosted video
  "category": "CategoryName",
  "tags": ["tag1", "tag2"],
  "added": "2025-06-07T00:00:00Z",
  "featured": true
}
```

> ❗ You should only use **one** of: `video_id`, `youtube_id`, `url`, or `dropbox_url` per video.

---

## ⭐ How to Set a Featured Video

- Set `"featured": true` on **one** video
- All other videos must have `"featured": false`
- Example:

```json
{
  "title": "My Best Video",
  "youtube_id": "PdsJs0UIm7s",
  "category": "Matrix",
  "tags": ["truth", "mustwatch"],
  "added": "2025-06-07T00:00:00Z",
  "featured": true
}
```

---

## 📦 Using Dropbox Videos

Dropbox videos must be shared with the **`?raw=1`** parameter at the end. Example:

```json
{
  "title": "Dropbox Sample",
  "dropbox_url": "https://www.dropbox.com/s/abc123/myvideo.mp4?raw=1",
  "category": "Matrix",
  "tags": ["dropbox", "truth"],
  "added": "2025-06-04T00:00:00Z",
  "featured": false
}
```

> ✅ If you forget `?raw=1`, the video will **not play**.

---

## 🏷 Tags and Filtering

- Tags help users find related content.
- Tags appear under each video.
- Clicking a tag filters videos by that tag.
- You can also search by tag using the search box.

---

## 🌓 Theme + View Options

- Toggle light/dark mode with the top-right button
- Use category and sort dropdowns to refine your view

---

## 📥 Local Video Hosting (Optional)

If you're uploading `.mp4` files directly, place them in a `videos/` folder and reference them like this:

```json
{
  "title": "Self-hosted Example",
  "url": "videos/yourfile.mp4",
  "category": "Offline",
  "tags": ["local", "secure"],
  "added": "2025-06-06T00:00:00Z",
  "featured": false
}
```

---

## 💡 Supported Video Sources Summary

| Source Type        | Field Used       | Example Value                                         |
|--------------------|------------------|--------------------------------------------------------|
| **Cloudflare**     | `"video_id"`     | `"ca53f3a766e01764d750d02186803f74"`                 |
| **YouTube**        | `"youtube_id"`   | `"PdsJs0UIm7s"`                                       |
| **Local MP4**      | `"url"`          | `"videos/filename.mp4"`                              |
| **Dropbox**        | `"dropbox_url"`  | `"https://www.dropbox.com/s/xyz/myvid.mp4?raw=1"`    |
