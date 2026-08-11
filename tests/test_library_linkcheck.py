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

    def test_fda_403_is_blocked_and_network_failure_is_review(self):
        """★403 과 타임아웃은 **다른 사실**이다. 403 은 차단의 적극적 증거라 blocked,
        타임아웃은 그냥 못 닿은 것이라 needs_review — 후자를 blocked 에 넣으면 '확인 못 한 게
        정상'이라는 통에 진짜 네트워크 장애가 섞여 묻힌다."""
        session = mock.MagicMock()
        session.request.side_effect = [_Response(403)] * 4
        blocked = lc.probe_url(
            "https://www.fda.gov/a", session=session, delay=0, timeout=3,
            sleeper=lambda _x: None,
        )
        self.assertEqual(blocked.status, "blocked")
        self.assertIn("suspected_bot_block", blocked.reason)

        session = mock.MagicMock()
        session.request.side_effect = [requests.Timeout()] * 4
        flaky = lc.probe_url(
            "https://www.fda.gov/a", session=session, delay=0, timeout=3,
            sleeper=lambda _x: None,
        )
        self.assertEqual(flaky.status, "needs_review")
        self.assertIn("network_or_tls", flaky.reason)
        self.assertNotIn("suspected_bot_block", flaky.reason)

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

    def test_disguised_block_via_off_host_redirect_is_blocked(self):
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
        self.assertEqual(result.status, "blocked")
        self.assertIn("suspected_bot_block", result.reason)
        self.assertIn("redirected_off_host", result.reason)

    def test_disguised_block_via_body_markers_same_host_is_blocked(self):
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
        self.assertEqual(result.status, "blocked")
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


class BlockedStatusSeparationTest(unittest.TestCase):
    """★2026-08-11 신설. `blocked`("검사기가 막혀 확인 못 함")를 `needs_review`("링크가
    수상하니 사람이 보라")에서 분리했다. 계기: 21 CFR 63건이 러너에서 전부
    unblock.federalregister.gov 로 리다이렉트됐는데, 사람이 브라우저로 누르면 정상이고
    수집기도 eCFR API 로 63건을 정상 수집한다 — 링크는 멀쩡하고 검사기만 못 본다.
    합쳐 두면 매주 63건이 진짜 신호(MFDS 5xx 90건)를 덮는다."""

    def _probe(self, url, response):
        session = mock.MagicMock()
        session.request.return_value = response
        return lc.probe_url(url, session=session, delay=0, timeout=3, sleeper=lambda _x: None)

    def test_four_distinct_statuses_exist(self):
        """네 값은 서로 다른 사실이고 조치도 다르다 — 어휘가 줄어들면 진단이 뭉개진다."""
        ok = self._probe("https://example.test/a", _Response(200))
        broken = self._probe("https://example.test/a", _Response(404))
        blocked = self._probe(
            "https://www.ecfr.gov/current/x",
            _Response(200, url="https://unblock.federalregister.gov/"))
        review = self._probe("https://example.test/a", _Response(503))
        self.assertEqual(
            [ok.status, broken.status, blocked.status, review.status],
            ["ok", "broken", "blocked", "needs_review"])

    def test_summary_reports_blocked_separately_from_needs_review(self):
        """report.summary 가 두 축을 따로 센다 — 합산하면 분리한 의미가 사라진다."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "cfr.json").write_text(json.dumps({"items": [
                {"id": "cfr-211-192", "official_url": "https://www.ecfr.gov/current/a"},
            ]}), encoding="utf-8")
            (root / "x.json").write_text(json.dumps({"items": [
                {"id": "x1", "official_url": "https://example.test/flaky"},
            ]}), encoding="utf-8")

            def fake_probe(url, **_kw):
                if "ecfr.gov" in url:
                    return lc.Probe("blocked", 206, "GET", 1,
                                    "suspected_bot_block:redirected_off_host:unblock.federalregister.gov",
                                    "https://unblock.federalregister.gov")
                return lc.Probe("needs_review", 503, "GET", 2,
                                "transient_or_access_control:http_503", url)

            with mock.patch.object(lc, "probe_url", side_effect=fake_probe):
                report = lc.build_report(root, delay=0, timeout=3, max_workers=2)

        summary = report["summary"]
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(summary["broken"], 0)
        # 봇차단 1건이 needs_review 에 섞여 들어가지 않았는지(합산 금지)를 못 박는다.
        self.assertNotEqual(summary["needs_review"], 2)

    def test_schema_version_declares_the_new_vocabulary(self):
        """옛 스냅샷과 needs_review 카운트를 직접 비교하면 안 되므로 버전으로 알린다."""
        self.assertEqual(lc.SCHEMA_VERSION, "grm-library-health/v2")

    def test_blocked_never_hides_a_dead_link(self):
        """봇 민감 호스트라도 404/410 은 blocked 가 아니라 broken 이다 — 죽은 링크가
        '차단이라 확인 못 함'으로 위장되면 영영 안 고쳐진다."""
        for code in (404, 410):
            result = self._probe("https://www.ecfr.gov/current/gone", _Response(code))
            self.assertEqual(result.status, "broken", code)
