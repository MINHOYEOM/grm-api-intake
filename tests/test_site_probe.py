#!/usr/bin/env python3
"""site_probe.py(라이브 사이트 합성 점검) 테스트 — 실 네트워크 접촉 없음.

모든 HTTP 는 `site_probe.requests.get`/`.post` 를 모킹해 대체한다(tests/
test_watchlist_notify_service.py 의 `_FakeResponse` 관용구와 동형). 덮는 것:
  · 전부 ok → main() exit 0 · 표에 fail 행 없음
  · /findings/ 500 → 그 점검만 fail(전체도 fail)
  · RPC 지연 2.5s → warn · 3.5s → fail(anon statement_timeout 3s 경계)
  · 월요일 계산 — PROBE_TODAY 가 월/화/일요일(KST)일 때 기대 날짜
  · 영문 브리프 부재 → warn(fail 아님, 전체도 ok 유지)
  · 보안 헤더 부재 → warn(fail 아님)
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import os
import unittest
from unittest import mock

import site_probe
site_probe.RETRY_SLEEP_S = 0.0

BASE = "https://grm-solutions.com"
SUPABASE = "https://example.supabase.co"


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", headers=None,
                elapsed_s: float = 0.05):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.elapsed = dt.timedelta(seconds=elapsed_s)


def _dispatch(mapping: dict[str, FakeResponse]):
    def _side_effect(url, **_kwargs):
        if url not in mapping:
            raise AssertionError(f"stub 에 없는 URL 호출: {url}")
        return mapping[url]
    return _side_effect


def _ok_get_map(today: dt.date, *, base: str = BASE) -> dict[str, FakeResponse]:
    monday = site_probe.most_recent_published_monday(today)
    date_str = monday.isoformat()
    return {
        f"{base}/": FakeResponse(
            200, text='<html lang="ko"><body>home</body></html>',
            headers={"strict-transport-security": "max-age=63072000",
                     "x-frame-options": "DENY"}),
        f"{base}/en/": FakeResponse(200, text='<html lang="en"><body>home</body></html>'),
        f"{base}/findings/": FakeResponse(200, text="findings app shell"),
        f"{base}/rss.xml": FakeResponse(200, text="<rss></rss>"),
        f"{base}/sitemap.xml": FakeResponse(
            200, text=f"<urlset><url><loc>{base}/briefs/2020-01-01/</loc></url></urlset>"),
        f"{base}/briefs/{date_str}/": FakeResponse(200, text="brief"),
        f"{base}/en/briefs/{date_str}/": FakeResponse(200, text="brief-en"),
    }


def _ok_post_map(*, supabase: str = SUPABASE) -> dict[str, FakeResponse]:
    return {
        f"{supabase}/rest/v1/rpc/fda_inspection_stats": FakeResponse(200, elapsed_s=0.2),
        f"{supabase}/rest/v1/rpc/findings_stats": FakeResponse(200, elapsed_s=0.3),
        f"{supabase}/rest/v1/rpc/findings_similar_to": FakeResponse(200, elapsed_s=0.4),
    }


class MostRecentPublishedMondayTest(unittest.TestCase):
    def test_monday_uses_last_week(self):
        # 2026-03-09 는 월요일. 오늘이 월요일이면 이번 주가 아니라 지난주 월요일을 쓴다.
        today = dt.date(2026, 3, 9)
        self.assertEqual(today.weekday(), 0)
        self.assertEqual(site_probe.most_recent_published_monday(today),
                         dt.date(2026, 3, 2))

    def test_tuesday_uses_this_week(self):
        # 2026-03-10 은 화요일. 이번 주 월요일(1일 전)을 쓴다.
        today = dt.date(2026, 3, 10)
        self.assertEqual(today.weekday(), 1)
        self.assertEqual(site_probe.most_recent_published_monday(today),
                         dt.date(2026, 3, 9))

    def test_sunday_uses_this_week(self):
        # 2026-03-15 는 일요일. 이번 주 월요일(6일 전)을 쓴다.
        today = dt.date(2026, 3, 15)
        self.assertEqual(today.weekday(), 6)
        self.assertEqual(site_probe.most_recent_published_monday(today),
                         dt.date(2026, 3, 9))


class ResolveTodayTest(unittest.TestCase):
    def test_probe_today_env_overrides(self):
        today = site_probe.resolve_today({"PROBE_TODAY": "2026-03-10"})
        self.assertEqual(today, dt.date(2026, 3, 10))

    def test_no_override_uses_kst_now(self):
        fixed = dt.datetime(2026, 3, 10, 1, 0, tzinfo=site_probe.KST)
        with mock.patch("site_probe.dt.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            today = site_probe.resolve_today({})
        self.assertEqual(today, dt.date(2026, 3, 10))


class RpcLatencyThresholdTest(unittest.TestCase):
    """anon statement_timeout 은 3초 — 스펙 문구 그대로 2.5s/3.5s 경계를 못박는다."""

    def test_2_5s_is_warn(self):
        resp = FakeResponse(200, elapsed_s=2.5)
        with mock.patch("site_probe.requests.post", return_value=resp):
            result = site_probe.check_rpc(
                "RPC findings_stats", SUPABASE, "anon-key", "findings_stats", {}, 5.0)
        self.assertEqual(result.status, "warn")

    def test_3_5s_is_fail(self):
        resp = FakeResponse(200, elapsed_s=3.5)
        with mock.patch("site_probe.requests.post", return_value=resp):
            result = site_probe.check_rpc(
                "RPC findings_stats", SUPABASE, "anon-key", "findings_stats", {}, 5.0)
        self.assertEqual(result.status, "fail")

    def test_below_2s_is_ok(self):
        resp = FakeResponse(200, elapsed_s=0.4)
        with mock.patch("site_probe.requests.post", return_value=resp):
            result = site_probe.check_rpc(
                "RPC findings_stats", SUPABASE, "anon-key", "findings_stats", {}, 5.0)
        self.assertEqual(result.status, "ok")

    def test_non_200_is_fail_regardless_of_latency(self):
        resp = FakeResponse(500, elapsed_s=0.1)
        with mock.patch("site_probe.requests.post", return_value=resp):
            result = site_probe.check_rpc(
                "RPC findings_stats", SUPABASE, "anon-key", "findings_stats", {}, 5.0)
        self.assertEqual(result.status, "fail")

    def test_missing_anon_key_is_fail_without_network_call(self):
        with mock.patch("site_probe.requests.post") as post:
            result = site_probe.check_rpc(
                "RPC findings_stats", SUPABASE, "", "findings_stats", {}, 5.0)
        post.assert_not_called()
        self.assertEqual(result.status, "fail")


class CheckHeaderPresentTest(unittest.TestCase):
    def test_present_is_ok(self):
        resp = FakeResponse(200, headers={"Strict-Transport-Security": "max-age=1"})
        result = site_probe.check_header_present(
            "헤더 strict-transport-security", resp, "strict-transport-security")
        self.assertEqual(result.status, "ok")

    def test_absent_is_warn_not_fail(self):
        resp = FakeResponse(200, headers={})
        result = site_probe.check_header_present(
            "헤더 x-frame-options", resp, "x-frame-options")
        self.assertEqual(result.status, "warn")

    def test_no_response_is_warn(self):
        result = site_probe.check_header_present(
            "헤더 x-frame-options", None, "x-frame-options")
        self.assertEqual(result.status, "warn")


class RunProbeAllOkTest(unittest.TestCase):
    def test_all_ok_no_fail_rows(self):
        today = dt.date(2026, 3, 10)
        get_map = _ok_get_map(today)
        post_map = _ok_post_map()
        with mock.patch("site_probe.requests.get", side_effect=_dispatch(get_map)), \
             mock.patch("site_probe.requests.post", side_effect=_dispatch(post_map)):
            report = site_probe.run_probe(
                base_url=BASE, supabase_url=SUPABASE, anon_key="anon-key",
                today=today, timeout=5.0)

        self.assertEqual(report["overall"], "ok")
        statuses = {c["status"] for c in report["checks"]}
        self.assertNotIn("fail", statuses)
        table = site_probe.render_markdown(report)
        self.assertNotIn("`fail`", table)

    def test_main_exits_zero_when_all_ok(self):
        today = dt.date(2026, 3, 10)
        get_map = _ok_get_map(today)
        post_map = _ok_post_map()
        env = {
            "SITE_BASE_URL": BASE,
            "SUPABASE_URL": SUPABASE,
            "SUPABASE_ANON_KEY": "anon-key",
            "PROBE_TODAY": today.isoformat(),
        }
        out_path = os.path.join(
            os.path.dirname(__file__), "_site_probe_test_report.json")
        self.addCleanup(lambda: os.path.exists(out_path) and os.remove(out_path))
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("site_probe.requests.get", side_effect=_dispatch(get_map)), \
             mock.patch("site_probe.requests.post", side_effect=_dispatch(post_map)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = site_probe.main(["--output", out_path])
        self.assertEqual(code, 0)
        self.assertNotIn("`fail`", buf.getvalue())
        self.assertTrue(os.path.exists(out_path))


class RunProbeFindingsDownTest(unittest.TestCase):
    def test_findings_500_is_fail(self):
        today = dt.date(2026, 3, 10)
        get_map = _ok_get_map(today)
        get_map[f"{BASE}/findings/"] = FakeResponse(500, text="Internal Server Error")
        post_map = _ok_post_map()
        with mock.patch("site_probe.requests.get", side_effect=_dispatch(get_map)), \
             mock.patch("site_probe.requests.post", side_effect=_dispatch(post_map)):
            report = site_probe.run_probe(
                base_url=BASE, supabase_url=SUPABASE, anon_key="anon-key",
                today=today, timeout=5.0)

        self.assertEqual(report["overall"], "fail")
        findings_rows = [c for c in report["checks"] if c["name"] == "GET /findings/"]
        self.assertEqual(len(findings_rows), 1)
        self.assertEqual(findings_rows[0]["status"], "fail")


class RunProbeEnglishBriefMissingTest(unittest.TestCase):
    def test_missing_english_brief_is_warn_only(self):
        today = dt.date(2026, 3, 10)
        get_map = _ok_get_map(today)
        monday = site_probe.most_recent_published_monday(today)
        en_url = f"{BASE}/en/briefs/{monday.isoformat()}/"
        get_map[en_url] = FakeResponse(404, text="not found")
        post_map = _ok_post_map()
        with mock.patch("site_probe.requests.get", side_effect=_dispatch(get_map)), \
             mock.patch("site_probe.requests.post", side_effect=_dispatch(post_map)):
            report = site_probe.run_probe(
                base_url=BASE, supabase_url=SUPABASE, anon_key="anon-key",
                today=today, timeout=5.0)

        en_rows = [c for c in report["checks"] if c["name"].startswith("최신 브리프(EN)")]
        self.assertEqual(len(en_rows), 1)
        self.assertEqual(en_rows[0]["status"], "warn")
        # warn 만 있으면 fail 은 아니다 — overall 은 "warn" 으로 승격되지만(관측 가능해야
        # 하므로) exit 코드는 0 이다(아래 exit-code 검증). fail 행은 없어야 한다.
        self.assertEqual(report["overall"], "warn")
        statuses = {c["status"] for c in report["checks"]}
        self.assertNotIn("fail", statuses)
        self.assertEqual(1 if report["overall"] == "fail" else 0, 0)


class SitemapAndRootChecksTest(unittest.TestCase):
    def test_sitemap_missing_briefs_loc_is_fail(self):
        today = dt.date(2026, 3, 10)
        get_map = _ok_get_map(today)
        get_map[f"{BASE}/sitemap.xml"] = FakeResponse(
            200, text="<urlset><url><loc>https://grm-solutions.com/findings/</loc></url></urlset>")
        post_map = _ok_post_map()
        with mock.patch("site_probe.requests.get", side_effect=_dispatch(get_map)), \
             mock.patch("site_probe.requests.post", side_effect=_dispatch(post_map)):
            report = site_probe.run_probe(
                base_url=BASE, supabase_url=SUPABASE, anon_key="anon-key",
                today=today, timeout=5.0)
        sitemap_rows = [c for c in report["checks"] if c["name"] == "GET /sitemap.xml"]
        self.assertEqual(sitemap_rows[0]["status"], "fail")

    def test_root_wrong_lang_attr_is_fail(self):
        today = dt.date(2026, 3, 10)
        get_map = _ok_get_map(today)
        get_map[f"{BASE}/"] = FakeResponse(200, text="<html><body>no lang attr</body></html>")
        post_map = _ok_post_map()
        with mock.patch("site_probe.requests.get", side_effect=_dispatch(get_map)), \
             mock.patch("site_probe.requests.post", side_effect=_dispatch(post_map)):
            report = site_probe.run_probe(
                base_url=BASE, supabase_url=SUPABASE, anon_key="anon-key",
                today=today, timeout=5.0)
        home_rows = [c for c in report["checks"] if c["name"] == "GET / (ko)"]
        self.assertEqual(home_rows[0]["status"], "fail")



class RpcRetryTest(unittest.TestCase):
    """[2026-09-10] RPC 검사는 fail 이면 한 번만 다시 잰다 — 콜드 블립은 통과, 연속 실패만 fail."""

    def _resp(self, status, secs):
        r = mock.Mock()
        r.status_code = status
        r.elapsed = dt.timedelta(seconds=secs)
        r.headers = {}
        r.text = ""
        return r

    def test_transient_500_then_ok_is_ok_with_retry_note(self):
        with mock.patch("site_probe.requests.post", side_effect=[self._resp(500, 0.3), self._resp(200, 0.4)]) as post:
            res = site_probe.check_rpc("rpc", SUPABASE, "anon", "findings_stats", {}, 10.0)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(res.status, "ok")
        self.assertIn("재시도 통과", res.detail)
        self.assertIn("HTTP 500", res.detail)

    def test_slow_then_slow_is_fail(self):
        with mock.patch("site_probe.requests.post", side_effect=[self._resp(200, 3.6), self._resp(200, 3.4)]) as post:
            res = site_probe.check_rpc("rpc", SUPABASE, "anon", "findings_stats", {}, 10.0)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(res.status, "fail")
        self.assertIn("재시도도", res.detail)

    def test_warn_does_not_retry(self):
        with mock.patch("site_probe.requests.post", side_effect=[self._resp(200, 2.5), self._resp(200, 0.1)]) as post:
            res = site_probe.check_rpc("rpc", SUPABASE, "anon", "findings_stats", {}, 10.0)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(res.status, "warn")

if __name__ == "__main__":
    unittest.main()
