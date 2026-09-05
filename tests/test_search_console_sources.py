#!/usr/bin/env python3
"""[078] Search Console 수집기 · 마이그레이션 계약.

Cloudflare RUM 은 "google.com 에서 왔다"까지만 안다. 이 트랙이 채우는 것은 **무엇을
검색해 들어왔는가**이고, 그래서 여기서 고정하는 계약은 네 가지다:

① **속성 주소를 추측하지 않는다** — 도메인 속성과 URL 접두 속성은 다른 데이터를 준다.
   접근 가능한 속성을 열거해 고르고, 못 고르면 무엇이 보였는지를 실패 메시지에 담는다.
② **경로만 저장한다** — 쿼리스트링에 실명이 실리는 경로가 있다(rum_path_daily 와 같은 규칙).
③ **순위 합산은 노출 가중** — 단순 평균은 뜻이 없는 수다.
④ **구역 규칙은 077 과 파리티** — 두 곳에 있는 어휘는 어긋나는 순간 두 보고가 갈린다.
"""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_search_console as gsc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# ★파일명을 번호로 참조하되 **변수 이름은 내용으로** 짓는다 — 마이그 번호는
# 병렬 세션끼리 충돌해 옮겨 다닌다(실제로 076 이 077 로 밀렸다).
MIG_GROWTH = ROOT / "web" / "migrations" / "077_growth_daily_report.sql"
MIG_GSC = ROOT / "web" / "migrations" / "078_search_console.sql"
WORKFLOW = ROOT / ".github" / "workflows" / "grm-rum-analytics.yml"


def _row(keys, clicks, impressions, position):
    return {"keys": list(keys), "clicks": clicks, "impressions": impressions,
            "ctr": (clicks / impressions if impressions else 0), "position": position}


class SitePickTest(unittest.TestCase):
    def test_domain_property_wins_over_url_prefix(self):
        """도메인 속성이 www·http 변형을 모두 포함하므로 더 온전하다."""
        entries = [{"siteUrl": "https://grm-solutions.com/", "permissionLevel": "siteOwner"},
                   {"siteUrl": "sc-domain:grm-solutions.com", "permissionLevel": "siteOwner"}]
        self.assertEqual(gsc.pick_site(entries, "grm-solutions.com"), "sc-domain:grm-solutions.com")

    def test_url_prefix_is_used_when_no_domain_property(self):
        entries = [{"siteUrl": "https://grm-solutions.com/", "permissionLevel": "siteFullUser"}]
        self.assertEqual(gsc.pick_site(entries, "grm-solutions.com"), "https://grm-solutions.com/")

    def test_www_host_still_matches_domain_property(self):
        entries = [{"siteUrl": "sc-domain:grm-solutions.com"}]
        self.assertEqual(gsc.pick_site(entries, "www.grm-solutions.com"), "sc-domain:grm-solutions.com")

    def test_no_match_fails_loudly_and_names_what_was_visible(self):
        """★이 메시지가 '서비스 계정을 속성에 추가했는가'라는 유일한 질문의 답이다."""
        with self.assertRaises(SystemExit) as ctx:
            gsc.pick_site([{"siteUrl": "sc-domain:example.com"}], "grm-solutions.com")
        self.assertIn("sc-domain:example.com", str(ctx.exception))
        self.assertIn("사용자 및 권한", str(ctx.exception))

    def test_empty_property_list_is_not_silently_ok(self):
        with self.assertRaises(SystemExit):
            gsc.pick_site([], "grm-solutions.com")


