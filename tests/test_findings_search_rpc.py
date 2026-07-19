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
  ⑫파일 번호 연속성(001~029, 결번 없음).
  ⑬028: 클라이언트가 FIELDS 로 선언한 필드는 두 RPC 의 findings[] 투영에 전부 실린다
    -- 026/027 이 firm_key/translation_method/confidence 를 빠뜨려 업체 프로파일 링크·
    AI 번역 고지·신뢰도가 **조용히** 소실됐다(방어적 분기라 크래시 없음). 클라이언트가
    잡을 수 없는 결함이므로 가드는 서버측 투영에 둔다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_SEARCH_PATH = _MIGRATIONS_DIR / "026_findings_search.sql"
_DASH_PATH = _MIGRATIONS_DIR / "027_findings_search_dash_axes.sql"
_PROJ_PATH = _MIGRATIONS_DIR / "028_findings_rpc_projection.sql"
_ENTITY_REPAIR_PATH = _MIGRATIONS_DIR / "029_findings_html_entity_repair.sql"
_HARDENING_PATH = _MIGRATIONS_DIR / "030_findings_search_hardening.sql"
_REACTIONS_TOP_PATH = _MIGRATIONS_DIR / "031_reactions_weekly_top.sql"
_CLIENT_JS_PATH = Path(__file__).resolve().parent.parent / "web" / "assets" / "findings.js"

# ⑬028 이 복원한, 클라이언트 카드 조립부가 읽는 필드 3종(회귀 고정용 명시 목록).
#   findings.js:1200 head.firm_key / :899 row.translation_method / :931 row.confidence.
_RESTORED_BY_028 = ("firm_key", "translation_method", "confidence")

# ⑬-b 클라이언트 FIELDS 중 RPC 투영에서 **의도적으로** 빠지는 필드.
#   finding_language 는 공개 게이트 술어(010: finding_text_ko <> '' or finding_language='KO')
#   에만 쓰이고 findings.js 가 렌더에서 읽지 않는다(row.finding_language 참조 0건). 게이트는
#   invoker+RLS 가 서버에서 강제하므로 클라이언트로 내보낼 이유가 없다.
#   ★이 집합에 필드를 추가하려면 "클라이언트가 읽지 않음"을 grep 으로 확인하고 근거를 적어라.
_FIELDS_NOT_PROJECTED = {"finding_language"}

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
    """⑫026 → 027(findings_search supersede) → 028(두 함수 supersede) → 029(HTML 엔티티
    오염 정정) → 030(findings_search 재supersede -- work_mem/p_page 클램프/blob semantics)
    로 체인이 이어지며, 031(reactions_weekly_top -- findings 밖 반응 주간 집계 count-only
    RPC)이 추가됐고, 032(gurumi_growth -- 구름이 성장 데이터 로그인 보관 테이블·본인 행
    RLS)가 뒤따랐다 -- 마이그레이션 번호가 001~032 까지 결번 없이 연속인지(파일명 접두
    3자리 번호 기준) 고정한다."""

    def test_026_file_exists(self) -> None:
        self.assertTrue(_SEARCH_PATH.is_file(), f"missing {_SEARCH_PATH}")

    def test_027_file_exists(self) -> None:
        self.assertTrue(_DASH_PATH.is_file(), f"missing {_DASH_PATH}")

    def test_028_file_exists(self) -> None:
        self.assertTrue(_PROJ_PATH.is_file(), f"missing {_PROJ_PATH}")

    def test_029_file_exists(self) -> None:
        self.assertTrue(_ENTITY_REPAIR_PATH.is_file(), f"missing {_ENTITY_REPAIR_PATH}")

    def test_030_file_exists(self) -> None:
        self.assertTrue(_HARDENING_PATH.is_file(), f"missing {_HARDENING_PATH}")

    def test_031_file_exists(self) -> None:
        self.assertTrue(_REACTIONS_TOP_PATH.is_file(), f"missing {_REACTIONS_TOP_PATH}")

    def test_migration_numbers_are_contiguous_from_001_to_032(self) -> None:
        numbers = sorted(
            int(m.group(1))
            for p in _MIGRATIONS_DIR.glob("*.sql")
            if (m := re.match(r"^(\d{3})_", p.name))
        )
        self.assertEqual(numbers, list(range(1, 33)))


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


# ============================================================================
# 028_findings_rpc_projection.sql -- 026/027 의 **반환 계약 결함** 수리.
#
# 결함: 클라이언트 카드 조립부가 읽는 firm_key/translation_method/confidence 가 두 RPC 의
# 투영(jsonb_build_object)에 없었다. 셋 다 방어적 분기(013·005 미적용 라이브 DB 하위호환
# 폴백)로 읽히므로 크래시 없이 **조용히** 링크·AI 고지·신뢰도만 빠졌다.
#
# ★아래 ProjectionCoversClientFieldsTest 가 이 결함군의 정본 가드다. 3필드를 하드코딩하는
#   대신 findings.js 의 FIELDS 선언(클라이언트가 "무엇이 필요한가"를 스스로 밝힌 목록)을
#   파싱해 교차검증한다 -- 이번 3필드뿐 아니라 **다음에 추가될 필드의 투영 누락**까지 잡는다.
#   이 결함이 조용했던 이유가 "클라이언트가 못 잡는다"였으므로 가드는 서버측에 있어야 한다.
# ============================================================================


