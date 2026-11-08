"""성장 일보 v2 · 주간 검색 개선 리포트(135) — 판독 규칙과 마이그레이션 계약.

규칙을 고정하는 이유: 보고는 매일 사람이 아닌 Routine 이 만든다. 아래 규칙이 조용히 풀리면
같은 오보가 돌아온다 — 급증일을 성장으로 읽기, Brevo globalStats 클릭을 사람 수로 읽기,
추정값 표시 누락, 구글 노출 급락을 놓치기.
"""
from __future__ import annotations

import io
import json
import pathlib
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
import growth_daily as gd  # noqa: E402  (web/growth_daily.py)

MIG = WEB_DIR / "migrations" / "135_growth_daily_v2.sql"


def _trend(values, start="2026-10-25", si=1.0):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return [{"date": (d0 + dt.timedelta(days=i)).isoformat(), "visits": v, "page_views": v,
             "sample_interval": si} for i, v in enumerate(values)]


TREND = _trend([23, 72, 78, 86, 47, 64, 12, 4085, 98, 190, 71, 99, 50, 55])


def _payload(**over):
    p = {
        "version": 2, "report_date": "2026-11-07",
        "growth": {
            "report_date": "2026-11-07",
            "day": {"best_visits": 55, "best_visits_sample_interval": 1.0},
            "trend_14d": TREND,
            "funnel": {"day": {"submits": 1}, "this_week_submits": 7},
            "referrers": {"day": [{"group": "direct", "visits": 44}, {"group": "naver", "visits": 11}],
                          "this_week": [{"group": "naver", "visits": 173}, {"group": "google", "visits": 8}]},
            "newsletter": {"latest": {"total": 31, "snap_date": "2026-11-08", "blacklisted": 0},
                           "delta_since_previous": 0, "delta_7d": 4},
            "countries": {"day": [{"country": "KR", "label": "한국", "visits": 40},
                                  {"country": "US", "label": "미국", "visits": 15}]},
            "data_quality": {"rum_latest_date": "2026-11-07", "rum_present_on_date": True},
            "week_compare": {"this_week": {"visits": 4648}, "prev_week": {"visits": 382}},
        },
        "search": {"connected": True, "report_date": "2026-11-04",
                   "day": {"clicks": 0, "impressions": 4},
                   "week_compare": {"this_week": {"clicks": 6, "impressions": 26}},
                   "top_queries": [], "data_quality": {"latest_date": "2026-11-04", "lag_days": 4}},
        "pages": {"day": [{"path": "/", "zone": "홈", "visits": 2}], "week": []},
        "mail": {"latest_weekly": {"name": "GRM Weekly Brief — 2026-11-02 (No.20)", "delivered": 29,
                                   "list_unique_clicks": None, "link_clicks": 8,
                                   "global_unique_clicks": 913, "links_top": [{"url": "/briefs/2026-11-02/", "clicks": 7}],
                                   "fb_up": 0, "fb_down": 0, "sent_at_kst": "2026-11-02 17:46"},
                 "recent": [], "unsubscriptions_14d": 0, "latest_capture_kst": "2026-11-08 05:21"},
    }
    p.update(over)
    return p


class SpikeRuleTest(unittest.TestCase):
    def test_bot_burst_day_is_a_spike_and_normal_days_are_not(self):
        self.assertEqual(gd.spike_dates(TREND), {"2026-11-01"})

    def test_monday_publish_bump_is_not_a_spike(self):
        # 11-03 의 190 은 평소의 2.7배 — 발행 효과일 수 있으니 빼지 않는다.
        self.assertNotIn("2026-11-03", gd.spike_dates(TREND))

    def test_small_sites_need_absolute_floor(self):
        # 중앙값 2인 날들에서 12 는 6배지만 100 미만 — 급증으로 치지 않는다.
        self.assertEqual(gd.spike_dates(_trend([2, 2, 2, 2, 12, 2, 2])), set())

    def test_week_excludes_spike_but_reports_raw_total(self):
        wk = gd.week_visits(TREND, "2026-11-07", gd.spike_dates(TREND))
        self.assertEqual(wk["total"], 4648)
        self.assertEqual(wk["clean"], 4648 - 4085)
        self.assertEqual(wk["excluded"], ["2026-11-01"])
        self.assertEqual(wk["days"], 7)