class PageAndParseTest(unittest.TestCase):
    def test_clean_page_keeps_path_only(self):
        cases = {
            "https://grm-solutions.com/glossary/gmp/": "/glossary/gmp/",
            "https://grm-solutions.com/findings/inspector/?key=홍길동": "/findings/inspector/",
            "https://grm-solutions.com": "/",
            "https://grm-solutions.com/": "/",
            "/already/a/path/": "/already/a/path/",
            "": "/",
        }
        for raw, want in cases.items():
            self.assertEqual(gsc.clean_page(raw), want, raw)

    def test_totals_are_kept_per_date(self):
        rows = [_row(["2026-09-01"], 7, 210, 11.44), _row(["2026-09-02"], 5, 180, 12.06)]
        out = gsc.parse_totals(rows)
        self.assertEqual(out["2026-09-01"]["clicks"], 7)
        self.assertEqual(out["2026-09-01"]["impressions"], 210)
        self.assertEqual(out["2026-09-01"]["avg_position"], 11.44)
        self.assertEqual(out["2026-09-01"]["snap_date"], "2026-09-01")

    def test_merged_rows_use_impression_weighted_position(self):
        """정규화로 두 URL 이 같은 경로가 되면 순위는 **노출 가중** 평균이라야 한다.

        단순 평균이면 노출 10 짜리와 990 짜리가 같은 무게를 갖는다 — 뜻이 없는 수다.
        """
        rows = [_row(["2026-09-01", "https://grm-solutions.com/g/?a=1"], 1, 10, 20.0),
                _row(["2026-09-01", "https://grm-solutions.com/g/?a=2"], 9, 990, 2.0)]
        out = gsc.parse_keyed(rows, gsc.clean_page)
        merged = out[("2026-09-01", "/g/")]
        self.assertEqual(merged["clicks"], 10)
        self.assertEqual(merged["impressions"], 1000)
        # (20*10 + 2*990) / 1000 = 2.18 — 단순 평균이면 11.0 이 나온다.
        self.assertEqual(merged["avg_position"], 2.18)

    def test_cap_keeps_top_by_impressions_and_is_deterministic(self):
        keyed = {("2026-09-01", f"q{i:02d}"): {"clicks": 0, "impressions": 100 - i,
                                               "avg_position": 5.0} for i in range(10)}
        rows = gsc.cap_by_day(keyed, 3, "query")
        self.assertEqual([r["query"] for r in rows], ["q00", "q01", "q02"])
        self.assertEqual(rows, gsc.cap_by_day(keyed, 3, "query"), "같은 입력이 같은 순서를 안 낸다")

    def test_rows_missing_a_dimension_are_skipped_not_guessed(self):
        out = gsc.parse_keyed([_row(["2026-09-01"], 1, 1, 1.0)], gsc.clean_page)
        self.assertEqual(out, {})

    def test_window_is_wide_enough_for_the_confirmation_lag(self):
        """확정 데이터가 2~3일 늦으므로 하루치만 받으면 매일 빈손이 된다."""
        import datetime as dt
        start, end = gsc.default_window(today=dt.date(2026, 9, 5))
        self.assertEqual(end, "2026-09-05")
        self.assertEqual(start, "2026-08-20")
        self.assertGreaterEqual(gsc.DEFAULT_DAYS, 7)


class CredentialTest(unittest.TestCase):
    def test_non_service_account_json_fails_with_a_useful_message(self):
        with self.assertRaises(SystemExit) as ctx:
            gsc.parse_service_account('{"type":"authorized_user","client_id":"x"}')
        self.assertIn("서비스 계정 키가 아니다", str(ctx.exception))

    def test_broken_json_fails_loudly(self):
        with self.assertRaises(SystemExit):
            gsc.parse_service_account("not json at all")

    def test_no_secret_is_a_clean_skip(self):
        with patch.dict(os.environ, {"GSC_SERVICE_ACCOUNT_JSON": ""}):
            self.assertEqual(gsc.main([]), 0)

    def test_query_values_never_reach_the_log(self):
        """★저장소가 PUBLIC 이라 Actions 로그가 공개다. 검색어는 값이 아니라 개수만 찍는다."""
        src = (ROOT / "collect_search_console.py").read_text(encoding="utf-8")
        body = src.split("def main(", 1)[1]
        for banned in ("print(queries", "print(query_rows", "print(totals",
                       "json.dumps(rows", "print(pages"):
            self.assertNotIn(banned, body, banned)
        # probe 는 응답의 **필드 이름**만 찍어야 한다. 행의 keys 값(차원 값)을
        # 꺼내는 순간 검색어가 공개 로그로 나간다.
        probe = src.split("if args.probe:", 1)[1].split("return 0", 1)[0]
        for banned in ('["keys"]', '.get("keys")', "['keys']"):
            self.assertNotIn(banned, probe, f"probe 가 행의 차원 값을 꺼낸다: {banned}")
        self.assertIn(".keys()", probe, "probe 가 필드 이름조차 안 찍으면 구조 검증이 안 된다")


