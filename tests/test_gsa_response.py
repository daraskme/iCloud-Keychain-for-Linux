"""GSA response handling without network calls or account credentials."""

import plistlib
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from icp.auth.gsa import GSAClient, GSAError


class GSAResponseTests(unittest.TestCase):
    def setUp(self):
        self.client = GSAClient(None, None)
        self.client._cpd = lambda: {}

    def test_html_error_reports_status_without_body(self):
        response = SimpleNamespace(
            status_code=503,
            headers={"Content-Type": "text/html"},
            content=b"<html>private upstream diagnostic</html>",
        )
        with patch("icp.auth.gsa.requests.post", return_value=response):
            with self.assertRaises(GSAError) as caught:
                self.client._request({"o": "init"})
        self.assertIn("HTTP 503 (text/html)", str(caught.exception))
        self.assertNotIn("private upstream diagnostic", str(caught.exception))

    def test_plist_response_still_parses(self):
        response = SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "text/x-xml-plist"},
            content=plistlib.dumps({"Response": {"Status": {"ec": 0}}}),
        )
        with patch("icp.auth.gsa.requests.post", return_value=response) as post:
            self.assertEqual(
                self.client._request({"o": "init"}), {"Status": {"ec": 0}}
            )
        self.assertIn("com.apple.akd/1.0", post.call_args.kwargs["headers"]["X-MMe-Client-Info"])


if __name__ == "__main__":
    unittest.main()
