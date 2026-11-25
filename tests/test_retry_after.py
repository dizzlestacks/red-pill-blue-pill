"""Retry-After is honoured across restarts and in both header formats (simulated responses only)."""
import email.utils
import time
from unittest.mock import MagicMock, patch
from test_watcher import WatchTests
import media_worker as worker

REAL_API = worker.api


def response(status, headers=None, body=None):
    r = MagicMock(status_code=status, ok=200 <= status < 300, headers=headers or {})
    r.json.return_value = body or {}
    return r


class RetryAfterTests(WatchTests):
    def tearDown(self):
        worker.BACKOFF_UNTIL = 0.0
        worker.LAST_REQUEST = 0.0

    def call(self, r):
        with patch.object(worker.requests, "request", return_value=r) as req, \
             patch.object(worker.time, "sleep") as sleep:
            try:
                REAL_API("/capabilities")
            except worker.UpstreamError as e:
                return req, sleep, e
        return req, sleep, None

    def test_seconds_header_persists_over_restart(self):
        _, _, error = self.call(response(429, {"Retry-After": "120"}))
        self.assertEqual(error.status, 429)
        self.assertGreater(worker.persisted_backoff(), time.time() + 115)
        # Simulated restart: in-memory monotonic backoff is lost, the stored deadline is not.
        worker.BACKOFF_UNTIL = 0.0
        worker.LAST_REQUEST = 0.0
        _, sleep, error = self.call(response(200))
        self.assertIsNone(error)
        self.assertGreater(sleep.call_args[0][0], 115)

    def test_http_date_and_bounds(self):
        future = email.utils.formatdate(time.time() + 300, usegmt=True)
        self.assertAlmostEqual(worker.retry_after_seconds(future), 300, delta=3)
        self.assertEqual(worker.retry_after_seconds("garbage"), 60)
        self.assertEqual(worker.retry_after_seconds("5"), 60)
        self.assertEqual(worker.retry_after_seconds("999999"), worker.MAX_RETRY_AFTER)
        worker.persisted_backoff(time.time() + 10 ** 9)
        self.assertLessEqual(worker.persisted_backoff(), time.time() + worker.MAX_RETRY_AFTER + 1)

    def test_other_errors_do_not_set_backoff(self):
        for status in (401, 402, 403, 503):
            _, _, error = self.call(response(status))
            self.assertEqual(error.status, status)
        self.assertEqual(worker.persisted_backoff(), 0)
