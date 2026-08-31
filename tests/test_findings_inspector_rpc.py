#!/usr/bin/env python3
"""FIND-483-SIGNER 2단계 -- 037_findings_inspector_profile.sql 정적 계약 tests.

오프라인 소스텍스트 검사만(실 네트워크·실 Postgres 없음) -- test_findings_search_rpc.py 의
정본 패턴(comment 제거 code 위에서 문자열/구조 계약을 고정하고, 필요하면 함수 정의를
부분 슬라이스해 스코프를 좁힌다)을 그대로 따른다. `_strip_sql_comments`·`_slice_function`·
`_slice_between` 은 그 파일에서 그대로 복제했다(레포 관례 -- 이 tests 디렉터리엔
`__init__.py` 가 없고, 기존 15개 findings 계열 테스트 파일 전부가 헬퍼를 각자 복제해
쓰지 서로 import 하지 않는다. 새로 import 경로를 여는 대신 관례를 따른다).

037 이 고정하는 계약:
  ①파일 위생 -- CRLF 없음·주석 밀도·세 함수 모두 create or replace function.
  ②★코호트 임계값 단일성(가장 중요) -- findings_inspector_index() 와
    findings_inspector_profile() 이 **같은 숫자**를 코호트 게이트로 써야 한다. 어긋나면
    "인덱스엔 있는데 열면 null"(또는 반대) 이 되어 딥링크가 깨진다. 두 함수에서 각각
    비교 연산자+숫자를 정규식으로 뽑아 상호 비교한다 -- ★2026-07-30 성능 리팩터로 index
    쪽이 `having count(distinct rid) >= 5` 에서 `where d.documents >= 5` 로 바뀌었으므로,
    "having" 이나 "count(distinct...)" 형태에 결합하지 않고 각 함수 슬라이스 안의
    **유일한** `>=`/`<` + 정수 비교를 뽑는 일반형 정규식을 쓴다(실측: 두 함수 각각에
    이 비교가 정확히 1건씩만 존재 -- `<>` 부정 연산자는 뒤에 숫자가 오지 않아 오탐 없음).
  ③게이트가 RPC 안에 있다 -- profile 본문이 코호트 미달 시 `'null'::jsonb` 로 수렴하는
    분기를 가져야 한다(UI 가 아니라 서버가 거부해야 딥링크로 "1건짜리 빈 프로파일"이
    존재하지 않는다).
  ④안전 계약 -- search_path 고정(3함수) · index/profile 은 security definer · 공개
    게이트 술어(scope_status='ok' 양쪽, finding_text_ko<>''/finding_language='KO' 는
    profile 의 public_* 카운트에만) · 483 한정 · 원문·URL 무반환(jsonb_build_object 키
    목록을 허용목록과 대조) · grant 는 index/profile 에만(findings_inspector_key 는 내부
    전용이라 미부여).
  ⑤결정론 -- display_name 타이브레이크 3단(count(*) desc, length(nm) desc, nm asc)이
    두 함수 모두에 있어야 한다. ★index 쪽은 위 성능 리팩터로 `order by ... limit 1`
    상관 서브쿼리에서 `row_number() over (partition by k order by ...)` 윈도우 함수로
    옮겨갔다 -- 이 테스트는 감싸는 구조(상관 서브쿼리 vs 윈도우 함수)가 아니라 "3단
    타이브레이크가 존재하는가"만 본다(리팩터마다 깨지는 테스트는 가치가 낮다는 코디네이터
    피드백 반영).
  ⑥범위 제한 문서화 -- 헤더가 008 의 "조사관별 집계는 범위가 아니다" 주석을 개정한다는
    사실, 순위·비교·엄격도 추론을 하지 않는다는 것, 실사관 디렉터리 페이지를 만들지
    않는다는 것을 명시하는지(원문 그대로 검사 -- comment-strip 하면 사라진다).

⑦마이그레이션 번호 연속성은 이 파일에 넣지 않는다 -- test_findings_search_rpc.py 의
  MigrationNumberSequenceTest.test_migration_numbers_are_contiguous_no_gaps 가 이미
  `web/migrations/*.sql` 을 glob 해 001~최댓값 결번을 동적으로 검사하므로(037 를
  하드코딩하지 않음) 037 추가만으로 그 테스트가 자동으로 037 까지 커버한다. 여기서
  중복 검사를 넣으면 두 파일이 같은 사실을 두 번 주장하게 된다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_PROFILE_PATH = _MIGRATIONS_DIR / "037_findings_inspector_profile.sql"

_FN_KEY_SIG = "create or replace function public.findings_inspector_key(p_name text)\n"
_FN_INDEX_SIG = "create or replace function public.findings_inspector_index()\n"
_FN_PROFILE_SIG = (
    "create or replace function public.findings_inspector_profile(p_inspector_key text)\n"
)

# jsonb_build_object 키는 이 파일 전체에서 예외 없이 `'키이름',` 형태(작은따옴표 +
# 스네이크케이스 + 즉시 콤마)로만 등장한다(값 쪽 문자열 리터럴은 전부 빈 문자열이거나
# 공백/숫자/대문자를 포함해 이 패턴에 걸리지 않음 -- 037 원문 실측 확인).
_JSONB_KEY_RE = re.compile(r"'([a-z_]+)',")

# ④원문·URL 무반환 계약 -- 007/008 안전 계약이 세운 유일한 표면(jsonb 키 이름)에서
# 이 다섯 키가 나오면 안 된다.
_FORBIDDEN_KEYS = {"finding_text", "finding_text_ko", "evidence_url", "raw_json", "row_json"}

_INDEX_ALLOWED_KEYS = {"inspector_key", "display_name", "documents"}

_PROFILE_ALLOWED_KEYS = {
    "inspector_key", "display_name", "totals", "by_category", "by_year", "documents",
    "findings", "public_findings", "firms", "first_seen", "last_seen",
    "category_code", "cnt", "year",
    "raw_signal_id", "published_date", "source", "firm_name", "firm_key",
    "obs_cnt", "public_obs_cnt",
}

# ②코호트 게이트 비교(연산자 + 정수)를 뽑는 일반형 정규식 -- "having"/"count(distinct...)"
# 같은 특정 구조에 결합하지 않는다. `<>`(부정 연산자)는 뒤에 공백/숫자가 오지 않으므로
# 오탐하지 않는다(037 원문 실측: `k <> ''`, `inspector_names <> '[]'::jsonb`,
# `finding_text_ko <> ''` 전부 뒤가 따옴표라 매치 안 됨).
_THRESHOLD_RE = re.compile(r"(>=|<)\s*(\d+)\b")

# ── 039_findings_inspector_alias.sql -- 별칭 병합 계약 ──────────────────────
_ALIAS_PATH = _MIGRATIONS_DIR / "039_findings_inspector_alias.sql"

_FN_PAIRS_SIG = "create or replace function public.findings_inspector_pairs()\n"

# 037 은 index/profile 양쪽에서 display_name 타이브레이크가 문자 그대로
# "count(*) desc, length(nm) desc, nm asc" 였다(각자 inline pairs CTE 가 원표기를 nm 으로
# 통일해서 별칭했기 때문). 039 는 공유 findings_inspector_pairs() 가 컬럼명 `raw_name` 을
# 돌려주는데, index 의 윈도우 함수(OVER ORDER BY)는 같은 SELECT 절에서 선언한 별칭 nm 을
# 참조할 수 없어 원컬럼 raw_name 을 그대로 쓴다 -- 반면 profile 은 자체 rows_out CTE 가
# 이미 `p.raw_name as nm` 으로 별칭을 씌운 뒤라 nm 을 쓴다. 그래서 037 처럼 리터럴 문자열
# 하나로 두 함수를 동시에 고정할 수 없다 -- "count(*) desc, length(X) desc, X asc" 형태
# (X 가 반복되는가)만 함수별로 독립 검사한다.
_TIEBREAK_SHAPE_RE = re.compile(r"count\(\*\) desc, length\((\w+)\) desc, \1 asc")


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


def _jsonb_keys(fn_body: str) -> set[str]:
    return set(_JSONB_KEY_RE.findall(fn_body))


class InspectorMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_PROFILE_PATH.is_file(), f"missing {_PROFILE_PATH}")
        self.sql = _PROFILE_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        # ★골든/마이그레이션 CRLF 함정(과거 전례) -- LF 고정.
        self.assertNotIn(b"\r\n", _PROFILE_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_defines_all_three_functions(self) -> None:
        for sig_prefix in (
            "create or replace function public.findings_inspector_key(",
            "create or replace function public.findings_inspector_index(",
            "create or replace function public.findings_inspector_profile(",
        ):
            self.assertIn(sig_prefix, self.code)


class InspectorFunctionSlicesTestBase(unittest.TestCase):
    """세 함수 슬라이스를 setUp 에서 공통 준비하는 베이스 -- 각 계약 테스트가 중복해서
    슬라이싱하지 않게 한다."""

    def setUp(self) -> None:
        self.sql = _PROFILE_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        self.fn_key = _slice_function(self.code, _FN_KEY_SIG)
        self.fn_index = _slice_function(self.code, _FN_INDEX_SIG)
        self.fn_profile = _slice_function(self.code, _FN_PROFILE_SIG)


class CohortThresholdSingleSourceOfTruthTest(InspectorFunctionSlicesTestBase):
    """★가장 중요한 계약: index 와 profile 이 같은 코호트 임계값을 써야 한다. 둘이
    어긋나면 "인덱스에는 있는데 열면 null"(또는 그 반대)이 되어 클라이언트가 건 링크가
    깨진다. 2026-07-30 성능 리팩터로 index 쪽 게이트 형태가
    `having count(distinct rid) >= 5` 에서 `where d.documents >= 5` 로 바뀌었으므로,
    이 테스트는 그 구조가 아니라 "각 함수 슬라이스 안에 유일하게 존재하는 `>=`/`<` +
    정수 비교"를 일반형 정규식으로 뽑아 비교한다."""

    def test_index_slice_has_exactly_one_threshold_comparison(self) -> None:
        matches = _THRESHOLD_RE.findall(self.fn_index)
        self.assertEqual(
            len(matches), 1,
            msg=f"findings_inspector_index 슬라이스에서 임계 비교가 1건이 아니다: {matches}",
        )

    def test_profile_slice_has_exactly_one_threshold_comparison(self) -> None:
        matches = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertEqual(
            len(matches), 1,
            msg=f"findings_inspector_profile 슬라이스에서 임계 비교가 1건이 아니다: {matches}",
        )

    def test_thresholds_are_numerically_identical(self) -> None:
        (_, idx_n), = _THRESHOLD_RE.findall(self.fn_index)
        (_, prof_n), = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertEqual(
            idx_n, prof_n,
            msg=(
                f"index 임계값({idx_n}) != profile 임계값({prof_n}) -- 코호트 게이트가 "
                "두 함수에서 어긋나면 링크(인덱스에 있음/열면 null)가 깨진다."
            ),
        )

    def test_index_gate_is_inclusive_and_profile_gate_is_exclusive_complement(self) -> None:
        # index 는 "포함"(>=), profile 은 "미달 시 제외"(<)를 표현한다 -- 같은 코호트
        # 경계의 서로 다른(그러나 논리적으로 상보적인) 표현이어야 한다.
        (idx_op, _), = _THRESHOLD_RE.findall(self.fn_index)
        (prof_op, _), = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertEqual(idx_op, ">=")
        self.assertEqual(prof_op, "<")

    def test_threshold_value_is_five_per_header_distribution(self) -> None:
        # 헤더 주석의 실측 분포("5건 이상 99명")와 일치하는지 -- 어긋나면 문서와 코드가
        # 따로 논다.
        (_, idx_n), = _THRESHOLD_RE.findall(self.fn_index)
        self.assertEqual(idx_n, "5")


