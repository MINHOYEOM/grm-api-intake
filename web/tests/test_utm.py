#!/usr/bin/env python3
"""web/utm.py 테스트 — 순수 함수 층(네트워크 0·now() 0).

CI(`unittest discover -s tests`)는 `tests/test_web_utm.py` shim 으로 순회. 직접:
  python web/tests/test_utm.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import utm  # noqa: E402


class WithUtmTest(unittest.TestCase):
    def test_appends_to_bare_url(self):
        out = utm.with_utm("https://grm-solutions.com/briefs/2026-09-07/",
                            "linkedin", "social", "2026-09-07_weekly")
        self.assertEqual(
            out,
            "https://grm-solutions.com/briefs/2026-09-07/"
            "?utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly")

    def test_preserves_existing_query(self):
        out = utm.with_utm("https://example.com/x?a=1&b=2", "newsletter", "email", "brief_2026-09-07")
        self.assertTrue(out.startswith("https://example.com/x?a=1&b=2&utm_source=newsletter"), out)
        self.assertIn("utm_medium=email", out)
        self.assertIn("utm_campaign=brief_2026-09-07", out)

    def test_preserves_fragment(self):
        out = utm.with_utm("https://example.com/x#sec-1", "newsletter", "forward", "brief_2026-09-07")
        self.assertTrue(out.endswith("#sec-1"), out)
        self.assertIn("?utm_source=newsletter", out)

    def test_rejects_at_sign(self):
        with self.assertRaises(ValueError):
            utm.with_utm("https://example.com/", "link@in", "social", "2026-09-07_weekly")

    def test_rejects_spaces(self):
        with self.assertRaises(ValueError):
            utm.with_utm("https://example.com/", "linkedin", "so cial", "2026-09-07_weekly")

    def test_rejects_empty_value(self):
        with self.assertRaises(ValueError):
            utm.with_utm("https://example.com/", "", "social", "2026-09-07_weekly")

    def test_uppercase_is_lowered_not_rejected(self):
        out = utm.with_utm("https://example.com/", "LinkedIn", "Social", "2026-09-07_WEEKLY")
        self.assertIn("utm_source=linkedin", out)
        self.assertIn("utm_medium=social", out)
        self.assertIn("utm_campaign=2026-09-07_weekly", out)

    def test_campaign_helper(self):
        self.assertEqual(utm.linkedin_weekly_campaign("2026-09-07"), "2026-09-07_weekly")

    def test_linkedin_weekly_constant(self):
        self.assertEqual(utm.LINKEDIN_WEEKLY, ("linkedin", "social"))
        out = utm.with_utm("https://example.com/", *utm.LINKEDIN_WEEKLY,
                            utm.linkedin_weekly_campaign("2026-09-07"))
        self.assertIn("utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly", out)


class ChannelDictionaryTest(unittest.TestCase):
    """2026-11-04 — 채널 사전 `CHANNELS` 가 내보내는 링크 표식의 단일 정본이다."""

    def test_every_channel_yields_valid_tags(self):
        for name in utm.CHANNELS:
            out = utm.channel_url("https://grm-solutions.com/x/", name, date="2026-11-02", month="2026-11")
            self.assertIn("utm_source=", out, name)

    def test_known_channels_match_table(self):
        self.assertEqual(
            "https://g.com/?utm_source=newsletter&utm_medium=email&utm_campaign=brief_2026-11-02",
            utm.channel_url("https://g.com/", "newsletter_body", date="2026-11-02"))
        self.assertEqual(
            "https://g.com/?utm_source=welcome&utm_medium=email&utm_campaign=confirm",
            utm.channel_url("https://g.com/", "welcome_confirm"))

    def test_unknown_channel_raises(self):
        with self.assertRaises(KeyError):
            utm.channel_url("https://g.com/", "nope")

    def test_fragment_stays_after_query(self):
        out = utm.channel_url("https://g.com/b/#fb-up", "newsletter_body", date="2026-11-02")
        self.assertTrue(out.endswith("#fb-up"))
        self.assertIn("?utm_source=newsletter", out)


class BareSiteUrlTest(unittest.TestCase):
    def test_tags_bare_domain_paths_only(self):
        out = utm.tag_bare_site_urls(
            "사례 grm-solutions.com/findings/doc/a/ (grm-solutions.com/glossary/) "
            "https://grm-solutions.com/briefs/x/?utm_source=linkedin", "linkedin_weekly", date="2026-11-02")
        q = "?utm_source=linkedin&utm_medium=social&utm_campaign=2026-11-02_weekly"
        self.assertIn(f"https://grm-solutions.com/findings/doc/a/{q} ", out)
        self.assertIn(f"(https://grm-solutions.com/glossary/{q})", out)
        # 이미 스킴이 붙은 주소는 한 번만 — 이중 표식 금지
        self.assertEqual(1, out.count("https://grm-solutions.com/briefs/x/?utm_source=linkedin"))
        self.assertEqual(out.count("utm_source=linkedin"), 3)


class ArrivalChannelWiringTest(unittest.TestCase):
    """104 도착 채널 카운터 — 가산 전용·무PII·클라이언트가 보내는 값의 범위를 고정한다."""

    MIG = (WEB_DIR / "migrations" / "104_arrival_channel_counts.sql").read_text(encoding="utf-8")
    BASE = (WEB_DIR / "templates" / "base.html").read_text(encoding="utf-8")

    def test_migration_is_additive_only(self):
        low = self.MIG.lower()
        for bad in ("drop table", "drop function", "alter table public.funnel", "alter table public.rum_",
                    "create or replace function public.funnel_", "create or replace function public.brief_"):
            self.assertNotIn(bad, low)

    def test_migration_stores_no_identifiers(self):
        cols = self.MIG.split("create table if not exists public.arrival_channel_counts", 1)[1].split(");", 1)[0]
        for col in ("day date", "source text", "medium text", "ref_host text", "total integer"):
            self.assertIn(col, cols)
        for bad in ("campaign", "path", "email", "ip ", "session", "user_agent"):
            self.assertNotIn(bad, cols)

    def test_anon_may_only_bump_and_read_is_authenticated(self):
        self.assertIn("grant execute on function public.arrival_bump(text, text, text) to anon, authenticated", self.MIG)
        self.assertIn("grant select on public.arrival_channel_counts to authenticated", self.MIG)
        self.assertNotIn("grant select on public.arrival_channel_counts to anon", self.MIG)

    def test_client_sends_only_source_medium_refhost_once_per_new_first_touch(self):
        self.assertIn("rpc/arrival_bump", self.BASE)
        self.assertIn("JSON.stringify({p_source:t.s,p_medium:t.m,p_ref_host:t.r})", self.BASE)
        self.assertIn("arrivalBump(touchCapture());", self.BASE)
        # 기존 first touch 가 살아 있으면 null — 재방문은 세지 않는다
        self.assertIn("old.exp>Date.now())return null;", self.BASE)
        # 제출 경로의 087 호출은 그대로
        self.assertIn("p_ref_host:t.r,p_landing_zone:t.z", self.BASE)


if __name__ == "__main__":
    unittest.main()
