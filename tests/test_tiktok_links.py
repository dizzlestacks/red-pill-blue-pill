"""No real TikTok requests or owner credentials are used by these tests."""
import sys
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tiktok_links as links

POST="https://www.tiktok.com/@wokearewe1/video/1234567890123456789"


def response(location=POST, status=302):
    value=MagicMock()
    value.__enter__.return_value=value
    value.status_code=status
    value.headers={"Location":location} if location else {}
    return value


class ShareLinks(unittest.TestCase):
    def setUp(self):
        owner = patch("tiktok_watch.owner", return_value="wokearewe1")
        owner.start()
        self.addCleanup(owner.stop)
        self.patcher=patch.object(links.requests,"Session")
        self.factory=self.patcher.start()
        self.session=self.factory.return_value.__enter__.return_value
        self.session.get.return_value=response()
        self.addCleanup(self.patcher.stop)

    def test_all_supported_share_forms(self):
        for url in ("https://vt.tiktok.com/Abcd/","https://vm.tiktok.com/Abcd/","https://tiktok.com/t/Abcd/","https://www.tiktok.com/t/Abcd/"):
            with self.subTest(url=url):
                self.assertEqual(links.resolve(url),POST)
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in self.session.get.call_args_list))

    def test_canonical_needs_no_network(self):
        self.assertEqual(links.resolve(POST+"?share=1"),POST)
        self.session.get.assert_not_called()

    def test_chain(self):
        self.session.get.side_effect=[response("https://vm.tiktok.com/B/"),response("/C/"),response(POST)]
        self.assertEqual(links.resolve("https://vt.tiktok.com/A/"),POST)
        self.assertEqual(self.session.get.call_count,3)

    def test_unsafe_destinations_never_requested(self):
        for destination in ("http://vt.tiktok.com/A/","https://127.0.0.1/","https://tiktok.com.evil.test/A/","https://example.com/","https://user:pass@vt.tiktok.com/A/","https://vt.tiktok.com:444/A/"):
            with self.subTest(destination=destination):
                self.session.get.reset_mock()
                self.session.get.return_value=response(destination)
                with self.assertRaises(ValueError):links.resolve("https://vt.tiktok.com/A/")
                self.assertEqual(self.session.get.call_count,1)

    def test_other_owner_rejected(self):
        self.session.get.return_value=response(POST.replace("@wokearewe1","@someone"))
        with self.assertRaisesRegex(ValueError,"owned"):links.resolve("https://vt.tiktok.com/A/")

    def test_failed_resolution_explicit(self):
        self.session.get.return_value=response(status=200)
        with self.assertRaisesRegex(ValueError,"did not resolve"):links.resolve("https://vt.tiktok.com/A/")

    def test_redirect_loop_bounded(self):
        self.session.get.return_value=response("https://vt.tiktok.com/A/")
        with self.assertRaisesRegex(ValueError,"Too many"):links.resolve("https://vt.tiktok.com/A/")
        self.assertEqual(self.session.get.call_count,6)

    def test_network_failure_and_deadline(self):
        self.session.get.side_effect=links.requests.Timeout()
        with self.assertRaisesRegex(ValueError,"Could not reach"):links.resolve("https://vt.tiktok.com/A/")
        self.session.get.reset_mock()
        with self.assertRaisesRegex(ValueError,"timed out"):links.resolve("https://vt.tiktok.com/A/",time.monotonic()-1)
        self.session.get.assert_not_called()


if __name__=="__main__":unittest.main()