class ProfileNullGateTest(InspectorFunctionSlicesTestBase):
    """③게이트가 RPC 안에 있다 -- 코호트 미달이면 profile 이 `'null'::jsonb` 로 수렴해야
    한다. UI 게이트는 딥링크로 우회되므로 서버가 거부해야 "1건짜리 빈 프로파일"이 원천
    적으로 존재하지 않는다(037 헤더 근거)."""

    def test_null_jsonb_literal_present(self) -> None:
        self.assertIn("'null'::jsonb", self.fn_profile)

    def test_null_branch_is_gated_by_the_same_extracted_threshold(self) -> None:
        # 위 CohortThresholdSingleSourceOfTruthTest 가 뽑은 것과 같은 숫자를 재사용해,
        # 그 비교 바로 뒤에 null 분기가 붙어 있는지(게이트와 분기가 분리돼 있지 않은지)
        # 확인한다.
        (op, n), = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertIn(f"{op} {n} then 'null'::jsonb", self.fn_profile)


class SafetyContractTest(InspectorFunctionSlicesTestBase):
    """④안전 계약 -- search_path 고정·definer 범위·공개 게이트 술어·483 한정·원문/URL
    무반환·grant 범위. 007/008/013 이 세운 계약을 037 이 조용히 무너뜨리지 않는지 고정."""

    def test_all_three_functions_pin_search_path(self) -> None:
        for fn in (self.fn_key, self.fn_index, self.fn_profile):
            self.assertIn("set search_path = public", fn)

    def test_index_and_profile_are_security_definer(self) -> None:
        for fn in (self.fn_index, self.fn_profile):
            self.assertIn("security definer", fn)

    def test_key_function_does_not_declare_security_definer(self) -> None:
        # findings_inspector_key 는 두 definer 함수가 내부에서만 호출하는 무상태 정규화
        # 함수라 definer 승격이 필요 없다(불필요한 권한 확대 방지).
        self.assertNotIn("security definer", self.fn_key)

    def test_scope_status_ok_gate_present_in_index_and_profile(self) -> None:
        for fn in (self.fn_index, self.fn_profile):
            self.assertIn("scope_status = 'ok'", fn)

    def test_public_predicate_present_exactly_twice_in_profile_public_counts(self) -> None:
        # public_findings 카운트와 public_obs_cnt(문서별 공개 관측 수) 두 곳에만 있어야
        # 한다 -- index 는 public/private 구분 없이 원시 건수만 돌려주므로 이 술어가 없다.
        occurrences = self.fn_profile.count("finding_text_ko <> '' or finding_language = 'KO'")
        self.assertEqual(occurrences, 2)
        self.assertNotIn("finding_text_ko <> ''", self.fn_index)

    def test_fda483_scope_present_in_index_and_profile(self) -> None:
        for fn in (self.fn_index, self.fn_profile):
            self.assertIn("source = 'FDA 483'", fn)

    def test_no_raw_text_or_url_keys_in_index_projection(self) -> None:
        keys = _jsonb_keys(self.fn_index)
        leaked = keys & _FORBIDDEN_KEYS
        self.assertEqual(leaked, set(), msg=f"index 투영에 금지 키 유출: {leaked}")

    def test_no_raw_text_or_url_keys_in_profile_projection(self) -> None:
        keys = _jsonb_keys(self.fn_profile)
        leaked = keys & _FORBIDDEN_KEYS
        self.assertEqual(leaked, set(), msg=f"profile 투영에 금지 키 유출: {leaked}")

    def test_index_projection_keys_within_declared_allowlist(self) -> None:
        keys = _jsonb_keys(self.fn_index)
        self.assertTrue(keys, "index jsonb 키 파싱이 빈 목록 -- 가드가 공허하게 통과 중")
        extra = keys - _INDEX_ALLOWED_KEYS
        self.assertEqual(extra, set(), msg=f"index 가 허용목록 밖의 새 키를 반환: {extra}")

    def test_profile_projection_keys_within_declared_allowlist(self) -> None:
        keys = _jsonb_keys(self.fn_profile)
        self.assertTrue(keys, "profile jsonb 키 파싱이 빈 목록 -- 가드가 공허하게 통과 중")
        extra = keys - _PROFILE_ALLOWED_KEYS
        self.assertEqual(extra, set(), msg=f"profile 이 허용목록 밖의 새 키를 반환: {extra}")

    def test_grant_execute_present_for_index_and_profile_only(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_inspector_index() to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_inspector_profile(text) "
            "to anon, authenticated;",
            self.sql,
        )

    def test_findings_inspector_key_is_not_granted(self) -> None:
        # 내부 전용 -- 두 definer 함수만 이 함수를 호출하므로 별도 grant 가 필요 없다.
        self.assertNotIn("grant execute on function public.findings_inspector_key", self.sql)