class Migration078ContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mig = MIG_GSC.read_text(encoding="utf-8")

    def test_tables_follow_the_072_read_rules(self):
        for table in ("gsc_daily", "gsc_query_daily", "gsc_page_daily"):
            self.assertIn(f"create table if not exists public.{table}", self.mig, table)
            self.assertIn(f"alter table public.{table} enable row level security", self.mig, table)
            grants = re.findall(rf"grant\s+\w+\s+on public\.{table} to ([a-z_]+)", self.mig)
            self.assertEqual(grants, ["authenticated"], f"{table} 에 다른 역할 grant: {grants}")

    def test_report_function_is_operator_only(self):
        fn = self.mig.split("create or replace function public.gsc_report", 1)[1]
        self.assertIn("security definer", fn)
        self.assertIn("set search_path = public, pg_temp", fn)
        self.assertIn("revoke all on function public.gsc_report(date) from public, anon, authenticated", fn)
        self.assertIn("grant execute on function public.gsc_report(date) to service_role", fn)

    def test_ctr_is_derived_not_stored(self):
        """저장하면 합산할 때 '평균의 평균'이라는 틀린 수가 만들어진다."""
        # ★주석을 먼저 걷어낸다 — 안 그러면 "ctr 은 저장하지 않는다"는 설명 문장이
        # 스스로 검사에 걸린다(실제로 걸렸다).
        ddl = self.mig.split("create or replace function", 1)[0]
        code = "\n".join(l for l in ddl.splitlines() if not l.strip().startswith("--"))
        self.assertNotIn("ctr", code.lower())

    def test_position_is_never_a_bare_column_name(self):
        """`position` 은 PostgreSQL 내장 함수 이름 — 열 이름으로 쓰면 파싱이 문맥에 갈린다."""
        ddl = self.mig.split("create or replace function", 1)[0]
        self.assertNotRegex(ddl, r"^\s+position\s+numeric", "열 이름이 position 이다")
        self.assertIn("avg_position numeric", ddl)

    def test_caps_live_inside_the_aggregate_subquery(self):
        """★상한을 집계 바깥에 걸면 결과가 한 행뿐이라 아무것도 자르지 못한다(실제로 한 번 그렇게 썼다)."""
        fn = self.mig.split("create or replace function public.gsc_report", 1)[1]
        for block in fn.split("into v_")[1:]:
            head = block.split(";", 1)[0]
            if "limit " not in head:
                continue
            self.assertNotRegex(head, r"jsonb_agg[^;]*\)\s*\n?\s*$",
                                "집계 바깥 limit 의심")
            # limit 은 반드시 서브쿼리(`from (select ...)`) 안에 있어야 한다.
            self.assertIn("from (", head, "limit 이 있는데 서브쿼리가 없다")

    def test_absent_data_is_not_reported_as_zero(self):
        """부재의 어휘 — 행이 없으면 '검색 유입 0' 이 아니라 '아직 연결 안 됨' 이다."""
        fn = self.mig.split("create or replace function public.gsc_report", 1)[1]
        self.assertIn("'connected', false", fn)
        self.assertIn("첫 수집 대기", fn)

    def test_lag_and_anonymization_are_forced_into_the_report(self):
        """둘 다 모르면 보고가 틀린다 — 함수가 문구를 함께 낸다."""
        fn = self.mig.split("create or replace function public.gsc_report", 1)[1]
        self.assertIn("'lag_basis'", fn)
        self.assertIn("'anonymization_basis'", fn)
        self.assertIn("'query_clicks_7d'", fn)
        self.assertIn("'total_clicks_7d'", fn)

    def test_weekly_position_is_impression_weighted(self):
        fn = self.mig.split("create or replace function public.gsc_report", 1)[1]
        self.assertIn("sum(avg_position * impressions)", fn)
        self.assertNotIn("avg(avg_position)", fn)


