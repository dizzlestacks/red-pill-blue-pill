"""Resolve TikTok share redirects without following arbitrary external URLs."""
import re
import time
from urllib.parse import urlparse, urljoin
import requests
import admin_store as store

HOSTS = {"vt.tiktok.com", "vm.tiktok.com", "tiktok.com", "www.tiktok.com"}


def validate(url):
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError("Invalid TikTok link")
    p = urlparse(url.strip())
    if p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError("TikTok share links must use HTTPS on TikTok's own domain")
    if p.hostname in {"vt.tiktok.com", "vm.tiktok.com"}:
        valid = re.fullmatch(r"/[\w-]+/?", p.path)
    else:
        valid = re.fullmatch(r"/(?:t/[\w-]+|@[\w.-]+/video/\d{8,25})/?", p.path)
    if not valid:
        raise ValueError("Paste a TikTok post or share link, not a profile")
    return p


def resolve(url, deadline=None):
    current = url.strip() if isinstance(url, str) else url
    deadline = min(deadline or float("inf"), time.monotonic() + 15)
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(6):
            p = validate(current)
            if "/video/" in p.path:
                try:
                    key, canonical, kind = store.source(current)
                except ValueError:
                    raise ValueError("This queue accepts posts owned by @wokearewe1 only")
                return canonical
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("TikTok link resolution timed out. Retry fewer links or paste the full post address.")
            try:
                with session.get(current, allow_redirects=False, stream=True,
                                 timeout=(min(3, remaining), min(5, remaining)),
                                 headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"}) as response:
                    if response.status_code not in (301, 302, 303, 307, 308):
                        raise ValueError("TikTok did not resolve this share link. It may be expired or blocked. Copy the full post address and retry.")
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("TikTok returned an empty redirect. Paste the full post address.")
                    current = urljoin(current, location)
            except requests.RequestException:
                raise ValueError("Could not reach TikTok to resolve this share link. Retry or paste the full post address.")
    raise ValueError("Too many TikTok redirects. Paste the full post address.")