class DisplayNameDeterminismTest(InspectorFunctionSlicesTestBase):
    """⑤display_name 선택에 3단 결정론 타이브레이크(최빈 -> 최장 표기 -> 사전순)가 있어야
    한다 -- 동률에서 실행마다 다른 표기가 나오면 링크 라벨이 흔들린다(이 저장소엔 타이브
    레이크 부재로 A/B 평가가 뒤집힌 전례가 037 헤더에 명시돼 있다).

    ★2026-07-30 성능 리팩터로 index 쪽이 코호트 1명마다 pairs 를 재훑는 상관 서브쿼리에서
    row_number() over (partition by k order by ...) 윈도우 함수 단일 패스로 바뀌었다.
    이 테스트는 감싸는 절(상관 서브쿼리의 `order by ... limit 1` vs 윈도우 함수의
    `row_number() over (... order by ...)`)이 아니라 "3단 타이브레이크 문자열이 존재
    하는가"만 검사한다 -- 구조 리팩터마다 깨지는 테스트는 가치가 낮다."""

    _TIEBREAK = "count(*) desc, length(nm) desc, nm asc"

    def test_index_has_three_stage_tiebreak(self) -> None:
        self.assertIn(self._TIEBREAK, self.fn_index)

    def test_profile_has_three_stage_tiebreak(self) -> None:
        self.assertIn(self._TIEBREAK, self.fn_profile)