class ZoneRuleSingleSourceTest(unittest.TestCase):
    """★구역 규칙은 **정의가 하나뿐**이어야 한다 — 077 의 `grm_zone_of`.

    처음엔 077(착지 표)과 078(검색 페이지 표)에 같은 CASE 를 복제해 두고 "파리티
    테스트로 어긋남을 잡겠다"고 했다. 그건 사본을 두 개 두고 감시하겠다는 뜻이고,
    같은 사이트를 두 보고가 다른 구역 이름으로 부를 여지를 남긴다. **사본을 감시하는
    대신 사본을 없앴다** — 손목록이 낡는 계열과 같은 교훈이라, 검사도 "두 목록이 같은가"
    가 아니라 "목록이 하나인가"를 묻는다.
    """

    MIGRATIONS = sorted((ROOT / "web" / "migrations").glob("*.sql"))

    def test_exactly_one_definition_across_all_migrations(self):
        defs = [m.name for m in self.MIGRATIONS
                if "create or replace function public.grm_zone_of" in m.read_text(encoding="utf-8")]
        self.assertEqual(defs, ["077_growth_daily_report.sql"],
                         f"grm_zone_of 정의가 하나가 아니다: {defs}")

    def test_both_reports_call_the_shared_function(self):
        """정의가 하나여도 한쪽이 자기 CASE 를 다시 쓰면 소용없다."""
        for mig in (MIG_GROWTH, MIG_GSC):
            sql = mig.read_text(encoding="utf-8")
            self.assertIn("public.grm_zone_of(", sql, mig.name)
            # 호출부 아래에 구역 라벨을 직접 적은 CASE 가 남아 있으면 안 된다.
            body = sql.split("create or replace function public.grm_zone_of", 1)[-1]
            body = body.split("$$;", 1)[-1] if mig is MIG_GROWTH else body
            self.assertNotIn("then '자료실'", body,
                             f"{mig.name} 에 구역 CASE 사본이 남아 있다")

    def test_specific_rules_come_before_the_general_one(self):
        """순서가 판정이다 — 일반 규칙이 앞서면 하위 구역이 통째로 삼켜진다."""
        fn = MIG_GROWTH.read_text(encoding="utf-8")             .split("create or replace function public.grm_zone_of", 1)[1].split("$$;", 1)[0]
        order = re.findall(r"when base ~ '(\^/[^']+)' then", fn)
        self.assertGreater(len(order), 10, "구역 규칙이 너무 적다 — 슬라이스가 빗나갔다")
        general = order.index("^/findings/")
        for specific in ("^/findings/firm/", "^/findings/inspector/", "^/findings/docs?/",
                         "^/findings/trends/", "^/findings/clause/"):
            self.assertLess(order.index(specific), general, specific)

    def test_english_tree_folds_into_the_same_zone(self):
        """`/en/glossary/x` 는 용어사전이다 — 언어별로 구역이 갈리면 비교가 안 된다."""
        fn = MIG_GROWTH.read_text(encoding="utf-8")             .split("create or replace function public.grm_zone_of", 1)[1].split("$$;", 1)[0]
        self.assertIn("'^/en(?=/|$)'", fn)


class WorkflowWiringTest(unittest.TestCase):
    def test_search_console_step_is_wired_with_its_own_secret(self):
        wf = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("collect_search_console.py", wf)
        self.assertIn("secrets.GSC_SERVICE_ACCOUNT_JSON", wf)
        # probe 경로가 있어야 첫 배선을 값 없이 검증할 수 있다.
        step = wf.split("collect_search_console.py --probe", 1)
        self.assertEqual(len(step), 2, "probe 경로가 없다")
        self.assertNotRegex(wf, r"\$\{\{[^}]*[0-9]\s*[-+*/]\s*[0-9][^}]*\}\}")

    def test_google_auth_is_declared(self):
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(req, r"(?m)^google-auth[><=]", "google-auth 가 requirements 에 없다")


if __name__ == "__main__":
    unittest.main()