#: findings 테이블의 실제 컬럼(002 fresh-install + 010 scope_status + 013 firm_key).
#   아래 파싱이 `row.length`·`row.className` 같은 비-컬럼 접근을 걸러내는 화이트리스트다.
_FINDINGS_COLUMNS = {
    "schema_version", "taxonomy_version", "finding_id", "raw_signal_id", "source",
    "agency", "document_type", "document_id", "published_date", "firm_name", "entity_id",
    "site_name", "site_country", "product_family", "modality", "category_code",
    "category_label_ko", "finding_text", "finding_language", "evidence_level",
    "evidence_url", "inspector_names", "cfr_refs", "mfds_refs", "extraction_method",
    "confidence", "review_status", "ingested_at", "finding_text_ko",
    "translation_method", "scope_status", "firm_key",
}


#: 파서가 dot-access 로 인식하는 수신 변수명 -- 이 튜플이 유일한 진실이다.
#   _parse_fields_from_source() 의 정규식과 ReceiverVariableNamingConventionTest 의
#   교차검증이 전부 이 상수를 읽는다. 각자 사본을 하드코딩하면 한쪽만 바뀌었을 때
#   조용히 어긋난다(Major 3 수리 ② -- Codex 통합 정밀점검 2026-07-16).
_RECEIVER_NAMES = ("row", "head", "r", "f")


def _parse_fields_from_source(js: str) -> list[str]:
    """js 소스 문자열(전체 파일 또는 뮤테이션된 사본)에서 findings 컬럼 dot-access 를 뽑는다.

    _parse_client_fields() 의 실제 로직 -- 문자열을 인자로 받도록 분리해 뮤테이션
    자기검증 테스트(MutationSelfVerificationTest)가 파일을 실제로 바꾸지 않고도 같은
    파싱 로직을 사본 위에서 재실행할 수 있게 한다."""
    used = set(re.findall(rf"\b(?:{'|'.join(_RECEIVER_NAMES)})\.([a-z_]+)\b", js))
    return sorted(used & _FINDINGS_COLUMNS)


def _parse_client_fields() -> list[str]:
    """findings.js 의 카드 조립부가 **행 객체에서 실제로 읽는** findings 컬럼을 뽑는다.

    ★앵커가 옮겨졌다: 종전에는 `var FIELDS = [ ... ];` 선언을 파싱했다. 그 선언은
    클라이언트가 PostgREST 로 직접 조회하며 "무엇을 select 할지" 밝히던 목록인데,
    서버 canonical search 전환으로 클라이언트가 더 이상 select 를 결정하지 않게 되어
    선언 자체가 사라졌다.

    그래서 **선언 대신 사용(row.X / head.X / r.X 접근)** 을 읽는다. 이게 원래 지키려던
    진짜 계약이기도 하다 — "클라이언트가 선언한 것"이 아니라 "**렌더러가 읽는 것**"이
    투영돼야 기능이 살아있기 때문이다. 선언은 낡을 수 있지만 사용은 낡지 않는다.

    비-컬럼 접근(row.length 등)은 _FINDINGS_COLUMNS 화이트리스트로 걸러낸다.

    ★Major 3(Codex 통합 정밀점검 2026-07-16): 이 파서는 dot-access 만 읽는 구조적
    사각지대가 있다 -- `row.document_id` 를 `row["document_id"]` 로 바꾸고 투영에서
    document_id 를 빼도 이 파서는 여전히 "document_id 를 안 읽는다"고 (틀리게) 보고해
    ProjectionCoversClientFieldsTest 가 66/66 green 으로 우회됐다(Codex 실증). 완벽한
    JS 정적 분석은 불가능하므로, 아래 NoBracketAccessOnFindingsColumnsTest 가 "이
    사각지대로 통하는 문법(bracket-access) 자체가 존재하지 않는다"를 별도 계약으로
    고정해 보완한다.
    """
    js = _CLIENT_JS_PATH.read_text(encoding="utf-8")
    return _parse_fields_from_source(js)


#: bracket-access(`["col"]`/`['col']`) 로 findings 컬럼을 읽는 표기 전부를 잡는 정규식.
#   컬럼명 알파벳 목록은 _FINDINGS_COLUMNS 화이트리스트를 그대로 재사용한다(32종).
_BRACKET_ACCESS_RE = re.compile(
    r"\[\s*[\"'](" + "|".join(sorted(_FINDINGS_COLUMNS)) + r")[\"']\s*\]"
)


