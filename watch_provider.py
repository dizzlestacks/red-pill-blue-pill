"""Boundary for the forthcoming you2mp3 watched-profile API.

No guessed network routes or request fields. Tests inject a normalized provider.
Enable the production mapping only after the public contract is documented.
"""


class Unavailable(Exception):
    pass


class Provider:
    def page(self, profile, cursor, limit, older=False):
        # GET /videos is a job queue, not a profile inventory.
        raise Unavailable("watching not available yet")

    def end_card_payload(self, payload, enabled):
        raise Unavailable("Per-request end cards are not documented on the live connection yet. "
                          "Wait, or explicitly use the service default for a manual import.")

    def final_ready(self, result):
        # Existing documented jobs can report asynchronous end-card processing.
        return all(card.get("status") in ("added", "not-applicable")
                   for card in result.get("endCards", []))


PROVIDER = Provider()
