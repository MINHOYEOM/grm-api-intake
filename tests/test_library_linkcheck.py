from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

import library_linkcheck as lc


class _Response:
    def __init__(self, status: int, url: str = "https://example.test/final", text: str = ""):
        self.status_code = status
        self.url = url
        self.text = text

    def close(self):
        pass


class LibraryLinkcheckTest(unittest.TestCase):
    def test_host_pacer_reserves_per_host_slots(self):
        clock = mock.MagicMock(side_effect=[0.0, 0.0, 0.0])
        sleeps = []
        pacer = lc.HostPacer(monotonic=clock, sleeper=sleeps.append)
        pacer.wait("https://a.test/1", 1.0)
        pacer.wait("https://a.test/2", 1.0)
        pacer.wait("https://b.test/1", 1.0)
        self.assertEqual(sleeps, [1.0])

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
            _Response(403), _Response(503), _Response(403),
            # Genuine success: same host, benign body — must stay "ok".
            _Response(200, url="https://www.fda.gov/a", text="<html>guidance document</html>"),
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

    # ── 200 위장 차단(ecfr.gov 실측: 봇 의심 요청에 HTTP 200 + 다른 호스트로 리다이렉트) ──

    def test_bot_sensitive_200_forces_get_even_when_head_succeeds(self):
        """HEAD 만으로는 위장 차단(본문 없음)을 못 잡는다 — 봇 민감 호스트는 HEAD 가
        200 이어도 GET 까지 가야 한다(비민감 호스트의 test_head_success_does_not_get 과
        대비되는 케이스)."""
        session = mock.MagicMock()
        session.request.return_value = _Response(200, url="https://www.ecfr.gov/current/x")
        result = lc.probe_url(
            "https://www.ecfr.gov/current/x", session=session, delay=0, timeout=3,
            sleeper=lambda _x: None,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(session.request.call_count, 2)  # HEAD + GET, HEAD 단독 종료 금지

    def test_disguised_block_via_off_host_redirect_is_needs_review(self):
        """ecfr.gov 실측 그대로: HEAD/GET 모두 200 이지만 최종 URL 이 완전히 다른
        호스트(unblock.federalregister.gov)로 튄다 — ok 로 단정하면 안 된다."""
        session = mock.MagicMock()
        session.request.return_value = _Response(
            200, url="https://unblock.federalregister.gov/", text="Request Access",
        )
        result = lc.probe_url(
            "https://www.ecfr.gov/current/title-21/part-211/section-211.192",
            session=session, delay=0, timeout=3, sleeper=lambda _x: None,
        )
        self.assertEqual(result.status, "needs_review")
        self.assertIn("suspected_bot_block", result.reason)
        self.assertIn("redirected_off_host", result.reason)

    def test_disguised_block_via_body_markers_same_host_is_needs_review(self):
        """호스트는 안 바뀌어도(같은 호스트) 본문이 전형적 차단 페이지 문구면 의심한다."""
        session = mock.MagicMock()
        session.request.return_value = _Response(
            200, url="https://www.fda.gov/a",
            text="<html><body>Please enable JavaScript and cookies to continue</body></html>",
        )
        result = lc.probe_url(
            "https://www.fda.gov/a", session=session, delay=0, timeout=3,
            sleeper=lambda _x: None,
        )
        self.assertEqual(result.status, "needs_review")
        self.assertIn("content_block_page", result.reason)

    def test_genuine_bot_sensitive_200_with_body_stays_ok(self):
        """차단 신호(호스트 변경·차단 문구)가 전혀 없으면 봇 민감 호스트도 계속 ok —
        지금 ok 로 잡히는 정상 링크가 이 수리로 needs_review 로 넘어가면 안 된다."""
        session = mock.MagicMock()
        session.request.return_value = _Response(
            200, url="https://www.canada.ca/en/health-canada/guide.html",
            text="<html><title>GMP Guide</title><body>Good manufacturing practices</body></html>",
        )
        result = lc.probe_url(
            "https://www.canada.ca/en/health-canada/guide.html",
            session=session, delay=0, timeout=3, sleeper=lambda _x: None,
        )
        self.assertEqual(result.status, "ok")

    def test_bot_sensitive_host_registry_includes_ecfr(self):
        self.assertIn("ecfr.gov", lc.BOT_SENSITIVE_HOSTS)