def _bracket_access_violations(js: str) -> list[str]:
    """js 소스 문자열에서 findings 컬럼에 대한 bracket-access 표기를 전부 찾는다.

    _parse_fields_from_source() 가 dot-access(row.col) 만 읽으므로, 같은 데이터를
    bracket-access(row["col"])로 읽으면 투영 가드의 사각지대가 된다 -- Codex 가
    `row.confidence` -> `row["confidence"]` 뮤테이션으로 이 우회를 실증했다(66/66
    green 유지, 투영에서 confidence 를 빼도 가드 미발화)."""
    return _BRACKET_ACCESS_RE.findall(js)


class ClientFieldsParseTest(unittest.TestCase):
    """가드의 입력(필드 파싱)이 살아 있는지부터 고정한다 -- 파싱이 조용히 빈 목록을
    반환하면 아래 교차검증이 **전부 공허하게 통과**한다(가드가 죽은 줄도 모른다).

    ★이 클래스가 있어야 하는 이유가 이번에 실증됐다: 앵커였던 `var FIELDS` 선언이
    전환으로 사라졌는데, 파싱이 조용히 빈 목록을 냈다면 투영 누락 가드가 죽은 채로
    green 이었을 것이다."""

    def test_client_js_exists(self) -> None:
        self.assertTrue(_CLIENT_JS_PATH.is_file(), f"missing {_CLIENT_JS_PATH}")

    def test_fields_parse_is_non_empty_and_plausible(self) -> None:
        fields = _parse_client_fields()
        self.assertGreaterEqual(len(fields), 12, msg=f"필드 파싱 결과가 빈약하다: {fields}")
        self.assertIn("finding_id", fields)
        self.assertIn("finding_text_ko", fields)

    def test_restored_three_are_read_by_client(self) -> None:
        # 028 이 복원한 3종을 렌더러가 실제로 읽어야 그 수리의 근거가 성립한다.
        fields = _parse_client_fields()
        for col in _RESTORED_BY_028:
            self.assertIn(col, fields)


class ProjectionMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PROJ_PATH.is_file(), f"missing {_PROJ_PATH}")
        self.sql = _PROJ_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        # ★골든/마이그레이션 CRLF 함정(과거 전례) -- LF 고정.
        self.assertNotIn(b"\r\n", _PROJ_PATH.read_bytes())

    def test_redeclares_both_functions(self) -> None:
        """028 은 027 과 달리 findings_document 도 재선언한다 -- 같은 결함이 두 함수에
        동일하게 있고, 한쪽만 고치면 "목록에선 보이는데 딥링크로 열면 사라지는" 불일치가
        된다(028 헤더 (B) 절 근거)."""
        self.assertIn("create or replace function public.findings_search(", self.code)
        self.assertIn("create or replace function public.findings_document(", self.code)