class ScopeLimitationsDocumentedTest(unittest.TestCase):
    """⑥범위 제한이 문서(원문 주석)에서 사라지지 않게 고정한다 -- "왜 이렇게 좁게
    만들었는가"가 코드에서 유실되면 다음 사람이 코호트 게이트나 범위 제한을 조용히
    되돌리기 쉽다. comment-strip **하지 않은** 원문(raw self.sql)으로 검사한다."""

    def setUp(self) -> None:
        self.sql = _PROFILE_PATH.read_text(encoding="utf-8")

    def test_header_declares_008_scope_note_amended(self) -> None:
        self.assertIn(
            "조사관(inspector)별 집계는 데이터 부재로 이번 범위가 아니다", self.sql
        )

    def test_header_declares_no_ranking_or_severity_inference(self) -> None:
        self.assertIn("순위·비교·엄격도 추론", self.sql)

    def test_header_declares_no_directory_listing_page(self) -> None:
        self.assertIn("디렉터리(목록 열람) 페이지를 만들지 않는다", self.sql)


# ============================================================================
# 039_findings_inspector_alias.sql -- 실사관 정체성에 모호하지 않은 별칭 병합.
#
# findings_inspector_index/findings_inspector_profile 을 다시 supersede 하고, 새로
# findings_inspector_pairs() 를 도입해 index/profile 이 **그 함수 하나만** 쓰게 한다
# (각자 alias/parts CTE 를 복제하면 한쪽만 바뀌는 표류가 생긴다 -- 이 저장소가 수동
# 허용목록 표류로 두 번 당한 것과 같은 계열, MEMORY 참조). findings_inspector_key 는
# 037 정의 그대로라 이 파일이 재선언하지 않는다 -- 재선언하면 037 의 정규화가 조용히
# 바뀔 수 있어 여기서도 슬라이스하지 않는다(037 의 테스트가 이미 그 함수를 고정한다).
# ============================================================================


