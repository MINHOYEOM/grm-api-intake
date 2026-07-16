#!/usr/bin/env python3
"""FIND-1 026(026_findings_search.sql) 서버 canonical search 마이그레이션 tests.

Offline source-text checks only -- no network, no real Postgres connection.
Mirrors the style of test_findings_similar_lexical.py (018/021/022 supersede
케이스)의 정본 패턴을 따른다: comment 를 제거한 code 위에서 문자열/구조 계약을
고정하고, 필요하면 함수 정의를 부분 슬라이스해 스코프를 좁힌다.

026 은 025 §⑤ 이월 항목(검색/필터/정렬/페이지네이션/파셋의 서버 정본화)의 이행이다.
파일 자체 헤더 주석("★security invoker", "★검색 semantics", "정렬 결정론", "한글 정렬")
이 이 파일이 고정하는 계약의 "왜"를 이미 상세히 서술하므로, 각 테스트는 그 이유를
요약한 한국어 독스트링만 붙인다.

이 파일이 고정하는 불가침 계약:
  ①findings_search/findings_document 모두 security invoker + search_path 고정
    -- definer 로 뒤집히면 RLS 우회, 6중복 게이트 부활(파일 헤더 "★security invoker").
  ②함수 본문이 공개 게이트 술어(scope_status/finding_text_ko<>'')를 복제하지 않음
    -- RLS(010)가 유일한 게이트여야 한다.
  ③검색은 ILIKE 부분일치, FTS 미사용 -- `무균` 이 `무균실`/`무균의` 를 놓치는 조용한
    검색 축소 방지(파일 헤더 "★검색 semantics").
  ④LIKE 와일드카드(%·_·\\) 이스케이프 -- indexOf 리터럴 취급과 semantics 일치.
  ⑤검색 blob 컬럼 14종 고정 -- "무엇이 검색되는가"를 조용히 넓히거나 좁히지 않는다.
  ⑥정렬 결정론(d.tie 최종 타이브레이크) + 허용 정렬 3종 고정 -- 022 fp16 동률 결함 재발 방지.
  ⑦firm_asc 에 ko-KR-x-icu collate -- DB 기본 collate(en_US.UTF-8)와 클라이언트
    localeCompare 불일치 방지.
  ⑧입력 클램프(p_page/p_docs_per_page/p_sort) -- 클라이언트 불신 원칙.
  ⑨grant execute to anon, authenticated 양쪽 함수 -- 미부여 시 사이트 백지.
  ⑩nested-loop 회피 형태(any (array(select …)) -- any (select array_agg(…)) 는
    타입 에러(dry-run 실측)이자 O(n×m) planner 회피 실패.
  ⑪searched CTE select * 금지 -- 초안이 select *(width=753)로 temp 스필(9,773) 실측.
  ⑫파일 번호 연속성(001~026, 결번 없음).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_SEARCH_PATH = _MIGRATIONS_DIR / "026_findings_search.sql"
_DASH_PATH = _MIGRATIONS_DIR / "027_findings_search_dash_axes.sql"

_FN_SEARCH_SIG = "create or replace function public.findings_search(\n"
_FN_DOCUMENT_SIG = "create or replace function public.findings_document(p_finding_id text)\n"

# ⑤검색 blob 이 반드시 담아야 하는 컬럼 14종(정본 -- 조용한 축소/확대 방지).
_EXPECTED_BLOB_COLUMNS = {
    "finding_text_ko", "finding_text", "firm_name", "category_code",
    "category_label_ko", "document_id", "agency", "source", "published_date",
    "evidence_level", "review_status", "translation_method", "cfr_refs", "mfds_refs",
}

# ⑥허용 정렬 3종(정본).
_EXPECTED_SORTS = {"date_desc", "date_asc", "firm_asc"}


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


def _slice_function(code: str, signature: str) -> str:
    """comment 제거된 code 에서 signature 로 시작하는 함수 정의 전체(닫는 $$; 까지)를 뽑는다."""
    start = code.index(signature)
    end = code.index("$$;", start) + len("$$;")
    return code[start:end]


def _slice_between(text: str, start_marker: str, end_marker: str) -> str:
    """text 안에서 start_marker 부터(포함) 그 뒤 첫 end_marker 직전까지를 뽑는다."""
    start = text.index(start_marker)
    return text[start: text.index(end_marker, start)]


class SearchMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_SEARCH_PATH.is_file(), f"missing {_SEARCH_PATH}")
        self.sql = _SEARCH_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        # ★골든/마이그레이션 CRLF 함정(과거 전례) -- LF 고정.
        self.assertNotIn(b"\r\n", _SEARCH_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_defines_both_functions(self) -> None:
        self.assertIn("create or replace function public.findings_search(", self.code)
        self.assertIn("create or replace function public.findings_document(", self.code)


class SecurityInvokerContractTest(unittest.TestCase):
    """①공개 게이트의 단일 진실을 RLS(010)로 되돌리는 이 파일의 핵심 이탈 결정 --
    security definer 로 조용히 뒤집히면 RLS 가 우회돼 6중복 게이트가 되살아난다
    (파일 헤더 "★security invoker" 근거). search_path 고정은 invoker 에서도 유지."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))
        self.fn_search = _slice_function(self.code, _FN_SEARCH_SIG)
        self.fn_document = _slice_function(self.code, _FN_DOCUMENT_SIG)

    def test_both_functions_are_security_invoker(self) -> None:
        for fn in (self.fn_search, self.fn_document):
            self.assertIn("security invoker", fn)
            self.assertNotIn("security definer", fn)

    def test_both_functions_pin_search_path(self) -> None:
        for fn in (self.fn_search, self.fn_document):
            self.assertIn("set search_path = public", fn)