class ProjectionCoversClientFieldsTest(unittest.TestCase):
    """★정본 가드: 클라이언트가 FIELDS 로 선언한 필드는 두 RPC 의 findings[] 투영에 전부
    실려야 한다(_FIELDS_NOT_PROJECTED 예외 제외). 이 결함(3필드 조용한 소실)이 애초에
    통과한 이유가 "서버 투영과 클라이언트 소비를 아무도 대조하지 않았다" 이므로, 그 대조를
    테스트로 만든다."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_PROJ_PATH.read_text(encoding="utf-8"))
        # findings_search 쪽 findings[] 투영 = page_docs_full 의 jsonb_agg 블록.
        self.search_rows = _slice_between(code, "page_docs_full as (", "fac_source as (")
        # findings_document 쪽 findings[] 투영 = 함수 정의 전체(rows_out jsonb_agg 포함).
        self.document_fn = _slice_function(code, _FN_DOCUMENT_SIG)
        self.expected = [f for f in _parse_client_fields() if f not in _FIELDS_NOT_PROJECTED]

    def test_findings_search_projects_every_client_field(self) -> None:
        for col in self.expected:
            self.assertIn(
                f"'{col}',",
                self.search_rows,
                msg=f"findings_search 의 findings[] 투영에 '{col}' 이 없다 -- 클라이언트가 "
                    f"FIELDS 로 선언한 필드다. 조용히 소실된다(방어적 분기라 크래시 없음).",
            )

    def test_findings_document_projects_every_client_field(self) -> None:
        for col in self.expected:
            self.assertIn(
                f"'{col}',",
                self.document_fn,
                msg=f"findings_document 의 findings[] 투영에 '{col}' 이 없다 -- 목록/딥링크 "
                    f"불일치가 된다.",
            )

    def test_exempt_fields_are_actually_unread_by_client(self) -> None:
        """예외 목록이 알리바이로 쓰이지 않게 한다 -- 클라이언트가 실제로 읽는 필드를
        _FIELDS_NOT_PROJECTED 에 넣어 가드를 무력화하는 것을 막는다."""
        js = _CLIENT_JS_PATH.read_text(encoding="utf-8")
        for col in _FIELDS_NOT_PROJECTED:
            for reader in (f"row.{col}", f"head.{col}"):
                self.assertNotIn(
                    reader,
                    js,
                    msg=f"{reader} 를 클라이언트가 읽는데 _FIELDS_NOT_PROJECTED 에 있다 -- "
                        f"예외가 아니라 투영해야 할 필드다.",
                )


class ProjectionRestoredFieldsTest(unittest.TestCase):
    """⑬028 이 복원한 3종을 명시적으로도 고정한다 -- 위 교차검증은 FIELDS 선언에 의존하므로,
    누군가 FIELDS 에서 3종을 지우면(예: "안 쓰는 것 같아서") 가드가 조용히 헐거워진다.
    이 클래스는 그 경우에도 실패해서 결함 재발을 막는다(가드의 이중화)."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_PROJ_PATH.read_text(encoding="utf-8"))
        self.search_rows = _slice_between(code, "page_docs_full as (", "fac_source as (")
        self.document_fn = _slice_function(code, _FN_DOCUMENT_SIG)

    def test_restored_fields_present_in_both_functions(self) -> None:
        for col in _RESTORED_BY_028:
            self.assertIn(f"'{col}',", self.search_rows, msg=f"findings_search: {col}")
            self.assertIn(f"'{col}',", self.document_fn, msg=f"findings_document: {col}")

    def test_page_rows_selects_restored_fields(self) -> None:
        """투영에 앞서 page_rows 가 세 컬럼을 실제로 읽어야 한다(jsonb 키만 있고 소스가
        없으면 SQL 자체가 깨진다)."""
        code = _strip_sql_comments(_PROJ_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "page_rows as (", "page_docs_full as (")
        for col in _RESTORED_BY_028:
            self.assertIn(f"f.{col}", block)

    def test_document_level_firm_key_present(self) -> None:
        """문서 대표값 묶음에도 firm_key 를 싣는다 -- buildDocHead 는 rows[0] 을 읽으므로
        렌더에는 findings[] 만 있어도 되지만, 대표값 묶음에 firm_name 만 있고 firm_key 가
        빠지면 계약이 불명확하다(028 헤더 근거)."""
        code = _strip_sql_comments(_PROJ_PATH.read_text(encoding="utf-8"))
        docs_block = _slice_between(code, "'documents', coalesce(", "'totals', jsonb_build_object(")
        self.assertIn("'firm_key',", docs_block)
        self.assertIn("min(pr.firm_key)", code)
        self.assertIn("min(firm_key) from rows_out", code)


