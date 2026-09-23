#!/usr/bin/env python3
"""collect_newsletter_campaigns.py(090 적재·N-05) 테스트 — 네트워크 0(순수 파싱)
+ main() 은 fetch/upsert 함수를 monkeypatch 로 갈아 끼워 검증한다(collect_newsletter_
subscribers.py 자매 스크립트에는 아직 전용 테스트가 없어, 이 파일이 그 자리의 첫 사례다 —
CLI idiom·클린 skip 계약은 그 스크립트를 그대로 읽고 mirrored).

직접: python tests/test_newsletter_campaigns.py
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

import collect_newsletter_campaigns as cnc  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "grm-rum-analytics.yml"


# ── 순수 파싱 — 이름→kind/publish_date ───────────────────────────────────────
class ClassifyNameTest(unittest.TestCase):
    def test_weekly_name_parsed(self):
        kind, pub = cnc.classify_name("GRM Weekly Brief — 2026-06-26 (No.2)")
        self.assertEqual(kind, "weekly")
        self.assertEqual(pub, "2026-06-26")

    def test_weekly_name_double_digit_issue(self):
        kind, pub = cnc.classify_name("GRM Weekly Brief — 2026-09-21 (No.13)")
        self.assertEqual((kind, pub), ("weekly", "2026-09-21"))

    def test_announce_name_prefix(self):
        kind, pub = cnc.classify_name("GRM Update — glossary-figures-launch")
        self.assertEqual(kind, "announce")
        self.assertIsNone(pub)

    def test_other_fallback_for_test_send(self):
        # [TEST] 접미(newsletter.py --mode test)는 weekly 정규식과 어긋나 other 로 접힌다.
        kind, pub = cnc.classify_name("GRM Weekly Brief — 2026-06-26 (No.2) [TEST]")
        self.assertEqual(kind, "other")
        self.assertIsNone(pub)

    def test_other_for_unrelated_name(self):
        self.assertEqual(cnc.classify_name("Random Campaign"), ("other", None))

    def test_other_for_empty_or_none(self):
        self.assertEqual(cnc.classify_name(""), ("other", None))
        self.assertEqual(cnc.classify_name(None), ("other", None))


# ── 순수 파싱 — globalStats 방어적 추출(여러 응답 모양) ─────────────────────────
class GlobalStatsExtractionTest(unittest.TestCase):
    def test_list_level_statistics_globalstats(self):
        camp = {"statistics": {"globalStats": {
            "sent": 500, "delivered": 490, "uniqueViews": 120, "uniqueClicks": 40,
            "clickers": 38, "unsubscriptions": 2, "hardBounces": 3, "softBounces": 7}}}
        stats = cnc.extract_global_stats(camp)
        self.assertEqual(stats, {
            "sent": 500, "delivered": 490, "unique_views": 120, "unique_clicks": 40,
            "clickers": 38, "unsubscriptions": 2, "hard_bounces": 3, "soft_bounces": 7})

    def test_top_level_globalstats_fallback(self):
        camp = {"globalStats": {"sent": 100, "delivered": 95}}
        stats = cnc.extract_global_stats(camp)
        self.assertEqual(stats["sent"], 100)
        self.assertEqual(stats["delivered"], 95)
        self.assertIsNone(stats["clickers"])          # 없는 키는 0 이 아니라 None

    def test_missing_stats_are_all_none_not_zero(self):
        stats = cnc.extract_global_stats({"id": 7, "name": "x"})
        self.assertTrue(all(v is None for v in stats.values()))
        self.assertFalse(cnc.has_any_global_stats(stats))

    def test_has_any_global_stats_true_when_one_present(self):
        stats = cnc.extract_global_stats({"globalStats": {"sent": 0}})
        self.assertTrue(cnc.has_any_global_stats(stats))   # 0 은 값이 있는 것(None 과 다름)


# ── 순수 파싱 — linksStats(1클릭 피드백 판독의 유일한 원천) ─────────────────────
class LinksStatsExtractionTest(unittest.TestCase):
    def test_top_level_linksstats_dict(self):
        camp = {"linksStats": {
            "https://grm.example/briefs/2026-06-26/#fb-up": 12,
            "https://grm.example/briefs/2026-06-26/#fb-down": 3}}
        links = cnc.extract_links(camp)
        self.assertEqual(links["https://grm.example/briefs/2026-06-26/#fb-up"], 12)
        self.assertEqual(links["https://grm.example/briefs/2026-06-26/#fb-down"], 3)

    def test_nested_statistics_linksstats(self):
        camp = {"statistics": {"linksStats": {"https://grm.example/x": 5}}}
        self.assertEqual(cnc.extract_links(camp), {"https://grm.example/x": 5})

    def test_non_dict_shapes_yield_empty(self):
        for camp in ({"linksStats": ["not", "a", "dict"]}, {"linksStats": None}, {}):
            self.assertEqual(cnc.extract_links(camp), {})

    def test_non_numeric_click_counts_are_dropped(self):
        camp = {"linksStats": {"https://grm.example/x": "n/a", "https://grm.example/y": 4}}
        self.assertEqual(cnc.extract_links(camp), {"https://grm.example/y": 4})


# ── 순수 파싱 — 60일 창 ────────────────────────────────────────────────────────
class WithinWindowTest(unittest.TestCase):
    NOW = dt.datetime(2026, 9, 23, 0, 0, tzinfo=dt.timezone.utc)

    def test_recent_within_window(self):
        self.assertTrue(cnc.within_window("2026-09-01T09:00:00Z", now=self.NOW))

    def test_older_than_window_excluded(self):
        self.assertFalse(cnc.within_window("2026-07-01T09:00:00Z", now=self.NOW))

    def test_exactly_at_boundary_included(self):
        boundary = (self.NOW - dt.timedelta(days=cnc.WINDOW_DAYS)).isoformat()
        self.assertTrue(cnc.within_window(boundary, now=self.NOW))

    def test_missing_sent_at_excluded(self):
        self.assertFalse(cnc.within_window(None, now=self.NOW))
        self.assertFalse(cnc.within_window("", now=self.NOW))

    def test_unparseable_sent_at_excluded(self):
        self.assertFalse(cnc.within_window("not-a-date", now=self.NOW))

    def test_naive_datetime_treated_as_utc(self):
        self.assertTrue(cnc.within_window("2026-09-01T09:00:00", now=self.NOW))

    def test_small_future_skew_tolerated_but_far_future_excluded(self):
        near = (self.NOW + dt.timedelta(hours=6)).isoformat()
        far = (self.NOW + dt.timedelta(days=10)).isoformat()
        self.assertTrue(cnc.within_window(near, now=self.NOW))
        self.assertFalse(cnc.within_window(far, now=self.NOW))


# ── 적재 행 조립(no PII) ───────────────────────────────────────────────────────
class BuildRowTest(unittest.TestCase):
    def test_row_shape_weekly(self):
        row = cnc.build_row(
            campaign_id=42, name="GRM Weekly Brief — 2026-06-26 (No.2)",
            sent_at="2026-06-26T09:00:00Z",
            stats={"sent": 500, "delivered": 490, "unique_views": 120, "unique_clicks": 40,
                   "clickers": 38, "unsubscriptions": 2, "hard_bounces": 3, "soft_bounces": 7},
            links={"https://grm.example/briefs/2026-06-26/#fb-up": 12},
            captured_at="2026-06-27T01:30:00+09:00")
        self.assertEqual(row["campaign_id"], 42)
        self.assertIsInstance(row["campaign_id"], int)
        self.assertEqual(row["kind"], "weekly")
        self.assertEqual(row["publish_date"], "2026-06-26")
        self.assertEqual(row["links"], {"https://grm.example/briefs/2026-06-26/#fb-up": 12})
        self.assertEqual(row["clickers"], 38)
        self.assertEqual(row["captured_at"], "2026-06-27T01:30:00+09:00")

    def test_row_links_default_empty_dict_not_none(self):
        row = cnc.build_row(campaign_id=1, name="x", sent_at=None, stats={}, links={},
                            captured_at="2026-01-01T00:00:00+00:00")
        self.assertEqual(row["links"], {})
        self.assertIsNotNone(row["links"])       # 컬럼이 not null default '{}'

    def test_row_never_carries_an_email_address(self):
        row = cnc.build_row(campaign_id=1, name="GRM Weekly Brief — 2026-06-26 (No.2)",
                            sent_at="2026-06-26T09:00:00Z", stats={}, links={},
                            captured_at="2026-06-27T01:30:00+09:00")
        blob = str(row)
        self.assertNotIn("@", blob)


# ── main() — fetch/upsert 를 monkeypatch 로 갈아 끼워 배선 검증(네트워크 0) ─────
class MainCleanSkipTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_missing_api_key_is_clean_skip(self):
        os.environ.pop("NEWSLETTER_API_KEY", None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cnc.main([])
        self.assertEqual(rc, 0)
        self.assertIn("NEWSLETTER_API_KEY", buf.getvalue())

    def test_missing_supabase_creds_is_clean_skip(self):
        os.environ["NEWSLETTER_API_KEY"] = "k"
        os.environ.pop("SUPABASE_URL", None)
        os.environ.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        called = {"n": 0}
        orig = cnc.fetch_campaign_list
        cnc.fetch_campaign_list = lambda *a, **kw: called.__setitem__("n", called["n"] + 1) or []
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cnc.main(["--supabase-url", "", "--service-role-key", ""])
        finally:
            cnc.fetch_campaign_list = orig
        self.assertEqual(rc, 0)
        self.assertIn("SUPABASE", buf.getvalue())
        self.assertEqual(called["n"], 0, "크리덴셜 확인 전 Brevo 를 먼저 불렀다")


class MainDryRunTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["NEWSLETTER_API_KEY"] = "k"
        self._orig_list = cnc.fetch_campaign_list
        self._orig_detail = cnc.fetch_campaign_detail
        self._orig_upsert = cnc.upsert_rows

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        cnc.fetch_campaign_list = self._orig_list
        cnc.fetch_campaign_detail = self._orig_detail
        cnc.upsert_rows = self._orig_upsert

    def test_dry_run_prints_structure_only_and_writes_nothing(self):
        fixture = [
            {"id": 1, "name": "GRM Weekly Brief — 2026-09-14 (No.10)",
             "sentDate": "2026-09-14T09:00:00Z",
             "statistics": {"globalStats": {"sent": 9999, "delivered": 9990, "clickers": 1234}}},
            {"id": 2, "name": "GRM Update — figures", "sentDate": "2026-09-10T09:00:00Z",
             "statistics": {"globalStats": {"sent": 500}}},
        ]
        cnc.fetch_campaign_list = lambda *a, **kw: fixture
        detail_calls = {"n": 0}

        def _boom_detail(*a, **kw):
            detail_calls["n"] += 1
            raise AssertionError("dry-run 은 캠페인 상세(linksStats)를 부르면 안 된다")
        cnc.fetch_campaign_detail = _boom_detail

        def _boom_upsert(*a, **kw):
            raise AssertionError("dry-run 은 적재하면 안 된다")
        cnc.upsert_rows = _boom_upsert

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cnc.main(["--dry-run"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertEqual(detail_calls["n"], 0)
        self.assertIn("2개", out)
        self.assertIn("weekly 1건", out)
        self.assertIn("announce 1건", out)
        # 구조만 — 캠페인 수치(9999·9990·1234)·이름은 로그에 찍히지 않는다(PUBLIC 저장소).
        for leaked in ("9999", "9990", "1234", "GRM Weekly Brief", "GRM Update"):
            self.assertNotIn(leaked, out, f"수치/캠페인명이 로그에 샜다: {leaked}")


class MainFullRunTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["NEWSLETTER_API_KEY"] = "k"
        self._orig_list = cnc.fetch_campaign_list
        self._orig_detail = cnc.fetch_campaign_detail
        self._orig_upsert = cnc.upsert_rows

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        cnc.fetch_campaign_list = self._orig_list
        cnc.fetch_campaign_detail = self._orig_detail
        cnc.upsert_rows = self._orig_upsert

    def test_full_run_upserts_windowed_rows_with_links_and_skips_stale(self):
        now_str = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT09:00:00Z")
        stale = "2020-01-01T09:00:00Z"
        fixture = [
            {"id": 10, "name": "GRM Weekly Brief — 2026-06-26 (No.2)", "sentDate": now_str,
             "statistics": {"globalStats": {"sent": 500, "delivered": 490}}},
            {"id": 11, "name": "GRM Weekly Brief — 2020-01-01 (No.1)", "sentDate": stale,
             "statistics": {"globalStats": {"sent": 10}}},
        ]
        cnc.fetch_campaign_list = lambda *a, **kw: fixture

        def _detail(api_key, campaign_id, *, statistics, timeout=30.0):
            self.assertEqual(campaign_id, 10, "창 밖 캠페인(11)의 상세를 부르면 안 된다")
            self.assertEqual(statistics, "linksStats")
            return {"linksStats": {"https://grm.example/briefs/2026-06-26/#fb-up": 5,
                                   "https://grm.example/briefs/2026-06-26/#fb-down": 1}}
        cnc.fetch_campaign_detail = _detail

        captured = {}

        def _upsert(url, key, rows, **kw):
            captured["url"], captured["key"], captured["rows"] = url, key, rows
        cnc.upsert_rows = _upsert

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cnc.main(["--supabase-url", "https://proj.supabase.co",
                          "--service-role-key", "srk"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["url"], "https://proj.supabase.co")
        self.assertEqual(captured["key"], "srk")
        rows = captured["rows"]
        self.assertEqual(len(rows), 1, "창 밖 캠페인이 섞여 들어갔다")
        row = rows[0]
        self.assertEqual(row["campaign_id"], 10)
        self.assertEqual(row["kind"], "weekly")
        self.assertEqual(row["publish_date"], "2026-06-26")
        self.assertEqual(row["links"]["https://grm.example/briefs/2026-06-26/#fb-up"], 5)
        self.assertEqual(row["links"]["https://grm.example/briefs/2026-06-26/#fb-down"], 1)
        self.assertIn("1행 적재", buf.getvalue())

    def test_link_detail_failure_does_not_abort_the_run(self):
        now_str = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT09:00:00Z")
        cnc.fetch_campaign_list = lambda *a, **kw: [
            {"id": 20, "name": "GRM Weekly Brief — 2026-06-26 (No.2)", "sentDate": now_str,
             "statistics": {"globalStats": {"sent": 1}}}]

        def _boom(*a, **kw):
            raise RuntimeError("network down")
        cnc.fetch_campaign_detail = _boom

        captured = {}
        cnc.upsert_rows = lambda url, key, rows, **kw: captured.setdefault("rows", rows)

        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cnc.main(["--supabase-url", "https://proj.supabase.co",
                          "--service-role-key", "srk"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["rows"][0]["links"], {})
        self.assertIn("RuntimeError", err.getvalue())


class MainHttpErrorTest(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["NEWSLETTER_API_KEY"] = "k"
        os.environ["SUPABASE_URL"] = "https://proj.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "srk"
        self._orig_list = cnc.fetch_campaign_list

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        cnc.fetch_campaign_list = self._orig_list

    def test_list_fetch_http_error_exits_1_with_status_only(self):
        class FakeResp:
            status_code = 503

        class FakeHTTPError(Exception):
            response = FakeResp()

        def _boom(*a, **kw):
            raise FakeHTTPError("<html>secret body</html>")
        cnc.fetch_campaign_list = _boom

        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = cnc.main([])
        self.assertEqual(rc, 1)
        self.assertIn("503", err.getvalue())
        self.assertNotIn("secret body", err.getvalue())


# ── 워크플로 배선(grm-rum-analytics.yml) ───────────────────────────────────────
class WorkflowWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_step_present_after_subscriber_snapshot(self):
        i_sub = self.text.index("Snapshot newsletter subscribers")
        i_camp = self.text.index("collect_newsletter_campaigns.py")
        self.assertLess(i_sub, i_camp, "캠페인 스텝이 구독자 스냅샷보다 앞에 있다")

    def test_step_run_command(self):
        self.assertIn("run: python collect_newsletter_campaigns.py", self.text)

    def test_step_env_vars(self):
        block = self.text[self.text.index("collect_newsletter_campaigns.py") - 800:
                          self.text.index("collect_newsletter_campaigns.py") + 40]
        for needle in ("NEWSLETTER_API_KEY:", "SUPABASE_URL:", "SUPABASE_SERVICE_ROLE_KEY:"):
            self.assertIn(needle, block, f"env 누락: {needle}")

    def test_step_condition_matches_sibling_style(self):
        block = self.text[self.text.index("collect_newsletter_campaigns.py") - 800:
                          self.text.index("collect_newsletter_campaigns.py") + 40]
        self.assertIn(
            "if: ${{ !cancelled() && inputs.cross_only != 'true' "
            "&& (inputs.probe || 'false') != 'true' }}", block)

    def test_header_comment_mentions_090(self):
        self.assertIn("090", self.text.split("on:")[0], "머리글 주석에 [090] 설명 없음")


if __name__ == "__main__":
    unittest.main()
