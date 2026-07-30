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


if __name__ == "__main__":
    unittest.main()