class PublicGateNotDuplicatedTest(unittest.TestCase):
    """②함수 본문이 공개 게이트 술어를 복제하지 않는다 -- RLS(010)가 유일한 게이트다.
    ★주의: 파일 헤더/하단 검증 주석에는 이 문자열이 설명으로 등장하므로, comment 를
    제거한 code 위에서 검사해야 한다(주석 포함 원문에는 존재함을 별도로 확인)."""

    def setUp(self) -> None:
        self.sql = _SEARCH_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        self.fn_search = _slice_function(self.code, _FN_SEARCH_SIG)
        self.fn_document = _slice_function(self.code, _FN_DOCUMENT_SIG)

    def test_gate_predicates_documented_in_raw_comments(self) -> None:
        """이 테스트 자체가 comment-strip 이 필요한 이유의 증거 -- 원문(주석 포함)에는
        게이트 문자열이 설명으로 실제 존재한다."""
        self.assertIn("scope_status = 'ok'", self.sql)
        self.assertIn("finding_text_ko <> ''", self.sql)

    def test_gate_predicates_absent_from_function_bodies(self) -> None:
        for fn in (self.fn_search, self.fn_document):
            self.assertNotIn("scope_status", fn)
            self.assertNotIn("finding_text_ko <> ''", fn)
            self.assertNotIn("finding_language = 'KO'", fn)


class IlikeNotFtsTest(unittest.TestCase):
    """③검색 semantics = ILIKE 부분일치 유지, FTS 미사용 -- D1: FTS 로 바꾸면 한국어
    `무균` 질의가 `무균실`·`무균의` 를 놓쳐 조용한 검색 축소가 된다(018 이미 실측)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))

    def test_uses_ilike(self) -> None:
        self.assertIn("ilike", self.code)

    def test_does_not_use_full_text_search_functions(self) -> None:
        for forbidden in ("to_tsvector", "websearch_to_tsquery", "to_tsquery"):
            self.assertNotIn(forbidden, self.code, f"026 must not use FTS function: {forbidden!r}")


class LikeWildcardEscapeTest(unittest.TestCase):
    """④LIKE 와일드카드(%·_·\\) 이스케이프 -- 없으면 사용자가 `%` 를 입력했을 때
    와일드카드로 동작해 클라이언트 indexOf(리터럴 취급)와 semantics 가 갈린다."""

    def test_replace_chain_escapes_backslash_percent_underscore_in_order(self) -> None:
        sql = _SEARCH_PATH.read_text(encoding="utf-8")
        # 백슬래시를 먼저 치환해야 뒤에 삽입한 이스케이프 문자를 다시 이스케이프하지
        # 않는다(파일 주석 명시) -- 순서까지 포함해 정확한 replace 체인을 고정한다.
        self.assertIn(
            "replace(replace(replace(coalesce(btrim(p_q), ''), '\\', '\\\\'), '%', '\\%'), "
            "'_', '\\_')",
            sql,
        )


class SearchBlobColumnsTest(unittest.TestCase):
    """⑤검색 blob 컬럼 목록 고정 -- "무엇이 검색되는가"를 문서화된 사실로 고정해
    조용한 축소/확대를 막는다(정본 = 14 컬럼)."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))
        self.blob_block = _slice_between(
            code,
            "coalesce(f.finding_text_ko, '')",
            ") ilike '%' || p.q_esc || '%'",
        )

    def test_blob_columns_are_exactly_the_declared_fourteen(self) -> None:
        found = set(re.findall(r"coalesce\(f\.(\w+)(?:::text)?, ''\)", self.blob_block))
        self.assertEqual(found, _EXPECTED_BLOB_COLUMNS)

    def test_blob_column_count_is_fourteen(self) -> None:
        self.assertEqual(len(_EXPECTED_BLOB_COLUMNS), 14)