class ForeignSkewTest(unittest.TestCase):
    def test_flags_single_foreign_country_over_30pct(self):
        c = gd.foreign_skew([{"country": "US", "visits": 37}, {"country": "KR", "visits": 13}], 55)
        self.assertEqual(c["country"], "US")

    def test_korea_majority_is_not_flagged(self):
        self.assertIsNone(gd.foreign_skew([{"country": "KR", "visits": 40}, {"country": "US", "visits": 15}], 55))

    def test_tiny_days_are_not_judged(self):
        self.assertIsNone(gd.foreign_skew([{"country": "US", "visits": 5}], 6))


class MailClickBasisTest(unittest.TestCase):
    """★globalStats 클릭(11-02 호 72 — 수신 29명)은 어떤 경우에도 헤드라인이 되면 안 된다."""

    def test_list_basis_wins(self):
        v, basis = gd.mail_clicks({"list_unique_clicks": 6, "link_clicks": 8, "global_unique_clicks": 72})
        self.assertEqual(v, 6)
        self.assertIn("목록 기준", basis)

    def test_falls_back_to_link_sum_never_global(self):
        v, basis = gd.mail_clicks({"list_unique_clicks": None, "link_clicks": 8, "global_unique_clicks": 72})
        self.assertEqual(v, 8)
        self.assertIn("링크별", basis)

    def test_global_only_means_no_number(self):
        v, _ = gd.mail_clicks({"global_unique_clicks": 72})
        self.assertIsNone(v)

    def test_rendered_daily_never_shows_global_clicks(self):
        md = gd.render_daily(_payload())
        self.assertNotIn("913", md)
        self.assertIn("8 / 수신 29", md)


class PrecisionTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(gd.precision(1.0), "정확")
        self.assertEqual(gd.precision(1.3), "거의 정확")
        self.assertIn("추정", gd.precision(12.5))
        self.assertEqual(gd.precision(None), "추정")

    def test_estimated_days_are_marked_in_trend(self):
        p = _payload()
        p["growth"]["trend_14d"] = _trend([10] * 13 + [80], si=None)
        md = gd.render_daily(p)
        self.assertIn("~80", md)


class RenderDailyTest(unittest.TestCase):
    def test_chain_rows_in_order(self):
        md = gd.render_daily(_payload())
        order = ["| 방문 |", "| 검색으로 들어온 방문 |", "| └ 구글 검색 클릭 |", "| 구독 신청(사이트 폼) |",
                 "| 확정 구독자 |", "| 최근 주간 메일 클릭 |", "| 방문 → 신청 전환 |"]
        pos = [md.index(x) for x in order]
        self.assertEqual(pos, sorted(pos))

    def test_spike_is_excluded_from_week_and_conversion(self):
        md = gd.render_daily(_payload())
        self.assertIn("563 (급증일 2026-11-01 제외 · 포함 시 4,648)", md)
        self.assertIn("1.2%", md)          # 7 ÷ 563
        self.assertIn("⚠급증", md)

    def test_search_visits_count_search_groups_only(self):
        md = gd.render_daily(_payload())
        self.assertIn("| 검색으로 들어온 방문 | 11 | 181 |", md)

    def test_missing_rum_is_not_zero(self):
        p = _payload()
        p["growth"]["data_quality"]["rum_present_on_date"] = False
        self.assertIn("수집되지 않음", gd.render_daily(p))

    def test_card_level_limit_is_stated(self):
        self.assertIn("카드별 클릭은 아직 셀 수 없다", gd.render_daily(_payload()))

    def test_unconnected_search(self):
        p = _payload(search={"connected": False})
        self.assertIn("연동 대기", gd.render_daily(p))