class ProjectionNarrowWorkingSetCarryoverTest(unittest.TestCase):
    """★026 의 좁은 작업집합 계약이 028 에서도 승계된다 -- 세 필드는 searched 가 아니라
    page_rows(페이지 24문서분)에만 추가해야 한다. searched 에 본문 텍스트를 실었을 때
    무검색 랜딩이 659ms(temp 스필)였고 좁힌 뒤 127.7ms 였다(026 실측).
    translation_method/confidence 는 짧은 컬럼이라 스필 위험은 없지만, searched 에 넣을
    이유가 없다 -- 계약은 "필요한 범위에서만 읽는다" 이지 "짧으면 아무 데나" 가 아니다.
    ※firm_key 는 예외다: 027 이 top_firms 집계 목적으로 이미 searched 에 넣었고 028 은 그
      결정을 승계한다(같은 컬럼, 독립된 두 소비처)."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_PROJ_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "searched as (", "filtered as (")
        self.select_list = _slice_between(block, "select", "from public.findings f")

    def test_searched_omits_wide_text_columns(self) -> None:
        for col in ("finding_text", "finding_text_ko"):
            self.assertNotIn(
                f"f.{col}",
                self.select_list,
                msg=f"028 searched CTE select 목록에 f.{col} 이 있다 -- 026 의 스필 방지 계약 위반.",
            )

    def test_searched_does_not_gain_card_only_columns(self) -> None:
        for col in ("translation_method", "confidence"):
            self.assertNotIn(
                f"f.{col}",
                self.select_list,
                msg=f"f.{col} 은 카드 렌더 전용이라 page_rows 에만 있어야 한다 -- searched 는 "
                    f"필터·파셋·문서묶음용 좁은 작업집합이다(028 헤더 근거).",
            )

    def test_searched_keeps_firm_key_for_top_firms(self) -> None:
        self.assertIn("f.firm_key", self.select_list)


class ProjectionInvokerAndGrantCarryoverTest(unittest.TestCase):
    """①②⑨ 026/027 의 보안·권한 계약이 028 에서도 승계된다 -- 재선언이 계약을 조용히
    되돌리는(definer 회귀·게이트 복제 부활·grant 누락) 것을 막는다. 028 은 findings_document
    도 만드므로 027 과 달리 **양쪽** grant 가 있어야 한다(없으면 딥링크가 백지)."""

    def setUp(self) -> None:
        self.sql = _PROJ_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_both_functions_are_security_invoker(self) -> None:
        for sig in (_FN_SEARCH_SIG, _FN_DOCUMENT_SIG):
            self.assertIn("security invoker", _slice_function(self.code, sig))

    def test_both_functions_pin_search_path(self) -> None:
        for sig in (_FN_SEARCH_SIG, _FN_DOCUMENT_SIG):
            self.assertIn("set search_path = public", _slice_function(self.code, sig))

    def test_gate_predicates_absent_from_function_bodies(self) -> None:
        # ②RLS(010)가 유일한 게이트여야 한다 -- 투영 수리를 하면서 게이트를 복제하지 않았는지.
        for sig in (_FN_SEARCH_SIG, _FN_DOCUMENT_SIG):
            body = _slice_function(self.code, sig)
            self.assertNotIn("scope_status", body)
            self.assertNotIn("finding_language = 'KO'", body)

    def test_grants_both_functions_to_anon_and_authenticated(self) -> None:
        for sig in (
            "grant execute on function public.findings_search"
            "(text, text, text, text, text, text, text, text, int, int) to anon, authenticated;",
            "grant execute on function public.findings_document(text) to anon, authenticated;",
        ):
            self.assertIn(sig, self.sql)


# ============================================================================
# Major 3(Codex 통합 정밀점검 2026-07-16) -- 투영 패리티 가드(ProjectionCoversClientFieldsTest)
# 의 입력인 _parse_client_fields() 가 dot-access(row.X)만 읽는 정적 파서라는 구조적 한계를
# Codex 가 뮤테이션으로 실증했다: findings.js 에서 `row.document_id` -> `row["document_id"]`
# 로 바꾸고 두 RPC 투영에서 document_id 를 빼도 66/66 green(가드가 우회됨).
#
# 완벽한 JS 정적 분석은 불가능하다. 실용적 정답 = 접근 문법 자체를 계약으로 제약하고
# (①②) 그 제약이 실제로 발화하는지 뮤테이션으로 자기검증한다(③).
# ============================================================================


class NoBracketAccessOnFindingsColumnsTest(unittest.TestCase):
    """①bracket-access 금지 계약 -- findings.js 전체에서 findings 컬럼명(32종 화이트
    리스트)에 대한 `["col"]`/`['col']` 표기가 **존재하지 않는다**를 고정한다.

    왜: _parse_client_fields 는 dot-access 만 읽으므로, bracket-access 가 생기면 그
    필드는 투영 가드의 사각지대가 된다 -- Codex 가 뮤테이션으로 실증한 우회로(위 섹션
    헤더 참조)를 문법 계약으로 봉쇄한다. 렌더러가 정말 동적 접근이 필요해지면 이 테스트를
    의도적으로 갱신하며 _parse_fields_from_source()/_BRACKET_ACCESS_RE 도 함께 확장하라는
    안내를 남긴다."""

    def test_findings_js_has_no_bracket_access_on_findings_columns(self) -> None:
        js = _CLIENT_JS_PATH.read_text(encoding="utf-8")
        violations = _bracket_access_violations(js)
        self.assertEqual(
            violations, [],
            msg=(
                f"findings.js 에 findings 컬럼 bracket-access 발견: {violations} -- "
                f"dot-access(row.X)로 바꾸거나, 정말 필요하면 _parse_fields_from_source() "
                f"정규식을 확장하고 이 테스트를 의도적으로 갱신하라(투영 가드의 사각지대가 "
                f"되지 않도록)."
            ),
        )


class ReceiverVariableNamingConventionTest(unittest.TestCase):
    """②변수명 관례 계약 -- 파서가 인식하는 수신 변수(_RECEIVER_NAMES = row/head/r/f) 밖의
    이름으로 findings 행이 흐르는 것도 사각지대다. 완전 차단은 불가능하니, findings_search/
    findings_document RPC 응답(documents[].findings[])을 실제로 받는 지역 변수명을 실측해
    파서 whitelist(_RECEIVER_NAMES -- 파서 정규식이 실제로 읽는 소스)에 전부 포함되는지
    교차확인한다.

    실측(2026-07-17): buildDocCard(rows, query) 의 두 `.forEach(function (row) {...})`·
    buildDocHead(rows) 의 `var head = rows[0];`·buildCard(row, query) 의 파라미터명 --
    전부 row/head 이며 _RECEIVER_NAMES 안에 있다.

    ★잔여 사각지대(정직하게): findings_similar/findings_similar_to RPC(018/021/022, 026~030
    findings_search/findings_document 와 무관한 별도 마이그레이션) 소비부는 `item` 이라는
    이름을 쓴다(mapSimilarItemToRow(item)/buildSimilarToItem(item)/renderSimilarResults 의
    `items.forEach(function (item) {...})`). 이 RPC 는 이 가드의 대상이 아니라서 위반으로
    잡지 않지만, "item" 자체는 파서 whitelist 밖이다 -- 만약 향후 findings_search 결과가
    "item" 이라는 이름으로 흐르게 리팩터링되면 이 교차검증도 파서도 그 사실을 모른 채
    통과한다(자동 탐지 불가능한 구조적 한계)."""

    def setUp(self) -> None:
        self.js = _CLIENT_JS_PATH.read_text(encoding="utf-8")

    def test_build_doc_card_foreach_params_are_within_whitelist(self) -> None:
        fn = self.js[self.js.index("function buildDocCard(rows, query) {"):]
        fn = fn[: fn.index("\n  }\n") + 4]
        params = re.findall(r"\.forEach\(function \((\w+)\)", fn)
        self.assertTrue(params, "buildDocCard 의 forEach 콜백 파라미터를 찾지 못함")
        for name in params:
            self.assertIn(name, _RECEIVER_NAMES)

    def test_build_doc_head_rows_zero_assignment_is_within_whitelist(self) -> None:
        fn = self.js[self.js.index("function buildDocHead(rows) {"):]
        fn = fn[: fn.index("\n  }\n") + 4]
        match = re.search(r"var (\w+) = rows\[0\];", fn)
        self.assertIsNotNone(match, "buildDocHead 의 rows[0] 대입 변수를 찾지 못함")
        self.assertIn(match.group(1), _RECEIVER_NAMES)

    def test_build_card_param_name_is_within_whitelist(self) -> None:
        match = re.search(r"function buildCard\((\w+), query\)", self.js)
        self.assertIsNotNone(match, "buildCard 의 첫 파라미터명을 찾지 못함")
        self.assertIn(match.group(1), _RECEIVER_NAMES)


class MutationSelfVerificationTest(unittest.TestCase):
    """③뮤테이션 자기검증 -- Codex 의 우회 시나리오(`row.confidence` -> `row["confidence"]`)
    를 findings.js 를 실제로 바꾸지 않고 **문자열 사본**에서 재현해, 위 ①②가 "말로만"
    방어가 아니라 실제로 발화하는지 실행으로 증명한다."""

    def setUp(self) -> None:
        self.original_js = _CLIENT_JS_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "row.confidence", self.original_js,
            "전제 조건 불충족: findings.js 가 row.confidence 를 dot-access 로 읽고 있어야 "
            "이 뮤테이션 시나리오가 성립한다(전제가 깨졌으면 findings.js 구조가 바뀐 것).",
        )
        # Codex 실증 그대로: dot-access 를 bracket-access 로 치환한 가상 소스(사본 -- 파일은 불변).
        self.mutated_js = self.original_js.replace("row.confidence", 'row["confidence"]')
        self.assertNotEqual(self.mutated_js, self.original_js, "치환이 실제로 일어나지 않았음")

    def test_mutation_makes_confidence_vanish_from_dot_access_parse(self) -> None:
        """파서(dot-access 전용)의 진짜 사각지대 실증 -- 뮤테이션 후 confidence 가
        파싱 결과에서 조용히 빠진다(투영 가드가 죽은 채로 green 이 될 수 있었던 이유)."""
        original_fields = _parse_fields_from_source(self.original_js)
        mutated_fields = _parse_fields_from_source(self.mutated_js)
        self.assertIn("confidence", original_fields)
        self.assertNotIn("confidence", mutated_fields)

    def test_bracket_access_ban_detects_the_mutation(self) -> None:
        """①bracket-access 금지 계약이 이 사본에서 실제로 실패를 감지해야 한다 --
        원본에서는 위반 0건, 뮤테이션 후에는 confidence 위반이 잡혀야 한다(가드가
        Codex 의 우회 시나리오를 실제로 막는다는 증거)."""
        self.assertEqual(_bracket_access_violations(self.original_js), [])
        violations = _bracket_access_violations(self.mutated_js)
        self.assertIn("confidence", violations)


# ============================================================================
# 030_findings_search_hardening.sql -- Major 1(무검색 랜딩 temp spill)+Minor 1(검색
# semantics 표류)+Minor 2(p_page int overflow) 수리(Codex 통합 정밀점검 2026-07-16).
# findings_search 만 재선언한다 -- findings_document 는 028 정의가 현행 그대로다(이
# 파일의 결함 3종 모두 페이지네이션·blob·집계 관련이라 findings_document 와 무관).
# ============================================================================


class HardeningMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_HARDENING_PATH.is_file(), f"missing {_HARDENING_PATH}")
        self.sql = _HARDENING_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        # ★골든/마이그레이션 CRLF 함정(과거 전례) -- LF 고정.
        self.assertNotIn(b"\r\n", _HARDENING_PATH.read_bytes())

    def test_redeclares_only_findings_search(self) -> None:
        self.assertIn("create or replace function public.findings_search(", self.code)
        self.assertNotIn("create or replace function public.findings_document(", self.code)


class HardeningWorkMemTest(unittest.TestCase):
    """Major 1 수리: `set work_mem = '8MB'` -- 없으면 CTE materialize(≈3MiB)가 인스턴스
    기본 2184kB 를 넘겨 temp 스필한다. 실측: 스필 시 anon temp read=4095(웜 ~127ms),
    work_mem 8MB 적용 후 스필 완전 소멸(shared hit 만, 웜 ~119ms). 027 이 대시보드 축
    (dash_* 4개 = filtered CTE 다중 소비)을 얹은 뒤 재측정을 빠뜨린 것이 026b 스필-0
    검증과 지금 실측이 어긋난 원인이다."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(_HARDENING_PATH.read_text(encoding="utf-8"))
        self.fn_search = _slice_function(self.code, _FN_SEARCH_SIG)

    def test_sets_work_mem_to_8mb(self) -> None:
        self.assertIn("set work_mem = '8MB'", self.fn_search)