class SortDeterminismTest(unittest.TestCase):
    """⑥정렬 결정론 -- 전 정렬 공통 최종 타이브레이크(d.tie asc)가 있어야 022 가 데인
    결함(fp16 동률 -> 평가 29슬롯 미판정)이 재발하지 않는다. 허용 정렬은 정확히
    date_desc/date_asc/firm_asc 3종이어야 한다(클램프 위반 시 조용히 무시되면 안 됨)."""

    def setUp(self) -> None:
        self.sql = _SEARCH_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_ordered_cte_has_final_tiebreak(self) -> None:
        ordered_block = _slice_between(self.code, "ordered as (", "tot as (")
        self.assertIn("d.tie asc", ordered_block)

    def test_allowed_sorts_are_exactly_the_declared_three(self) -> None:
        match = re.search(r"p_sort in \(([^)]*)\)", self.sql)
        self.assertIsNotNone(match, "p_sort allowlist not found")
        allowed = set(re.findall(r"'([a-z_]+)'", match.group(1)))
        self.assertEqual(allowed, _EXPECTED_SORTS)

    def test_unrecognized_sort_falls_back_to_date_desc(self) -> None:
        self.assertIn("then p_sort else 'date_desc' end", self.code)


class KoreanCollationTest(unittest.TestCase):
    """⑦firm_asc 경로는 ko-KR-x-icu collate 를 써야 한다 -- DB 기본 collate 는
    en_US.UTF-8 이라 없으면 한글 업체명 순서가 클라이언트 localeCompare 와 갈린다."""

    def test_firm_asc_order_key_uses_icu_collation(self) -> None:
        code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))
        ordered_block = _slice_between(code, "ordered as (", "tot as (")
        self.assertIn(
            '(case when p.sort = \'firm_asc\' then d.firm end) collate "ko-KR-x-icu" asc',
            ordered_block,
        )


