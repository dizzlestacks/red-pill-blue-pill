"""Explicit public export. Never serialize private catalogue fields wholesale."""
import os
import re
from urllib.parse import urlparse


def pi_origin():
    value = os.environ.get("PI_PUBLIC_URL", "").strip().rstrip("/")
    p = urlparse(value)
    if value and (p.scheme != "https" or not p.hostname or p.username or p.password or p.path or p.query or p.fragment):
        raise ValueError("PI_PUBLIC_URL must be an HTTPS origin without a path or credentials")
    return value


def export(catalog, configured_origin=True):
    videos = []
    for item in catalog.get("videos", []):
        v = {}
        tiktok = item.get("source_url") or item.get("tiktok_url") or ""
        if isinstance(tiktok,str) and re.fullmatch(r"https://www\.tiktok\.com/@[\w.-]+/video/\d{8,25}",tiktok):
            v["tiktok_url"] = tiktok
        for key in ("id", "title", "added"):
            if isinstance(item.get(key), str):
                v[key] = item[key][:1000]
        for key in ("pinned", "featured"):
            v[key] = item.get(key) is True
        v["tags"] = [t[:100] for t in item.get("tags", []) if isinstance(t, str)][:100]
        cats = item.get("category", [])
        v["category"] = [c[:100] for c in (cats if isinstance(cats, list) else [cats]) if isinstance(c, str)][:100]
        youtube = item.get("youtube_id", "")
        if isinstance(youtube, str) and re.fullmatch(r"[\w-]{11}|PL[\w-]+", youtube):
            v["youtube_id"] = youtube
        elif not v.get("tiktok_url"):
            v["limited_unavailable"] = True
        videos.append(v)
    popup = catalog.get("popup", {})
    # Popup copy is intentionally public. Notes, source URLs, internal media
    # paths, queue state, backup records and arbitrary extension fields are not.
    return {"videos": videos, "popup": {"enabled": popup.get("enabled") is True,
            "message": str(popup.get("message", ""))[:10000]},
            "public_pi_url": pi_origin() if configured_origin else catalog.get("public_pi_url", "")}
