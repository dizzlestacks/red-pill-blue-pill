"""Manual single/bulk additions use the same durable catalogue and backup queue."""
import time
import uuid
import admin_store as store
import tiktok_links


def add(body):
    urls = body.get("urls")
    if not isinstance(urls, list) or not 1 <= len(urls) <= 100:
        raise ValueError("Paste between 1 and 100 video links")
    metadata = body.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("Video details must be an object")
    if not isinstance(metadata.get("title", ""), str) or not isinstance(metadata.get("added", ""), str):
        raise ValueError("Title and date must be text")
    keep = body.get("keep_copy", True) is True
    resolved = []
    deadline = time.monotonic() + 20
    # Validate the complete batch before saving anything.
    from urllib.parse import urlparse
    for url in urls:
        if not isinstance(url, str):
            raise ValueError("Each link must be text")
        host = (urlparse(url).hostname or "").lower()
        canonical = tiktok_links.resolve(url, deadline) if host == "tiktok.com" or host.endswith(".tiktok.com") else url
        resolved.append(store.source(canonical))
    results = []
    with store.LOCK:
        cat, rev = store.load_catalog()
        for key, url, kind in resolved:
            existing = None
            for video in cat["videos"]:
                try:
                    if store.source(store.video_source(video))[0] == key:
                        existing = video
                        break
                except ValueError:
                    pass
            if existing:
                results.append({"id": existing["id"], "status": "duplicate skipped", "video": existing})
                continue
            added = metadata.get("added") or time.strftime("%Y-%m-%d", time.gmtime())
            title = metadata.get("title", "").strip()
            video = {k: metadata[k] for k in ("category", "tags", "notes", "featured", "pinned", "start", "end") if k in metadata}
            video.update(id=uuid.uuid4().hex, title=title or "Untitled " + added[:10],
                         needs_title=not title or bool(metadata.get("needs_title")),
                         added=added, source_url=url, source=kind,
                         youtube_id=key[3:] if kind == "youtube" else "",
                         url="" if kind == "youtube" else url)
            cat["videos"].append(video)
            results.append({"id": video["id"], "status": "added", "video": video})
        store.save_catalog(cat, rev)
        for result in results:
            video = result.pop("video")
            if keep:
                # Existing catalogue metadata is deliberately never overwritten.
                try:
                    result["backup"] = store.enqueue(store.video_source(video), video["id"], video["title"], video.get("tags", []))
                except Exception:
                    # Catalogue already saved: report the partial outcome, never
                    # suggest nothing happened or silently discard the queue failure.
                    result["backup"] = {"status": "failed", "reason": "Video saved, but its backup could not be queued. Use Back up in Videos to retry."}
    return {"ok": True, "results": results}