class InputClampTest(unittest.TestCase):
    """⑧입력 클램프 -- 클라이언트를 신뢰하지 않는다: 페이지 하한 1, 페이지당 문서 수
    상한 100(하한 1), 정렬은 허용목록 case(위 SortDeterminismTest 와 상호보완)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))

    def test_page_is_clamped_to_at_least_one(self) -> None:
        self.assertIn("greatest(coalesce(p_page, 1), 1)", self.code)

    def test_docs_per_page_is_clamped_between_one_and_hundred(self) -> None:
        self.assertIn("least(greatest(coalesce(p_docs_per_page, 24), 1), 100)", self.code)

    def test_sort_has_case_based_allowlist(self) -> None:
        self.assertIn("case when p_sort in (", self.code)


class GrantExecuteTest(unittest.TestCase):
    """⑨grant execute to anon, authenticated 양쪽 함수 -- 미부여 시 사이트 전체 백지
    (invoker 라 execute 권한만으로 열리지 않고, RLS(010)+findings select(003)이 실제
    게이트지만 execute 자체가 없으면 그 앞 단계에서 막힌다)."""

    def setUp(self) -> None:
        self.sql = _SEARCH_PATH.read_text(encoding="utf-8")

    def test_findings_search_granted_to_anon_and_authenticated(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_search(text, text, text, text, text, "
            "text, text, text, int, int) to anon, authenticated;",
            self.sql,
        )

    def test_findings_document_granted_to_anon_and_authenticated(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_document(text) to anon, authenticated;",
            self.sql,
        )


class PageRowsLateTextFetchTest(unittest.TestCase):
    """⑩본문 텍스트는 **페이지분만 늦게** findings 에서 PK 로 되읽어야 한다.

    page_rows 가 filtered∩page_docs 를 findings 에 finding_id(=PK)로 join 하는 형태를
    고정한다. 이 형태가 두 가지를 동시에 해결한다:
      · 넓은 텍스트가 앞선 CTE 들을 통과하지 않는다(아래 SearchedCteIsNarrowTest 참조)
      · planner 가 PK Index Scan 을 쓴다(실측 Index Scan using findings_pkey, 65행)
    초안은 filtered 를 page_docs 에 직접 걸어 CTE 간 nested loop O(n×m) 가 됐었다
    (실측 Rows Removed by Join Filter: 58,848).
    """

    def setUp(self) -> None:
        code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))
        self.code = code
        self.page_rows_block = _slice_between(code, "page_rows as (", "page_docs_full as (")

    def test_page_rows_joins_findings_by_primary_key(self) -> None:
        self.assertIn("join public.findings f on f.finding_id = fl.finding_id", self.page_rows_block)

    def test_page_rows_restricted_to_page_docs(self) -> None:
        self.assertIn("join page_docs pd on pd.raw_signal_id = fi.raw_signal_id", self.page_rows_block)

    def test_does_not_use_any_select_array_agg_form(self) -> None:
        """any (select array_agg(…)) 는 서브쿼리로 해석돼 `text = text[]` 타입 에러를
        낸다 -- dry-run 이 실제로 잡은 버그라 되살아나면 함수가 아예 안 돈다."""
        self.assertNotIn("any (select array_agg", self.code)
        self.assertNotIn("any(select array_agg", self.code)


class SearchedCteIsNarrowTest(unittest.TestCase):
    """⑪searched CTE 는 **좁아야** 한다 -- 본문 텍스트를 싣지 않고 컬럼을 명시 투영한다.

    ★이 계약이 이 파일에서 가장 비싸게 배운 것이다. 본문(finding_text/finding_text_ko)을
    이 CTE 에 실었더니 **무검색 랜딩**(q='' 이라 8,168행 전량이 통과하는 최빈·최악 경로)에서
    CTE materialize 가 temp 파일로 스필해 659ms 가 나왔다(temp read=3,714 written=1,238).
    검색이 있을 때는 2,454행만 남아 메모리에 들어가므로 **검색만 재보면 통과하는 함정**이었다.
    좁힌 뒤 127.7ms·스필 0. 본문은 page_rows 가 페이지분만 PK 로 되읽는다.

    blob 에 쓰이는 컬럼(category_label_ko·document_id·translation_method·cfr_refs·
    mfds_refs)은 WHERE 절이 f 를 직접 참조하므로 select 목록에 없어야 정상이다.
    """

    _WIDE_COLUMNS = ("finding_text", "finding_text_ko")

    def setUp(self) -> None:
        code = _strip_sql_comments(_SEARCH_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "searched as (", "filtered as (")
        # select 목록만 본다 -- blob(ILIKE 대상)은 where 절이라 넓은 컬럼을 참조하는 게 정상.
        self.select_list = _slice_between(block, "select", "from public.findings f")
        self.block = block

    def test_searched_cte_does_not_select_star(self) -> None:
        self.assertNotIn("select *", self.block)

    def test_searched_cte_explicitly_projects_columns(self) -> None:
        self.assertIn("f.finding_id, f.raw_signal_id, f.source, f.agency", self.select_list)

    def test_searched_cte_omits_wide_text_columns(self) -> None:
        for col in self._WIDE_COLUMNS:
            self.assertNotIn(
                f"f.{col}",
                self.select_list,
                msg=(
                    f"searched CTE select 목록에 f.{col} 이 있다. 넓은 텍스트를 CTE 로 물고 "
                    "다니면 무검색 랜딩에서 temp 스필이 나 5배 느려진다(659ms vs 127ms). "
                    "본문은 page_rows 가 페이지분만 PK 로 되읽어야 한다."
                ),
            )


class MigrationNumberSequenceTest(unittest.TestCase):
    """⑫026 에 이어 027 이 findings_search 를 supersede 하며 마지막 번호가 갱신됐다 --
    마이그레이션 번호가 001~027 까지 결번 없이 연속인지(파일명 접두 3자리 번호 기준) 고정한다."""

    def test_026_file_exists(self) -> None:
        self.assertTrue(_SEARCH_PATH.is_file(), f"missing {_SEARCH_PATH}")

    def test_027_file_exists(self) -> None:
        self.assertTrue(_DASH_PATH.is_file(), f"missing {_DASH_PATH}")

    def test_migration_numbers_are_contiguous_from_001_to_027(self) -> None:
        numbers = sorted(
            int(m.group(1))
            for p in _MIGRATIONS_DIR.glob("*.sql")
            if (m := re.match(r"^(\d{3})_", p.name))
        )
        self.assertEqual(numbers, list(range(1, 28)))


# ============================================================================
# 027_findings_search_dash_axes.sql -- findings_search 를 supersede 해 대시보드
# 축(agency 파셋 + dash 블록)을 추가하는 마이그레이션의 정적 계약.
#
# 026 은 그 시점의 정본 기록으로 위에 그대로 남아 있다(findings_document 정의의 정본은
# 계속 026). 027 은 findings_search 만 재선언하므로 아래 클래스들은 026 클래스를 건드리지
# 않고 027 전용 파일을 대상으로 별도 검사한다.
# ============================================================================


class DashAxesRedeclarationScopeTest(unittest.TestCase):
    """②027 은 findings_search 만 재선언하고 findings_document 는 재선언하지 않는다 --
    딥링크 해석(findings_document)은 대시보드 축과 무관하므로 불필요한 재선언은 diff 를
    부풀리고 "무엇이 바뀌었나"를 흐린다(027 헤더 (B) 절 근거)."""

    def setUp(self) -> None:
        self.assertTrue(_DASH_PATH.is_file(), f"missing {_DASH_PATH}")
        self.code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))

    def test_redeclares_findings_search(self) -> None:
        self.assertIn(_FN_SEARCH_SIG, self.code)

    def test_does_not_redeclare_findings_document(self) -> None:
        self.assertNotIn("create or replace function public.findings_document", self.code)


class DashSecurityInvokerCarryoverTest(unittest.TestCase):
    """③027 의 findings_search 도 security invoker + search_path 고정을 유지한다 --
    supersede 과정에서 026 이 확립한 "RLS(010)가 유일한 게이트" 원칙이 뒤집히면 안 된다."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))
        self.fn_search = _slice_function(code, _FN_SEARCH_SIG)

    def test_is_security_invoker(self) -> None:
        self.assertIn("security invoker", self.fn_search)
        self.assertNotIn("security definer", self.fn_search)

    def test_pins_search_path(self) -> None:
        self.assertIn("set search_path = public", self.fn_search)