class AliasFunctionSlicesTestBase(unittest.TestCase):
    """세 함수(pairs/index/profile) 슬라이스를 setUp 에서 공통 준비하는 베이스 --
    findings_inspector_key 는 039 가 재선언하지 않으므로 여기서 슬라이스하지 않는다."""

    def setUp(self) -> None:
        self.assertTrue(_ALIAS_PATH.is_file(), f"missing {_ALIAS_PATH}")
        self.sql = _ALIAS_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        self.fn_pairs = _slice_function(self.code, _FN_PAIRS_SIG)
        self.fn_index = _slice_function(self.code, _FN_INDEX_SIG)
        self.fn_profile = _slice_function(self.code, _FN_PROFILE_SIG)


class AliasMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_ALIAS_PATH.is_file(), f"missing {_ALIAS_PATH}")
        self.sql = _ALIAS_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        # ★골든/마이그레이션 CRLF 함정(과거 전례) -- LF 고정.
        self.assertNotIn(b"\r\n", _ALIAS_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_defines_pairs_index_and_profile(self) -> None:
        for sig_prefix in (
            "create or replace function public.findings_inspector_pairs(",
            "create or replace function public.findings_inspector_index(",
            "create or replace function public.findings_inspector_profile(",
        ):
            self.assertIn(sig_prefix, self.code)

    def test_does_not_redeclare_findings_inspector_key(self) -> None:
        # 037 정의가 그대로 현행이어야 한다 -- 여기서 재선언하면 037 의 정규화 규칙이
        # 조용히 바뀔 수 있다(039 헤더 근거).
        self.assertNotIn(
            "create or replace function public.findings_inspector_key(", self.code
        )


class AliasSingleSourceOfTruthTest(AliasFunctionSlicesTestBase):
    """★단일 정본 -- index 와 profile 이 각자 CTE 를 복제하지 않고 둘 다
    findings_inspector_pairs() 하나만 소비해야 한다. `alias`/`parts` 라는 CTE 이름 자체가
    pairs 함수 슬라이스 밖(index/profile)에 나타나면, 누군가 병합 로직을 복제해 넣었다는
    뜻이므로 표류의 시작이다(037→039 배경 주석 근거)."""

    def test_index_consumes_pairs_function(self) -> None:
        self.assertIn("public.findings_inspector_pairs()", self.fn_index)

    def test_profile_consumes_pairs_function(self) -> None:
        self.assertIn("public.findings_inspector_pairs()", self.fn_profile)

    def test_alias_cte_exists_only_inside_pairs_slice(self) -> None:
        self.assertIn("alias as (", self.fn_pairs)
        self.assertNotIn("alias as (", self.fn_index)
        self.assertNotIn("alias as (", self.fn_profile)

    def test_parts_cte_exists_only_inside_pairs_slice(self) -> None:
        self.assertIn("parts as (", self.fn_pairs)
        self.assertNotIn("parts as (", self.fn_index)
        self.assertNotIn("parts as (", self.fn_profile)


class AliasAmbiguityGuardTest(AliasFunctionSlicesTestBase):
    """★모호성 가드(가장 중요) -- 2토큰 이름은 같은 first/last 를 가진 3토큰 이상 후보가
    **정확히 1개**일 때만 흡수한다(`count(*) ... = 1`). 이 비교가 빠지거나 `>= 1`(1개
    이상)로 완화되면 후보가 2개 이상인 동명이인 케이스까지 병합돼 남의 실사 이력이
    붙는다 -- 이 테스트가 그 회귀를 막는 유일한 장치다(임무 지시서 근거)."""

    _GUARD_RE = re.compile(
        r"count\(\*\) from parts l\s+where l\.ntok >= 3 and l\.first_tok = s\.first_tok "
        r"and l\.last_tok = s\.last_tok\)\s*=\s*1"
    )

    def test_exactly_one_candidate_guard_present_in_pairs(self) -> None:
        self.assertRegex(self.fn_pairs, self._GUARD_RE)

    def test_guard_is_not_a_looser_at_least_one_comparison(self) -> None:
        # `>= 1` 로 완화되면 후보 2개 이상(동명이인)도 병합 대상이 된다 -- 정확히 이
        # 퇴행을 잡는다.
        self.assertNotIn(
            "l.last_tok = s.last_tok) >= 1",
            self.fn_pairs,
            msg=(
                "모호성 가드가 '정확히 1개'(= 1)가 아니라 '1개 이상'(>= 1)으로 완화됐다 "
                "-- 동명이인이 병합될 수 있다."
            ),
        )


class AliasCohortThresholdSingleSourceOfTruthTest(AliasFunctionSlicesTestBase):
    """037 의 CohortThresholdSingleSourceOfTruthTest 와 동일한 계약을 039 재선언본에 다시
    건다 -- index/profile 이 같은 코호트 임계값(5)을 써야 "인덱스엔 있는데 열면 null"
    (또는 그 반대) 딥링크 불일치가 재발하지 않는다."""

    def test_index_slice_has_exactly_one_threshold_comparison(self) -> None:
        matches = _THRESHOLD_RE.findall(self.fn_index)
        self.assertEqual(
            len(matches), 1, msg=f"index 슬라이스에서 임계 비교가 1건이 아니다: {matches}"
        )

    def test_profile_slice_has_exactly_one_threshold_comparison(self) -> None:
        matches = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertEqual(
            len(matches), 1, msg=f"profile 슬라이스에서 임계 비교가 1건이 아니다: {matches}"
        )

    def test_thresholds_are_numerically_identical(self) -> None:
        (_, idx_n), = _THRESHOLD_RE.findall(self.fn_index)
        (_, prof_n), = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertEqual(
            idx_n, prof_n,
            msg=f"index 임계값({idx_n}) != profile 임계값({prof_n})",
        )

    def test_threshold_value_is_five(self) -> None:
        (_, idx_n), = _THRESHOLD_RE.findall(self.fn_index)
        self.assertEqual(idx_n, "5")

    def test_index_gate_is_inclusive_and_profile_gate_is_exclusive_complement(self) -> None:
        (idx_op, _), = _THRESHOLD_RE.findall(self.fn_index)
        (prof_op, _), = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertEqual(idx_op, ">=")
        self.assertEqual(prof_op, "<")


class AliasProfileNullGateTest(AliasFunctionSlicesTestBase):
    """게이트가 RPC 안에 있다 -- 코호트 미달이면 profile 이 `'null'::jsonb` 로 수렴해야
    한다(037 과 동일 계약, 039 재선언본에서도 승계 확인)."""

    def test_null_jsonb_literal_present(self) -> None:
        self.assertIn("'null'::jsonb", self.fn_profile)

    def test_null_branch_is_gated_by_the_same_extracted_threshold(self) -> None:
        (op, n), = _THRESHOLD_RE.findall(self.fn_profile)
        self.assertIn(f"{op} {n} then 'null'::jsonb", self.fn_profile)


class AliasSafetyContractTest(AliasFunctionSlicesTestBase):
    """037 과 불변인 안전 계약이 039 에서도 승계되는지 -- search_path 고정(3함수) ·
    index/profile security definer · scope_status/source 게이트가 pairs·profile 에
    존재(index 는 pairs() 호출을 통해 상속받으므로 텍스트로 복제하지 않는다 -- 복제하면
    위 단일 정본 계약 위반의 신호다) · 원문/URL 무반환(허용·금지 목록 양방향) · grant 범위."""

    def test_all_three_functions_pin_search_path(self) -> None:
        for fn in (self.fn_pairs, self.fn_index, self.fn_profile):
            self.assertIn("set search_path = public", fn)

    def test_index_and_profile_are_security_definer(self) -> None:
        for fn in (self.fn_index, self.fn_profile):
            self.assertIn("security definer", fn)

    def test_pairs_is_also_security_definer(self) -> None:
        # 037 의 findings_inspector_key(무상태 정규화 헬퍼)와 달리, pairs 는 findings
        # 원장을 직접 읽는 정체성 정본 함수라 definer 로 선언돼 있다(039 원문 실측).
        self.assertIn("security definer", self.fn_pairs)

    def test_scope_status_and_source_gate_present_in_pairs_and_profile(self) -> None:
        for fn in (self.fn_pairs, self.fn_profile):
            self.assertIn("scope_status = 'ok'", fn)
            self.assertIn("source = 'FDA 483'", fn)

    def test_index_does_not_restate_scope_gate(self) -> None:
        # index 는 pairs() 를 통해서만 게이트를 상속한다 -- 여기 다시 나타나면 CTE 복제의
        # 신호(단일 정본 위반)다.
        self.assertNotIn("scope_status", self.fn_index)
        self.assertNotIn("source = 'FDA 483'", self.fn_index)

    def test_no_raw_text_or_url_keys_in_index_projection(self) -> None:
        keys = _jsonb_keys(self.fn_index)
        leaked = keys & _FORBIDDEN_KEYS
        self.assertEqual(leaked, set(), msg=f"index 투영에 금지 키 유출: {leaked}")

    def test_no_raw_text_or_url_keys_in_profile_projection(self) -> None:
        keys = _jsonb_keys(self.fn_profile)
        leaked = keys & _FORBIDDEN_KEYS
        self.assertEqual(leaked, set(), msg=f"profile 투영에 금지 키 유출: {leaked}")

    def test_index_projection_keys_within_declared_allowlist(self) -> None:
        keys = _jsonb_keys(self.fn_index)
        self.assertTrue(keys, "index jsonb 키 파싱이 빈 목록 -- 가드가 공허하게 통과 중")
        extra = keys - _INDEX_ALLOWED_KEYS
        self.assertEqual(extra, set(), msg=f"index 가 허용목록 밖의 새 키를 반환: {extra}")

    def test_profile_projection_keys_within_declared_allowlist(self) -> None:
        keys = _jsonb_keys(self.fn_profile)
        self.assertTrue(keys, "profile jsonb 키 파싱이 빈 목록 -- 가드가 공허하게 통과 중")
        extra = keys - _PROFILE_ALLOWED_KEYS
        self.assertEqual(extra, set(), msg=f"profile 이 허용목록 밖의 새 키를 반환: {extra}")

    def test_grant_execute_present_for_pairs_index_and_profile(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_inspector_pairs() "
            "to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_inspector_index() "
            "to anon, authenticated;",
            self.sql,
        )
        self.assertIn(
            "grant execute on function public.findings_inspector_profile(text) "
            "to anon, authenticated;",
            self.sql,
        )

    def test_findings_inspector_key_is_not_granted_here(self) -> None:
        self.assertNotIn("grant execute on function public.findings_inspector_key", self.sql)


class AliasDisplayNameDeterminismTest(AliasFunctionSlicesTestBase):
    """display_name 선택에 3단 결정론 타이브레이크(최빈 -> 최장 표기 -> 사전순)가 두 함수
    모두에 있어야 한다. ★037 과 달리 039 는 index/profile 에서 타이브레이크가 참조하는
    컬럼명이 다르다(모듈 상단 `_TIEBREAK_SHAPE_RE` 주석에 구조적 이유 설명) -- 그래서
    리터럴 문자열 하나로 고정하지 않고 "count(*) desc, length(X) desc, X asc" 형태(X 가
    반복되는가)만 함수별로 독립 검사한다."""

    def test_index_has_three_stage_tiebreak_shape(self) -> None:
        self.assertRegex(self.fn_index, _TIEBREAK_SHAPE_RE)

    def test_profile_has_three_stage_tiebreak_shape(self) -> None:
        self.assertRegex(self.fn_profile, _TIEBREAK_SHAPE_RE)


class AliasInputResolutionBranchTest(AliasFunctionSlicesTestBase):
    """★입력 해소(링크 호환) -- `profile(p_inspector_key)` 는 **해소된 키든 병합 전 짧은
    표기든 양쪽 다** 같은 프로파일로 착지해야 한다. `inspector_key = q.qk` 와
    `findings_inspector_key(a.raw_name) = q.qk` 양쪽 분기가 하나의 `or` 로 묶여 있어야
    한다 -- 한쪽만 남으면 이미 배포된 링크 중 한 형태가 깨진다(039 헤더 근거)."""

    def test_both_resolution_branches_present(self) -> None:
        self.assertIn("a.inspector_key = q.qk", self.fn_profile)
        self.assertIn("public.findings_inspector_key(a.raw_name) = q.qk", self.fn_profile)

    def test_branches_are_joined_by_or(self) -> None:
        self.assertIn(
            "a.inspector_key = q.qk or public.findings_inspector_key(a.raw_name) = q.qk",
            self.fn_profile,
        )


# ============================================================================
# 070_findings_inspector_document_id.sql -- documents[] 에 document_id 순수 가산.
#
# 037/039 이후 065_profile_categories_and_repeats.sql 이 findings_inspector_profile 을
# 다시 재선언해(categories·repeats·years 추가) 현재 정본이 됐다 -- 065 자체는 이 tests
# 디렉터리에 오프라인 계약 파일이 없어(가장 최근 리팩터가 커버되지 않은 채로 있었다),
# 070 의 허용목록은 037 당시의 _PROFILE_ALLOWED_KEYS 가 아니라 **065 가 이미 반영한
# 현재 모양(그 세 키 포함)** 을 기준선으로 잡는다 -- 037 당시 허용목록으로 대조하면
# categories/repeats/years 가 전부 "허용목록 밖의 새 키"로 오탐된다(070 이 늘린 게
# 아닌데 070 탓처럼 보인다).
# ============================================================================

_DOCID_PATH = _MIGRATIONS_DIR / "070_findings_inspector_document_id.sql"

# 065 가 이미 확정한 모양(순수 가산 이력) -- 070 의 "새로 추가된 키"를 이 기준선과
# 비교해야 document_id **하나만** 늘었다는 걸 증명할 수 있다.
_PROFILE_KEYS_AFTER_065 = _PROFILE_ALLOWED_KEYS | {"categories", "repeats", "years"}


class DocumentIdMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_DOCID_PATH.is_file(), f"missing {_DOCID_PATH}")
        self.sql = _DOCID_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _DOCID_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 15)

    def test_defines_only_profile_function(self) -> None:
        self.assertIn(_FN_PROFILE_SIG, self.code)
        # ★범위 제한(070 헤더 근거) -- index/pairs/key 는 이 파일에서 재선언하지 않는다.
        # 재선언하면 037→039 가 경계한 "각자 CTE 복제로 표류" 가 재발한다.
        self.assertNotIn(
            "create or replace function public.findings_inspector_index(", self.code
        )
        self.assertNotIn(
            "create or replace function public.findings_inspector_pairs(", self.code
        )
        self.assertNotIn(
            "create or replace function public.findings_inspector_key(", self.code
        )


class DocumentIdFunctionSliceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_DOCID_PATH.is_file(), f"missing {_DOCID_PATH}")
        self.sql = _DOCID_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)
        self.fn_profile = _slice_function(self.code, _FN_PROFILE_SIG)


class DocumentIdPureAdditionTest(DocumentIdFunctionSliceTestBase):
    """★가장 중요한 계약: documents[] 에 document_id 하나만 늘고, 065 가 이미 확정한
    나머지 투영은 바이트 단위로 그대로여야 한다(임무 지시서 근거)."""

    def test_signature_unchanged(self) -> None:
        # PostgREST 는 인자가 하나만 달라도 404 -- 시그니처 불변이 이 계약의 전제(#681).
        self.assertIn(_FN_PROFILE_SIG, self.code)

    def test_document_id_selected_from_findings_in_rows_out(self) -> None:
        # rows_out CTE 가 findings.document_id 를 select 하지 않으면 바깥 집계가 참조할
        # 컬럼 자체가 없다 -- 배선의 첫 고리.
        self.assertIn("f.document_id, p.raw_name as nm", self.fn_profile)

    def test_document_id_aggregated_and_projected_in_documents_array(self) -> None:
        self.assertIn("max(document_id) as document_id", self.fn_profile)
        self.assertIn("'document_id',", self.fn_profile)

    def test_projection_keys_are_065_baseline_plus_document_id_only(self) -> None:
        keys = _jsonb_keys(self.fn_profile)
        self.assertTrue(keys, "070 profile jsonb 키 파싱이 빈 목록 -- 가드가 공허하게 통과 중")
        added = keys - _PROFILE_KEYS_AFTER_065
        self.assertEqual(
            added, {"document_id"},
            msg=f"070 이 document_id 외의 키를 늘렸다(순수 가산 위반): {added}",
        )
        missing = _PROFILE_KEYS_AFTER_065 - keys
        self.assertEqual(
            missing, set(),
            msg=f"070 이 065 가 이미 확정한 키를 잃었다(순수 가산 위반): {missing}",
        )

    def test_no_raw_text_or_url_keys(self) -> None:
        keys = _jsonb_keys(self.fn_profile)
        leaked = keys & _FORBIDDEN_KEYS
        self.assertEqual(leaked, set(), msg=f"070 profile 투영에 금지 키 유출: {leaked}")


