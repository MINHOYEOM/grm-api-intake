from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import library_linkcheck as lc


class _Response:
    def __init__(self, status: int, url: str = "https://example.test/final"):
        self.status_code = status
        self.url = url

    def close(self):
        pass


class LibraryLinkcheckTest(unittest.TestCase):
    def test_collects_supported_fields_and_reference_locations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.json").write_text(json.dumps({"items": [{
                "id": "x", "official_url": "https://example.test/a",
                "pdf_url": "https://example.test/a.pdf",
            }]}), encoding="utf-8")
            refs, files = lc.collect_urls(root)
        self.assertEqual(files, ["a.json"])
        self.assertEqual(refs["https://example.test/a"][0]["field"], "official_url")
        self.assertEqual(len(refs), 2)

    def test_head_success_does_not_get(self):
        session = mock.MagicMock()
        session.request.return_value = _Response(200)
        sleeps = []
        result = lc.probe_url(
            "https://example.test/a", session=session, delay=1, timeout=3,
            sleeper=sleeps.append,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(sleeps, [1])

    def test_head_failure_uses_get_fallback_and_retry(self):
        session = mock.MagicMock()
        session.request.side_effect = [
            _Response(403), _Response(503), _Response(403), _Response(200),
        ]
        sleeps = []
        result = lc.probe_url(
            "https://www.fda.gov/a", session=session, delay=2, timeout=3,
            sleeper=sleeps.append,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.attempts, 2)
        self.assertEqual(sleeps, [2, 2, 2, 2])

    def test_fda_403_and_network_failure_are_review_not_broken(self):
        for side_effect in (
            [_Response(403), _Response(403), _Response(403), _Response(403)],
            [requests.Timeout(), requests.Timeout(), requests.Timeout(), requests.Timeout()],
        ):
            session = mock.MagicMock()
            session.request.side_effect = side_effect
            result = lc.probe_url(
                "https://www.fda.gov/a", session=session, delay=0, timeout=3,
                sleeper=lambda _x: None,
            )
            self.assertEqual(result.status, "needs_review")
            self.assertIn("suspected_bot_block", result.reason)

    def test_404_is_broken_even_on_canada(self):
        session = mock.MagicMock()
        session.request.return_value = _Response(404)
        result = lc.probe_url(
            "https://www.canada.ca/a", session=session, delay=0, timeout=3,
            sleeper=lambda _x: None,
        )
        self.assertEqual(result.status, "broken")
        self.assertEqual(session.request.call_count, 1)