class HardeningPageClampTest(unittest.TestCase):
    """Minor 2 수리: p_page 상한 클램프 400,000 -- p_page=2147483647 입력 시
    (page-1)*per 의 int 연산이 22003(integer out of range)으로 HTTP 400 이 되던 결함.
    400,000 × per 최대 100 = 4천만 < 2^31 로 overflow 를 원천 차단하면서 실 코퍼스
    (~1,400 페이지)의 280배 여유를 남긴다. 범위 밖 페이지는 종전처럼 빈 documents."""

    def test_page_is_clamped_between_one_and_four_hundred_thousand(self) -> None:
        code = _strip_sql_comments(_HARDENING_PATH.read_text(encoding="utf-8"))
        self.assertIn("least(greatest(coalesce(p_page, 1), 1), 400000)", code)


class HardeningBlobRefsElementsOnlyTest(unittest.TestCase):
    """Minor 1① 수리(확대 결함): blob 이 `cfr_refs::text`/`mfds_refs::text` 를 실으면
    JSON 구두점 자체가 검색 대상이 된다 -- `[]` 질의가 빈 배열 리터럴과 매치해 8,168건
    전량과 매치하던 결함(실측). 수리 = jsonb_array_elements_text 로 배열 **원소만**
    추출(빈 배열은 '' -- 종전 클라이언트 join 과 동치). blob 이 refs 컬럼을 통째로
    ::text 캐스팅하지 않는다는 것과, 원소 추출 함수를 쓴다는 것을 함께 고정한다."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_HARDENING_PATH.read_text(encoding="utf-8"))
        self.blob_block = _slice_between(code, "searched as (", "filtered as (")

    def test_does_not_cast_refs_columns_to_text_directly(self) -> None:
        self.assertNotIn("f.cfr_refs::text", self.blob_block)
        self.assertNotIn("f.mfds_refs::text", self.blob_block)

    def test_uses_jsonb_array_elements_text_for_both_refs(self) -> None:
        self.assertIn("jsonb_array_elements_text(f.cfr_refs)", self.blob_block)
        self.assertIn("jsonb_array_elements_text(f.mfds_refs)", self.blob_block)


class HardeningReviewStatusSpaceVariantTest(unittest.TestCase):
    """Minor 1② 수리(축소 결함): blob 에 `review_status` 의 '_'→' ' 표기 변형이 빠져
    "needs review"(공백) 검색이 42건 매치하던 종전 클라이언트 semantics 가 축소돼
    있었다(실측). `replace(coalesce(f.review_status, ''), '_', ' ')` 복원으로
    되살린다(원값 review_status 도 그대로 유지 -- 밑줄 표기 질의도 계속 매치)."""

    def test_blob_includes_review_status_space_variant(self) -> None:
        code = _strip_sql_comments(_HARDENING_PATH.read_text(encoding="utf-8"))
        self.assertIn("replace(coalesce(f.review_status, ''), '_', ' ')", code)


class HardeningBlobFieldOrderTest(unittest.TestCase):
    """Minor 1④ 수리: blob 필드 순서를 종전 searchTermsFor 순서(finding_text 가
    finding_text_ko 보다 선두)로 재정렬한다. blob 은 공백 결합이라 필드 경계를 넘는
    우연 매치(cross-field)가 존재하는데(종전부터 그랬음), 순서가 다르면 우연 매치의
    **집합이 달라진다**(실측: "Baxalta US Inc. documentation_records" 종전 0건 vs
    순서가 바뀐 상태에서 1건 -- firm_name 뒤 필드가 document_id 에서 category_code 로
    바뀐 탓). 순서 자체를 position 비교로 고정해 이 표류를 막는다."""

    def test_finding_text_precedes_finding_text_ko_in_blob(self) -> None:
        code = _strip_sql_comments(_HARDENING_PATH.read_text(encoding="utf-8"))
        block = _slice_between(code, "searched as (", "filtered as (")
        self.assertLess(
            block.index("f.finding_text,"),
            block.index("f.finding_text_ko,"),
            msg="blob 에서 finding_text 가 finding_text_ko 보다 앞에 와야 한다(종전 순서 승계).",
        )


class HardeningCarryoverInvariantsTest(unittest.TestCase):
    """026~028 불가침 계약이 030(findings_search 를 통째로 재선언하는 supersede 파일)
    에서도 승계되는지 030 파일 자체를 대상으로 재검사한다 -- create or replace 재선언은
    구조적으로 계약을 조용히 되돌릴 수 있는 지점이다. 승계 대상: security invoker +
    search_path 고정 · 게이트 술어 비복제(RLS 010 이 유일한 게이트) · 좁은 searched CTE
    (select 목록에 finding_text 계열 없음 -- WHERE 절 blob 은 예외) · d.tie asc 최종
    타이브레이크 · firm_asc ko-KR-x-icu collate · ILIKE 전용(FTS 함수 부재) ·
    grant execute to anon, authenticated."""

    def setUp(self) -> None:
        self.assertTrue(_HARDENING_PATH.is_file(), f"missing {_HARDENING_PATH}")
        self.sql = _HARDENING_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        self.fn_search = _slice_function(self.code, _FN_SEARCH_SIG)

    def test_is_security_invoker_and_pins_search_path(self) -> None:
        self.assertIn("security invoker", self.fn_search)
        self.assertNotIn("security definer", self.fn_search)
        self.assertIn("set search_path = public", self.fn_search)

    def test_gate_predicates_absent_from_function_body(self) -> None:
        self.assertNotIn("scope_status", self.fn_search)
        self.assertNotIn("finding_language = 'KO'", self.fn_search)

    def test_searched_cte_select_list_omits_wide_text_columns(self) -> None:
        block = _slice_between(self.code, "searched as (", "filtered as (")
        select_list = _slice_between(block, "select", "from public.findings f")
        for col in ("finding_text", "finding_text_ko"):
            self.assertNotIn(f"f.{col}", select_list)

    def test_ordered_cte_has_final_tiebreak(self) -> None:
        ordered_block = _slice_between(self.code, "ordered as (", "tot as (")
        self.assertIn("d.tie asc", ordered_block)

    def test_firm_asc_uses_icu_collation(self) -> None:
        ordered_block = _slice_between(self.code, "ordered as (", "tot as (")
        self.assertIn(
            '(case when p.sort = \'firm_asc\' then d.firm end) collate "ko-KR-x-icu" asc',
            ordered_block,
        )

    def test_uses_ilike_not_fts(self) -> None:
        self.assertIn("ilike", self.code)
        for forbidden in ("to_tsvector", "websearch_to_tsquery", "to_tsquery"):
            self.assertNotIn(forbidden, self.code, f"030 must not use FTS function: {forbidden!r}")

    def test_grants_execute_to_anon_and_authenticated(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_search(text, text, text, text, text, "
            "text, text, text, int, int) to anon, authenticated;",
            self.sql,
        )


class HardeningProjectionCoversClientFieldsTest(unittest.TestCase):
    """★투영 패리티 가드(ProjectionCoversClientFieldsTest) 확장 -- 그 클래스는 028 을 대상
    으로 검사하는데, 030 이 findings_search 를 다시 supersede 해 **현행 정본은 030**이다.
    028 검사는 역사 기록으로 그대로 두고(그 시점 결함의 증거), 030 을 대상으로 같은 교차
    검증(클라이언트가 실제로 읽는 필드가 findings[] 투영에 전부 실리는가)을 추가한다.
    findings_document 는 030 이 재선언하지 않으므로(028 정의가 현행 그대로) 검사 대상에서
    제외한다(위 HardeningMigrationFileTest.test_redeclares_only_findings_search 참조)."""

    def setUp(self) -> None:
        code = _strip_sql_comments(_HARDENING_PATH.read_text(encoding="utf-8"))
        self.search_rows = _slice_between(code, "page_docs_full as (", "fac_source as (")
        self.expected = [f for f in _parse_client_fields() if f not in _FIELDS_NOT_PROJECTED]

    def test_findings_search_projects_every_client_field(self) -> None:
        for col in self.expected:
            self.assertIn(
                f"'{col}',",
                self.search_rows,
                msg=f"030 findings_search 의 findings[] 투영에 '{col}' 이 없다 -- 클라이언트가 "
                    f"실제로 읽는 필드다(조용히 소실되면 방어적 분기라 크래시 없이 링크·고지· "
                    f"신뢰도 등이 사라진다).",
            )

    def test_restored_by_028_still_present_in_030(self) -> None:
        for col in _RESTORED_BY_028:
            self.assertIn(f"'{col}',", self.search_rows, msg=f"030: {col}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