class DashSearchedCteCarryoverTest(unittest.TestCase):
    """④좁은 searched CTE 계약이 027 에서도 승계된다 -- select 목록에 finding_text/
    finding_text_ko 가 없어야 무검색 랜딩 659ms->127ms 개선(026 실측)이 유지된다.
    다만 f.firm_key 는 있어야 한다 -- 027 이 top_firms 집계를 위해 추가한 것으로,
    027 헤더가 밝히듯 짧은 generated 컬럼이라 본문 텍스트와 달리 스필을 유발하지 않는다."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "searched as (", "filtered as (")
        self.select_list = _slice_between(block, "select", "from public.findings f")

    def test_omits_wide_text_columns(self) -> None:
        for col in ("finding_text", "finding_text_ko"):
            self.assertNotIn(
                f"f.{col}",
                self.select_list,
                msg=f"027 searched CTE select 목록에 f.{col} 이 있다 -- 026 의 스필 방지 계약 위반.",
            )

    def test_includes_firm_key_for_top_firms(self) -> None:
        self.assertIn("f.firm_key", self.select_list)


class FacetsAndDashAxesPresentTest(unittest.TestCase):
    """⑤반환 jsonb 의 facets 블록에 6축(source/category/month/evidence/review_status/
    agency), dash 블록에 4축(agency/category/month/top_firms)이 있어야 한다 -- PR-B 가
    요구하는 findings.js 렌더 전환(agency 분포·top firms)이 이 데이터 계약에 의존한다."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))

    def test_facets_block_has_six_axes(self) -> None:
        block = _slice_between(
            self.code, "'facets', jsonb_build_object(", "'dash', jsonb_build_object("
        )
        for key in (
            "by_source", "by_category", "by_month",
            "by_evidence", "by_review_status", "by_agency",
        ):
            self.assertIn(f"'{key}'", block)

    def test_dash_block_has_four_axes(self) -> None:
        block = _slice_between(self.code, "'dash', jsonb_build_object(", "'page',")
        for key in ("by_agency", "by_category", "by_month", "top_firms"):
            self.assertIn(f"'{key}'", block)