class RenderSearchTest(unittest.TestCase):
    BASE = {"connected": True, "window": {"days": 28, "start": "2026-10-08", "end": "2026-11-04", "min_impressions": 3},
            "totals": {"clicks": 9, "impressions": 217, "pos": 9.2, "prev_clicks": 170, "prev_impressions": 22173},
            "low_ctr_pages": [{"path": "/library/mfds/", "zone": "자료실", "clicks": 0, "impressions": 11, "pos": 6.1}],
            "striking_pages": [], "striking_queries": [],
            "duplicate_paths": [{"path": "/findings/index.html", "impressions": 13, "clicks": 0, "pos": 4.7}],
            "topics": [{"label": "FDA 지적 조항", "query_impressions": 1, "page_impressions": 47, "page_clicks": 1,
                        "queries": [{"query": "21 cfr 211.84"}]}],
            "query_rows": 8, "page_rows": 178, "basis": "b"}

    def test_collapse_alarm_comes_first(self):
        md = gd.render_search(self.BASE)
        self.assertIn("⚠ 구글 노출이 직전 기간의 1.0%", md)
        self.assertLess(md.index("⚠"), md.index("## 1."))

    def test_no_alarm_when_stable(self):
        p = dict(self.BASE, totals=dict(self.BASE["totals"], prev_impressions=250))
        self.assertNotIn("⚠", gd.render_search(p))

    def test_sections(self):
        md = gd.render_search(self.BASE)
        for s in ("`/library/mfds/`", "`/findings/index.html`", "FDA 지적 조항", "21 cfr 211.84"):
            self.assertIn(s, md)

    def test_unconnected(self):
        self.assertIn("연동 대기", gd.render_search({"connected": False}))


class InputShapeTest(unittest.TestCase):
    def test_unwrap_connector_shapes(self):
        p = _payload()
        self.assertEqual(gd.unwrap([{"r": p}])["version"], 2)
        self.assertEqual(gd.unwrap([{"growth_daily_v2": json.dumps(p)}])["version"], 2)
        self.assertEqual(gd.unwrap(p)["version"], 2)

    def test_cli_reads_json_file(self):
        with tempfile.TemporaryDirectory() as td:
            f = pathlib.Path(td) / "g.json"
            f.write_text(json.dumps([{"r": _payload()}], ensure_ascii=False), encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = gd.main(["daily", "--json", str(f)])
        self.assertEqual(rc, 0)
        self.assertIn("GRM 성장 일보 — 2026-11-07(토)", buf.getvalue())

    def test_main_guards_stdout_encoding_first(self):
        src = (WEB_DIR / "growth_daily.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        self.assertLess(body.index("reconfigure"), body.index("argparse.ArgumentParser"))


class MigrationContractTest(unittest.TestCase):
    SQL = MIG.read_text(encoding="utf-8")

    def test_additive_only(self):
        low = self.SQL.lower()
        self.assertNotRegex(low, r"\bdrop\s+(table|function|column)")
        for fn in ("growth_daily_report", "gsc_report", "newsletter_campaigns_report"):
            self.assertNotRegex(low, rf"create\s+or\s+replace\s+function\s+public\.{fn}\s*\(")
        self.assertIn("add column if not exists list_unique_clicks", low)

    def test_report_functions_are_service_role_only(self):
        for sig in ("growth_daily_v2(date)", "gsc_search_opportunities(integer, integer)"):
            self.assertIn(f"revoke all on function public.{sig} from public, anon, authenticated", self.SQL)
            self.assertIn(f"grant execute on function public.{sig} to service_role", self.SQL)

    def test_postgres_word_boundary_is_not_backslash_b(self):
        # Postgres ARE 에서 \b 는 백스페이스다 — 단어 경계는 \y.
        code = "\n".join(ln for ln in self.SQL.splitlines() if not ln.lstrip().startswith("--"))
        self.assertNotIn("\\b", code)
        self.assertIn("\\y", code)

    def test_mail_block_reads_list_basis_and_keeps_global_as_reference(self):
        self.assertIn("'list_unique_clicks', w.list_unique_clicks", self.SQL)
        self.assertIn("'global_unique_clicks', w.unique_clicks", self.SQL)


class RoutineInstructionTest(unittest.TestCase):
    """Routine 지시문이 렌더러와 RPC 를 실제로 부르는지 — 지시문만 바뀌고 코드가 안 쓰이는 표류 방지."""

    def test_daily_instruction_uses_v2_and_renderer(self):
        t = (ROOT / "company" / "growth" / "daily-report.md").read_text(encoding="utf-8")
        self.assertIn("select public.growth_daily_v2()", t)
        self.assertIn("web/growth_daily.py daily", t)

    def test_weekly_instruction_is_search_report(self):
        t = (ROOT / "company" / "growth" / "weekly-report.md").read_text(encoding="utf-8")
        self.assertIn("select public.gsc_search_opportunities()", t)
        self.assertIn("web/growth_daily.py search", t)


if __name__ == "__main__":
    unittest.main()