class DocumentIdSafetyAndGateCarriedOverTest(DocumentIdFunctionSliceTestBase):
    """037/039/065 가 세운 안전 계약·코호트 게이트가 070 재선언에서도 그대로인지 --
    이 재선언이 로직을 다시 쓴 게 아니라 필드 하나만 얹은 것이라는 방증이기도 하다."""

    def test_security_definer_and_search_path(self) -> None:
        self.assertIn("security definer", self.fn_profile)
        self.assertIn("set search_path = public", self.fn_profile)

    def test_scope_and_source_gate_present(self) -> None:
        self.assertIn("scope_status = 'ok'", self.fn_profile)
        self.assertIn("source = 'FDA 483'", self.fn_profile)

    def test_cohort_threshold_still_five_and_null_branch_present(self) -> None:
        # ★070 의 profile 슬라이스에는 065 의 repeats having 절(`>= 2`)도 함께 들어 있어
        # _THRESHOLD_RE(037/039 이 쓰던 "슬라이스 안 유일 비교" 가정)를 그대로 재사용하면
        # 2건이 잡혀 오탐한다 -- 그래서 여기선 코호트 게이트 문구를 직접 대조한다.
        self.assertIn(
            "(select count(distinct raw_signal_id) from rows_out) < 5", self.fn_profile
        )
        self.assertIn("< 5 then 'null'::jsonb", self.fn_profile)

    def test_display_name_tiebreak_preserved(self) -> None:
        self.assertIn("count(*) desc, length(nm) desc, nm asc", self.fn_profile)

    def test_input_resolution_both_branches_preserved(self) -> None:
        self.assertIn(
            "a.inspector_key = q.qk or public.findings_inspector_key(a.raw_name) = q.qk",
            self.fn_profile,
        )

    def test_repeats_definition_unchanged(self) -> None:
        # 065 의 핵심 정의(문서 수로 반복을 센다)가 070 재선언에서도 그대로인지.
        self.assertIn("count(distinct raw_signal_id)::int as documents", self.fn_profile)
        self.assertIn("having count(distinct raw_signal_id) >= 2", self.fn_profile)

    def test_grant_execute_present_for_profile_only(self) -> None:
        self.assertIn(
            "grant execute on function public.findings_inspector_profile(text) "
            "to anon, authenticated;",
            self.sql,
        )
        self.assertNotIn("grant execute on function public.findings_inspector_index", self.sql)
        self.assertNotIn("grant execute on function public.findings_inspector_pairs", self.sql)
        self.assertNotIn("grant execute on function public.findings_inspector_key", self.sql)


if __name__ == "__main__":
    unittest.main()
