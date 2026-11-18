"""you2mp3 documented discovery-cursor API (not publication-date pagination)."""


class Unavailable(Exception):
    pass


class Provider:
    def page(self, profile, cursor, limit, older=False):
        from media_worker import api, UpstreamError
        from tiktok_watch import profile_owner
        after = int(cursor or 0)
        if not 0 <= after <= 2147483647:
            raise ValueError("Invalid discovery cursor")
        try:
            data = api(f"/tiktok-watch/posts?after={after}&limit={min(100, limit)}")
        except UpstreamError as e:
            if e.status in (404, 501):
                raise Unavailable("watching not available yet") from e
            raise
        status = data["status"]
        if profile_owner(status["profile"]) != profile_owner(profile):
            raise ValueError("The connection watches a different profile")
        if not status["enabled"]:
            raise ValueError("The upstream profile watcher is disabled")
        next_cursor = data["nextCursor"]
        if type(next_cursor) is not int or next_cursor < after or type(data["hasMore"]) is not bool:
            raise ValueError("Invalid discovery paging")
        posts = data["posts"]
        previous = after
        normalized = []
        for p in posts:
            if type(p["cursor"]) is not int or not previous < p["cursor"] <= next_cursor:
                raise ValueError("Non-monotonic discovery cursor")
            previous = p["cursor"]
            normalized.append(dict(id=p["postId"], url=p["url"], caption=p["caption"],
                                   hashtags=p["hashtags"], posted_at=p["postedAt"],
                                   thumbnail=p["thumbnail"], first_seen_at=p["firstSeenAt"]))
        return dict(posts=normalized, next_cursor=str(next_cursor), has_more=data["hasMore"],
                    inventory_count=status["postCount"], warning=status.get("warning") or status.get("lastError") or "",
                    completeness=status["completeness"])

    def end_card_payload(self, payload, enabled):
        return dict(payload, addEndCard=bool(enabled))

    def final_ready(self, result):
        # Existing documented jobs can report asynchronous end-card processing.
        return all(card.get("status") in ("added", "not-applicable")
                   for card in result.get("endCards", []))


PROVIDER = Provider()