class FacetsVsDashPopulationTest(unittest.TestCase):
    """⑥facets 축(fac_*)은 검색만 적용한 'searched' 를, dash 축(dash_*)은 필터 전량
    적용한 'filtered' 를 모집단으로 삼는다 -- 뒤바뀌면 "소스를 MFDS 로 바꾸면 몇 건?"
    (파셋)과 "현재 결과의 분포"(대시보드)가 서로 다른 질문에 틀린 답을 낸다(027 헤더 근거)."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))

    def test_fac_ctes_use_searched_base(self) -> None:
        bounds = [
            ("fac_source as (", "fac_cat as ("),
            ("fac_cat as (", "fac_month as ("),
            ("fac_month as (", "fac_ev as ("),
            ("fac_ev as (", "fac_rs as ("),
            ("fac_rs as (", "fac_agency as ("),
            ("fac_agency as (", "dash_agency as ("),
        ]
        for start, end in bounds:
            block = _slice_between(self.code, start, end)
            self.assertIn("from searched s, p", block, msg=f"{start} block")

    def test_dash_ctes_use_filtered_base(self) -> None:
        bounds = [
            ("dash_agency as (", "dash_cat as ("),
            ("dash_cat as (", "dash_month as ("),
            ("dash_month as (", "dash_firms as ("),
        ]
        for start, end in bounds:
            block = _slice_between(self.code, start, end)
            self.assertIn("from filtered f", block, msg=f"{start} block")


class FacAgencyExcludesOwnAxisTest(unittest.TestCase):
    """⑦fac_agency 는 자기 축(agency)의 필터 술어를 제외한다(표준 파세팅) -- 넣으면
    "기관을 X 로 바꾸면 몇 건?" 질문이 항상 현재 선택값으로만 필터링되어 무의미해진다.
    같은 원리로 fac_source 도 p.f_source 를 제외해야 한다."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))

    def test_fac_agency_omits_agency_predicate_but_keeps_others(self) -> None:
        block = _slice_between(self.code, "fac_agency as (", "dash_agency as (")
        self.assertNotIn("p.f_agency", block)
        for other in ("p.f_source", "p.f_cat", "p.f_month", "p.f_ev", "p.f_rs"):
            self.assertIn(other, block)

    def test_fac_source_omits_source_predicate_but_keeps_others(self) -> None:
        block = _slice_between(self.code, "fac_source as (", "fac_cat as (")
        self.assertNotIn("p.f_source", block)
        for other in ("p.f_cat", "p.f_month", "p.f_ev", "p.f_rs", "p.f_agency"):
            self.assertIn(other, block)


class TopFirmsRepresentativeNameLateralTest(unittest.TestCase):
    """⑧top_firms 대표 표시명은 025 와 동일한 lateral 규칙(최빈 -> 최장 -> 알파벳)을 써야
    한다 -- min(firm_name) 으로 골랐더니 025 와 건수·순서는 같은데 표기만 갈렸다(실측:
    `Hospira Inc` vs 025 의 `Hospira Inc.`). 같은 블록이 필터 유무로 표기가 바뀌면 사용자
    에겐 다른 회사처럼 보인다."""

    def test_dash_firms_lateral_orders_by_count_then_length_then_alpha(self) -> None:
        code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "dash_firms as (", "select jsonb_build_object(")
        self.assertIn(
            "order by count(*) desc, length(f2.firm_name) desc, f2.firm_name asc",
            block,
        )


class TopFirmsGroupedByFirmKeyTest(unittest.TestCase):
    """⑨top_firms 는 firm_name 이 아니라 firm_key 로 묶는다 -- 017/025 가 정규화한 기준.
    종전 클라이언트는 무필터=firm_key(RPC), 필터=firm_name(computeFirmTop) 으로 기준이
    갈리는 잠복 불일치가 있었다 -- 서버 일원화로 이 불일치를 없앤다."""

    def test_dash_firms_groups_by_firm_key_not_firm_name(self) -> None:
        code = _strip_sql_comments(_DASH_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "dash_firms as (", "select jsonb_build_object(")
        self.assertIn("group by f.firm_key", block)
        self.assertNotIn("group by f.firm_name", block)


class DashGrantScopeTest(unittest.TestCase):
    """⑩027 은 findings_search 의 grant 를 재선언하고(단독으로 fresh DB 에 적용해도
    성립하도록, create or replace 자체는 멱등), findings_document 의 grant 는 하지
    않는다 -- 그 함수를 만들지 않은 이 파일이 아니라 026 이 계속 담당한다."""

    def setUp(self) -> None:
        self.sql = _DASH_PATH.read_text(encoding="utf-8")

    def test_grants_findings_search_execute_to_anon_and_authenticated(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_search(text, text, text, text, text, "
            "text, text, text, int, int) to anon, authenticated;",
            self.sql,
        )

    def test_does_not_grant_findings_document(self) -> None:
        self.assertNotIn("grant execute on function public.findings_document", self.sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
