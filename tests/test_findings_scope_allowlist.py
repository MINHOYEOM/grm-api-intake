#!/usr/bin/env python3
"""FIND-1 483 범위 분류 마이그레이션 tests -- 020/023/024.

Offline source-text checks only -- no network, no real Postgres/sqlite connection.

★024(024_findings_scope_hctp.sql) 가 023(023_findings_scope_tiered.sql)의 (A) 분류 함수
grm_classify_483_scope 바디를 create or replace 로 **supersede** 한다 -- 사용자 범위 결정
(2026-07-16): HCT/P(21 CFR 1271 인체 세포·조직)를 GRM 범위(§1.1 "의약품 전반")에서 제외한다.
변경은 est_type 3계층 중 tissue 토큰의 계층 이동뿐이다 -- ①강한 허용목록에서 `human tissue`
제거 ②부정목록에 `tissue` 추가 ③약한 허용목록에서 `tissue` 제거. ④본문/업체명 폴백·⑤fragment
임계·⑥ok 는 023 과 완전 동일(본문 축 불변 = 설계 핵심 -- HCT/P 를 본문 신호로 배제하지
않는다. Celltex/Liveyon 등 FDA 가 생물의약품으로 규제한 세포치료 업체 29건은 본문의 제약
신호로 계속 공개된다). 023 파일의 (A) 함수 바디는 git 히스토리·원복용 원본으로 그대로 남고
(파일 상단에 그 사실을 알리는 포인터 주석만 추가했다 -- 007/008/009→010, 018/021→022,
020→023 관례와 동형), 프로덕션 현행 정의는 024 이다. 시그니처(4-인자)가 불변이므로 020 의
(B) 백필/(C) 트리거/(D) 010 2-인자 함수 drop 은 재배선 없이 그대로 프로덕션 현행이다 --
그래서 020 쪽 TriggerTest/BackfillTest/OldFunctionRetiredTest/SearchPathTest 는 변경 없이
계속 020 파일을 검사한다. 023 자체의 (A) 함수 바디 내용·순서 검사(구 TieredFunctionSignature
Test/TierOrderTest/StrongAllowNarrowTokenTest/WeakAllowAfterDenyTest/DenylistUnchangedTest/
FallbackFragmentOkUnchangedTest/PreservedCompositeLabelTest/CodexCounterexampleBlockedTest/
TieredBackfillTest/TieredRuleSemanticsTest)는 이 파일에서 024 파일을 검사하는 것으로
이관됐고, 023 쪽은 024 를 가리키는 supersede 헤더가 실제로 있는지 + (A) 바디가 원복용으로
그대로 남았는지만 확인한다(020 쪽이 023 을 가리키는 것과 동일한 처리 -- 020→023 전례를
023→024 에도 그대로 적용).

이 파일이 020/023/024 전체에 걸쳐 고정하는 계약:
  ①시그니처 = grm_classify_483_scope(p_est_type text, p_len integer, p_doc_text text,
    p_firm_name text), 4-인자 불변(020(D)가 010 의 2-인자 버전을 이미 drop)
  ②020(구) 판정 순서 = 허용목록(넓은 토큰 포함) -> 부정목록 -> 본문/업체명 폴백 ->
    fragment -> ok
  ③023/024(신) 판정 순서 = 강한 허용목록 -> 부정목록 -> 약한 허용목록 -> 본문/업체명 폴백 ->
    fragment -> ok -- 023 은 020 의 허용목록을 강/약으로 분리했을 뿐(부정목록·폴백·임계·ok는
    020 과 byte 동일), 024 는 023 의 3계층 중 tissue 토큰만 계층 이동(그 외 023 과 byte
    동일 -- 특히 ④⑤⑥ 본문 폴백/fragment/ok 는 023 과 완전 동일)
  ④023 회귀 계약(023 자체 헤더 명시, 유지): FDA 실존 어휘의 분류 결과는 020 과 전량 동일해야
    한다 -- 바뀌는 것은 실존하지 않는 위험 라벨(Codex 반례 8종)의 방어뿐이다
  ⑤024 회귀 계약(024 자체 헤더 명시, 신규): 023 대비 바뀌는 것은 HCT/P 계열 est_type 라벨
    (Human Tissue Establishment·Reproductive Human Tissue·Human Tissue and Medical Device
    Manufacturer·Tissue Testing Laboratory)이 'non_pharma' 로 바뀌는 것뿐이다 -- 혈액제제
    (Blood Bank·Plasma Derivative·Red Cross·Vaccine/Blood Products)·생물의약품으로 규제된
    세포치료(Biological Drug Manufacturer 계열, 본문 신호로 보존)는 전부 'ok' 유지된다
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_ALLOWLIST_MIGRATION_PATH = _MIGRATIONS_DIR / "020_findings_scope_allowlist.sql"
_TIERED_MIGRATION_PATH = _MIGRATIONS_DIR / "023_findings_scope_tiered.sql"
_HCTP_MIGRATION_PATH = _MIGRATIONS_DIR / "024_findings_scope_hctp.sql"
_SCOPE_MIGRATION_PATH = _MIGRATIONS_DIR / "010_findings_scope_purity.sql"


def _strip_sql_comments(sql: str) -> str:
    kept = [line for line in sql.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept)


def _extract_est_type_regexes(sql: str) -> list[str]:
    """est_type 을 대상으로 하는 `~* '(...)'` 정규식 본문을 파일 등장 순서대로 뽑는다.

    괄호 균형으로 세지 않고 "정규식 리터럴 1개 = 1줄, 그 줄의 마지막 `'` 바로 뒤가 개행"
    이라는 마이그레이션의 실제 서식에 앵커한다 -- `(control|contract) testing laborator`
    처럼 정규식 안에 중첩 괄호가 있으면 `\\(.*?\\)'` 논-그리디만으로는 첫 내부 `)'` 에서
    멈춰 잘못 잘리므로, 줄 끝(`'\\n`)까지 통째로 잡는 이 방식이 필요하다.
    """
    return re.findall(r"when coalesce\(p_est_type, ''\) ~\* '(\(.*?)'\n", sql)


def _pg_regex(pattern: str) -> re.Pattern[str]:
    """Postgres `~*` 정규식 리터럴을 파이썬 re 로 옮긴다: 단어 경계 `\\y` -> `\\b`,
    `~*`(대소문자 무시) -> re.I."""
    return re.compile(pattern.replace(r"\y", r"\b"), re.I)


def _fallback_fragment_ok_block(code_no_comments: str) -> str:
    """④(020 기준 ③) 본문/업체명 폴백 when 절부터 함수 정의 끝(닫는 `$$;` 직전)까지.
    comment 제거된 code 에서 뽑아야 원문자 번호 차이(020='③④', 023='④⑤')가 비교에
    섞이지 않는다."""
    start = code_no_comments.index("when regexp_replace(coalesce(p_doc_text, '')")
    end = code_no_comments.index("$$;", start)
    return code_no_comments[start:end]


# 020/023 이 강한 허용목록으로 보존하던 실존 FDA 복합 라벨 4종 중, 024 에서도 여전히
# ①(강한 허용목록)로 보존되는 3종. 네 번째 'Human Tissue and Medical Device Manufacturer'
# 는 024 에서 ②(부정목록)로 계층 이동한다(HCT/P 배제) -- 아래 _HCTP_EXCLUDED_LABELS 참조.
_PRESERVED_COMPOSITE_LABELS_024 = (
    "Pharmaceutical and Medical Device Manufacturer",
    "Medical Food and OTC Drug Manufacturer",
    "Biologics & Medical Device Manufacturer",
)

# Codex 감사가 지목한 반례 8종 -- 020 에서는 허용목록의 넓은 토큰이 부정목록보다 먼저라
# 전부 'ok' 로 새는 경로였다(실존 0건이지만 방어가 없었다). 023 의 수리 목표이자 024 에서도
# 그대로 유지돼야 하는 회귀 계약('Tissue Medical Device Manufacturer' 는 024 의 새 tissue
# deny 토큰과도 무관하게 이미 'medical device' 로 걸리므로 영향 없음).
_CODEX_COUNTEREXAMPLES = (
    "Sterile Medical Device Manufacturer",
    "Sterile Food Manufacturer",
    "Blood Collection Medical Device Manufacturer",
    "Plasma Medical Device Manufacturer",
    "Tissue Medical Device Manufacturer",
    "Medical Device Contract Testing Laboratory",
    "Medical Device Own Label Distributor",
    "Medical Device Manufacturer",
)

# 024 헤더 "★영향(dry-run 실측)" 이 명시하는 HCT/P 배제 대상 4 라벨 -- ①(강한 허용목록)에
# 비매치, ②(부정목록)에 매치해야 한다(= non_pharma, 024 의 수리 목표).
_HCTP_EXCLUDED_LABELS = (
    "Human Tissue Establishment",
    "Reproductive Human Tissue",
    "Human Tissue and Medical Device Manufacturer",
    "Tissue Testing Laboratory",
)

# 024 헤더 "★혈액제제 경계 보존(불가침)" 이 명시하는 라벨 -- ①(강한 허용목록)로 보존된다.
# 'Blood Bank' 는 ③(약한 허용목록)의 `blood` 토큰으로 보존되므로 별도 취급한다(아래
# HctpBloodCellTherapyPreservedTest 참조).
_BLOOD_CELL_THERAPY_LABELS = (
    "Plasma Derivative Manufacturer",
    "American National Red Cross",
    "Vaccine/Blood Products Manufacturer",
    "Biological Drug Manufacturer",
)


# ---------------------------------------------------------------------------
# 020 -- (A) 함수 바디는 023 이 supersede. (B) 백필/(C) 트리거/(D) drop 은 시그니처 불변
# 덕에 재배선 없이 여전히 프로덕션 현행이므로, 아래 클래스들은 020 파일을 계속 검사한다.
# ---------------------------------------------------------------------------

class AllowlistMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            _ALLOWLIST_MIGRATION_PATH.is_file(), f"missing {_ALLOWLIST_MIGRATION_PATH}"
        )
        self.sql = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _ALLOWLIST_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_measured_impact_counts(self) -> None:
        # The live-measured numbers (2026-07-15) this migration is anchored to.
        for token in ("8,545", "8,455", "375", "62", "272", "103", "392", "734"):
            self.assertIn(token, self.sql, f"missing measured count: {token!r}")

    def test_documents_revert_procedure(self) -> None:
        self.assertIn("되돌", self.sql)
        self.assertIn("scope_status = 'ok'", self.sql)

    def test_documents_010_supersede_relationship(self) -> None:
        self.assertIn("010_findings_scope_purity.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())

    def test_declares_impact_surface(self) -> None:
        # Item 3 of the brief: every consumer of the scope_status predicate is named.
        for token in ("findings_public_read", "findings_stats", "findings_category_matrix",
                      "findings_translation_queue", "findings_similar", "trends"):
            self.assertIn(token, self.sql, f"impact surface not documented: {token!r}")

    def test_supersede_header_points_to_023(self) -> None:
        """★(A) 함수 바디는 023 이 supersede -- 007/008/009→010, 018/021→022 관례와 동형으로
        020 파일 상단에 그 사실을 알리는 포인터 주석이 있어야 한다."""
        self.assertIn("023_findings_scope_tiered.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())
        # 포인터는 파일 맨 위(함수 정의보다 앞)에 있어야 한다.
        self.assertLess(
            self.sql.index("023_findings_scope_tiered.sql"),
            self.sql.index("create or replace function public.grm_classify_483_scope("),
        )

    def test_020_classify_body_left_intact_for_revert(self) -> None:
        """023 포인터 주석 추가가 (A) 함수 바디 자체는 건드리지 않았는지 -- 010 의 (B)/(D)
        가 020 헤더 추가 후에도 원문 그대로 남아있는 것(UpstreamHeaderNoteTest)과 동형인
        보존 계약."""
        self.assertIn(
            "create or replace function public.grm_classify_483_scope(\n"
            "  p_est_type text,\n"
            "  p_len integer,\n"
            "  p_doc_text text,\n"
            "  p_firm_name text\n"
            ")",
            self.sql,
        )


class NoNewStatusValueTest(unittest.TestCase):
    """This migration deliberately reuses 010's 3-value scope_status domain -- it must
    not touch the column, the check constraint, the public policy, or the RPCs, since
    a value-set change is what would force those to be re-declared."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_does_not_alter_table_or_constraint(self) -> None:
        self.assertNotIn("alter table", self.code.lower())
        self.assertNotIn("add constraint", self.code.lower())
        self.assertNotIn("findings_scope_status_chk", self.code)

    def test_does_not_redefine_policy_or_rpcs(self) -> None:
        self.assertNotIn("create policy", self.code.lower())
        self.assertNotIn("security definer", self.code.lower())
        for fn in ("findings_stats", "findings_firm_stats", "findings_category_matrix",
                   "findings_translation_queue", "findings_translation_rows"):
            self.assertNotIn(f"function public.{fn}", self.code)

    def test_only_the_three_010_status_values_are_emitted(self) -> None:
        emitted = set(re.findall(r"'(ok|non_pharma|fragment|needs_review)'", self.code))
        self.assertEqual(emitted, {"ok", "non_pharma", "fragment"})


class TriggerTest(unittest.TestCase):
    """(C) trigger -- rewired to the 4-arg rule, reading doc text from raw_json. Still
    current in production (023 does not touch it -- same 4-arg signature)."""

    def setUp(self) -> None:
        self.sql = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_trigger_only_touches_fda_483(self) -> None:
        self.assertIn("if new.source = 'FDA 483' then", self.code)

    def test_trigger_reads_doc_text_from_observations_array(self) -> None:
        self.assertIn("fda_483_observations", self.code)
        self.assertIn("jsonb_array_elements", self.code)
        self.assertIn("deficiency", self.code)

    def test_trigger_guards_non_array_observations(self) -> None:
        # jsonb_array_elements() raises on a non-array -- an unguarded call would take
        # the whole ingestion pipeline down on one malformed raw_json.
        self.assertIn("jsonb_typeof", self.code)

    def test_trigger_defaults_to_ok_when_raw_signal_missing(self) -> None:
        self.assertIn("new.scope_status := 'ok';", self.code)

    def test_trigger_passes_four_args(self) -> None:
        self.assertIn("public.grm_classify_483_scope(\n        v_est_type,", self.code)
        self.assertIn("v_doc_text,", self.code)
        self.assertIn("coalesce(new.firm_name, '')", self.code)

    def test_trigger_recreated_before_insert_for_each_row(self) -> None:
        self.assertIn("drop trigger if exists findings_scope_status_biu on public.findings;",
                      self.code)
        self.assertIn("before insert on public.findings", self.code)
        self.assertIn("for each row execute function public.grm_findings_scope_status_trigger();",
                      self.code)


class BackfillTest(unittest.TestCase):
    """(B) backfill -- document-level aggregation, 483-only, idempotent. Historical fact
    (already applied); 023 ships its own defensive re-run of the same shape."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_backfill_is_document_scoped_via_string_agg(self) -> None:
        self.assertIn("string_agg(f.finding_text, ' ')", self.code)
        self.assertIn("group by f.raw_signal_id", self.code)

    def test_backfill_restricted_to_fda_483(self) -> None:
        self.assertEqual(self.code.count("f.source = 'FDA 483'"), 2)  # CTE + UPDATE

    def test_backfill_extracts_establishment_type_via_jsonb_cast(self) -> None:
        self.assertIn("(rs.raw_json::jsonb) ->> 'establishment_type'", self.code)

    def test_backfill_passes_firm_name(self) -> None:
        self.assertIn("coalesce(f.firm_name, '')", self.code)


class OldFunctionRetiredTest(unittest.TestCase):
    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_two_arg_function_is_dropped(self) -> None:
        self.assertIn("drop function if exists public.grm_classify_483_scope(text, integer);",
                      self.code)

    def test_drop_happens_after_trigger_is_rewired(self) -> None:
        # Dropping before the trigger function is replaced would leave the trigger
        # pointing at a function that no longer exists.
        trigger_idx = self.code.index("create trigger findings_scope_status_biu")
        drop_idx = self.code.index("drop function if exists public.grm_classify_483_scope")
        self.assertLess(trigger_idx, drop_idx)


class SearchPathTest(unittest.TestCase):
    def test_both_functions_pin_search_path(self) -> None:
        code = _strip_sql_comments(_ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8"))
        # grm_classify_483_scope + grm_findings_scope_status_trigger, no RPCs here.
        self.assertEqual(code.count("set search_path = public"), 2)


# ---------------------------------------------------------------------------
# 023 -- 024 에 (A) 정의를 supersede 당한 이력 파일. 020 의 함수-바디 내용·순서 계약(구
# ClassifyFunctionTest/RuleSemanticsTest)이 옮겨온 뒤(023 단계), 이제 그 023 자신의 함수-
# 바디 내용·순서 계약도 아래 024 섹션으로 다시 옮겨갔다 -- 여기 남는 것은 020 쪽과 동형인
# supersede 헤더 검사 + 바디 원복용 보존 검사, 그리고 파일 수준 위생 점검뿐이다.
# ---------------------------------------------------------------------------

class TieredMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_TIERED_MIGRATION_PATH.is_file(), f"missing {_TIERED_MIGRATION_PATH}")
        self.sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _TIERED_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_020_relationship(self) -> None:
        # 023 자체 표현은 "교체" (020 과의 관계 서술). "supersede" 는 이제 023 파일에도
        # 등장한다 -- 아래 test_supersede_header_points_to_024 가 검증하는, 파일 맨 위에
        # 새로 추가된 024 포인터 주석 때문이다(020 관계 문구 자체는 그 추가로 바뀌지 않았다).
        self.assertIn("020", self.sql)
        self.assertIn("교체", self.sql)

    def test_does_not_touch_unrelated_objects(self) -> None:
        """023 자체 헤더 계약: (A) 함수 바디 교체 + 방어적 (B) 백필 재실행뿐 -- 020 이 만든
        (C) 트리거·(D) drop, 010 의 컬럼/정책/RPC 는 재선언하지 않는다. 024 포인터 주석
        추가(위 test_supersede_header_points_to_024) 도 순수 주석이라 이 계약을 안 건드린다."""
        for forbidden in (
            "alter table", "add constraint", "create policy", "security definer",
            "create trigger", "drop trigger", "drop function",
        ):
            self.assertNotIn(forbidden, self.code.lower(), f"023 must not contain: {forbidden}")

    def test_does_not_redefine_other_rpcs(self) -> None:
        for fn in ("findings_stats", "findings_firm_stats", "findings_category_matrix",
                   "findings_translation_queue", "findings_translation_rows",
                   "findings_similar", "findings_similar_to"):
            self.assertNotIn(f"function public.{fn}", self.code)

    def test_only_the_three_status_values_are_emitted(self) -> None:
        emitted = set(re.findall(r"'(ok|non_pharma|fragment|needs_review)'", self.code))
        self.assertEqual(emitted, {"ok", "non_pharma", "fragment"})

    def test_supersede_header_points_to_024(self) -> None:
        """★(A) 함수 바디는 024 이 supersede -- 020→023 관례와 동형으로 023 파일 상단에
        그 사실을 알리는 포인터 주석이 있어야 한다(020 쪽 test_supersede_header_points_to_023
        와 대응)."""
        self.assertIn("024_findings_scope_hctp.sql", self.sql)
        self.assertIn("supersede", self.sql.lower())
        # 포인터는 파일 맨 위(함수 정의보다 앞)에 있어야 한다.
        self.assertLess(
            self.sql.index("024_findings_scope_hctp.sql"),
            self.sql.index("create or replace function public.grm_classify_483_scope("),
        )

    def test_023_classify_body_left_intact_for_revert(self) -> None:
        """024 포인터 주석 추가가 (A) 함수 바디 자체는 건드리지 않았는지 -- 020 쪽
        test_020_classify_body_left_intact_for_revert 와 동형인 보존 계약."""
        self.assertIn(
            "create or replace function public.grm_classify_483_scope(\n"
            "  p_est_type text,\n"
            "  p_len integer,\n"
            "  p_doc_text text,\n"
            "  p_firm_name text\n"
            ")",
            self.sql,
        )


# ---------------------------------------------------------------------------
# 024 -- grm_classify_483_scope() 의 프로덕션 현행 (A) 정의. 023 의 함수-바디 내용·순서
# 계약(구 TieredFunctionSignatureTest/TierOrderTest/StrongAllowNarrowTokenTest/
# WeakAllowAfterDenyTest/DenylistUnchangedTest/FallbackFragmentOkUnchangedTest/
# PreservedCompositeLabelTest/CodexCounterexampleBlockedTest/TieredBackfillTest/
# TieredRuleSemanticsTest)이 전부 이리로 옮겨온다(020→023 때와 동형).
# ---------------------------------------------------------------------------

class HctpMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_HCTP_MIGRATION_PATH.is_file(), f"missing {_HCTP_MIGRATION_PATH}")
        self.sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _HCTP_MIGRATION_PATH.read_bytes())

    def test_has_korean_block_comments(self) -> None:
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_documents_023_relationship(self) -> None:
        self.assertIn("023", self.sql)
        self.assertIn("supersede", self.sql.lower())

    def test_does_not_touch_unrelated_objects(self) -> None:
        """024 자체 헤더 계약: (A) 함수 바디 교체 + tissue 라벨 한정 (B) 백필뿐 -- 020 이
        만든 (C) 트리거·(D) drop, 010 의 컬럼/정책/RPC 는 재선언하지 않는다."""
        for forbidden in (
            "alter table", "add constraint", "create policy", "security definer",
            "create trigger", "drop trigger", "drop function",
        ):
            self.assertNotIn(forbidden, self.code.lower(), f"024 must not contain: {forbidden}")

    def test_does_not_redefine_other_rpcs(self) -> None:
        for fn in ("findings_stats", "findings_firm_stats", "findings_category_matrix",
                   "findings_translation_queue", "findings_translation_rows",
                   "findings_similar", "findings_similar_to"):
            self.assertNotIn(f"function public.{fn}", self.code)

    def test_only_the_three_status_values_are_emitted(self) -> None:
        emitted = set(re.findall(r"'(ok|non_pharma|fragment|needs_review)'", self.code))
        self.assertEqual(emitted, {"ok", "non_pharma", "fragment"})


class HctpFunctionSignatureTest(unittest.TestCase):
    """(h) 시그니처(4-인자) 불변 -- 023(A)가 이미 020 의 4-인자 형태를 유지했으므로 024 도
    같은 4-인자 시그니처를 create or replace 로 교체할 뿐, 트리거 재배선이 불요하다(020 의
    (C) 트리거가 재배선 없이 이 함수를 계속 호출한다). immutable·고정 search_path 도 023 의
    (A) 원본과 동일하게 유지돼야 한다."""

    def setUp(self) -> None:
        self.sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_signature_matches_four_arg_form(self) -> None:
        self.assertIn("create or replace function public.grm_classify_483_scope(", self.sql)
        for arg in ("p_est_type text", "p_len integer", "p_doc_text text", "p_firm_name text"):
            self.assertIn(arg, self.sql, f"missing arg: {arg!r}")
        self.assertIn("returns text", self.sql)
        self.assertIn("language sql", self.code)

    def test_immutable_and_search_path_pinned(self) -> None:
        self.assertIn("immutable", self.code)
        self.assertEqual(self.code.count("set search_path = public"), 1)


class HctpTierOrderTest(unittest.TestCase):
    """(a) 3계층 순서 유지 -- 024 도 023 과 동일하게 강한 allow -> deny -> 약한 allow 순으로
    when 절이 배치돼야 한다. 024 의 유일한 실질 변경은 순서가 아니라 tissue 토큰의 계층
    이동뿐이다(아래 HctpOnlyTissueTokenMovedTest 로 정밀 확인)."""

    def setUp(self) -> None:
        self.sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        self.regexes = _extract_est_type_regexes(self.sql)

    def test_exactly_three_est_type_tiers(self) -> None:
        self.assertEqual(len(self.regexes), 3, "024 must define exactly 3 est_type tiers")

    def test_tiers_are_strong_allow_deny_weak_allow_in_that_order(self) -> None:
        strong_allow, deny, weak_allow = self.regexes
        # 내용으로 신원 확인(등장 순서만 믿지 않는다) -- 각 계층의 대표 토큰.
        self.assertIn("outsourcing facility", strong_allow)
        self.assertIn("shell egg", deny)
        self.assertIn("repacker/relabeler", weak_allow)
        # 텍스트 위치 비교(when 절이 실제로 그 순서로 배치돼 있는지).
        i_strong = self.sql.index(strong_allow)
        i_deny = self.sql.index(deny)
        i_weak = self.sql.index(weak_allow)
        self.assertLess(i_strong, i_deny, "strong allow must precede deny")
        self.assertLess(i_deny, i_weak, "deny must precede weak allow")

    def test_weak_allow_precedes_body_fallback_block(self) -> None:
        # ④ 본문 폴백(아래 HctpFallbackBodyAxisUnchangedTest 대상)도 약한 허용목록보다 뒤에
        # 와야 전체 6-분기 순서(①②③④⑤⑥)가 성립한다.
        _strong, _deny, weak_allow = self.regexes
        fallback_idx = self.sql.index("when regexp_replace(coalesce(p_doc_text, '')")
        self.assertLess(self.sql.index(weak_allow), fallback_idx)


class HctpOnlyTissueTokenMovedTest(unittest.TestCase):
    """(b) 024 의 est_type 3계층 변경은 tissue 토큰의 계층 이동뿐이어야 한다(024 헤더
    "★변경" 명시) -- 023 대비 정확히 그 문자열 차이만 있는지 재구성으로 증명하고, 세
    계층 각각에서 해당 토큰의 존재/부재를 직접 확인한다."""

    def setUp(self) -> None:
        sql023 = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        sql024 = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        self.strong023, self.deny023, self.weak023 = _extract_est_type_regexes(sql023)
        self.strong024, self.deny024, self.weak024 = _extract_est_type_regexes(sql024)

    def test_strong_allow_lost_exactly_human_tissue(self) -> None:
        self.assertNotEqual(self.strong023, self.strong024)
        self.assertEqual(self.strong023.replace("human tissue|", ""), self.strong024)

    def test_deny_gained_exactly_tissue(self) -> None:
        self.assertNotEqual(self.deny023, self.deny024)
        self.assertEqual(self.deny023[:-1] + "|tissue)", self.deny024)

    def test_weak_allow_lost_exactly_tissue(self) -> None:
        self.assertNotEqual(self.weak023, self.weak024)
        self.assertEqual(self.weak023.replace("tissue|", ""), self.weak024)

    def test_human_tissue_absent_from_strong_allow(self) -> None:
        self.assertNotIn("human tissue", self.strong024)

    def test_tissue_present_in_deny(self) -> None:
        self.assertIn("tissue", self.deny024)

    def test_tissue_absent_from_weak_allow(self) -> None:
        self.assertNotIn("tissue", self.weak024)


class HctpExclusionMatchTest(unittest.TestCase):
    """(c) HCT/P 배제 실매치 검증 -- 024 헤더 "★영향(dry-run 실측)" 이 명시하는 4 라벨이
    ①(강한 허용목록)에 비매치하고 ②(부정목록)에 매치하는지(= non_pharma 경로) 파이썬 re 로
    확인한다(SQL `~*`/`\\y` -> 파이썬 re.I/`\\b` 치환은 _pg_regex 재사용)."""

    def setUp(self) -> None:
        sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        strong_allow, deny, _weak_allow = _extract_est_type_regexes(sql)
        self.strong_allow_re = _pg_regex(strong_allow)
        self.deny_re = _pg_regex(deny)

    def test_hctp_labels_miss_strong_allow_and_hit_deny(self) -> None:
        for label in _HCTP_EXCLUDED_LABELS:
            with self.subTest(label=label):
                self.assertIsNone(
                    self.strong_allow_re.search(label),
                    f"{label!r} must NOT match strong allow (① regression -> would stay 'ok')",
                )
                self.assertIsNotNone(
                    self.deny_re.search(label),
                    f"{label!r} must match deny (② -> classified 'non_pharma')",
                )


class HctpBloodCellTherapyPreservedTest(unittest.TestCase):
    """(d) 혈액제제·세포치료 보존 실매치 검증 -- 024 헤더 "★혈액제제 경계 보존(불가침)" 목록을
    파이썬 re 로 확인한다. 'Blood Bank' 는 ③(약한 허용목록)의 `blood` 로, 나머지 4종은
    ①(강한 허용목록)로 보존된다."""

    def setUp(self) -> None:
        sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        self.strong_allow, self.deny, self.weak_allow = _extract_est_type_regexes(sql)
        self.strong_allow_re = _pg_regex(self.strong_allow)
        self.deny_re = _pg_regex(self.deny)
        self.weak_allow_re = _pg_regex(self.weak_allow)

    def test_blood_bank_misses_strong_allow_and_deny_hits_weak_allow(self) -> None:
        label = "Blood Bank"
        self.assertIsNone(self.strong_allow_re.search(label), f"{label!r} must not hit ①")
        self.assertIsNone(self.deny_re.search(label), f"{label!r} must not hit ②")
        self.assertIsNotNone(self.weak_allow_re.search(label), f"{label!r} must hit ③ (ok)")

    def test_preserved_labels_hit_strong_allow(self) -> None:
        for label in _BLOOD_CELL_THERAPY_LABELS:
            with self.subTest(label=label):
                self.assertIsNotNone(
                    self.strong_allow_re.search(label),
                    f"{label!r} must match the strong-allow (①) regex",
                )


class HctpFallbackBodyAxisUnchangedTest(unittest.TestCase):
    """(e) 본문 축 불변 -- ④ pharma/device/food/firm 정규식·⑤ fragment 임계(30자)·⑥ ok 가
    023 과 024 사이에 byte 일치해야 한다(024 헤더의 설계 핵심: est_type 축만 바뀐다)."""

    def setUp(self) -> None:
        self.code023 = _strip_sql_comments(_TIERED_MIGRATION_PATH.read_text(encoding="utf-8"))
        self.code024 = _strip_sql_comments(_HCTP_MIGRATION_PATH.read_text(encoding="utf-8"))
        self.block023 = _fallback_fragment_ok_block(self.code023)
        self.block024 = _fallback_fragment_ok_block(self.code024)

    def test_fallback_fragment_ok_block_is_byte_identical_to_023(self) -> None:
        self.assertEqual(self.block023, self.block024)

    def test_fragment_threshold_is_still_30(self) -> None:
        self.assertIn("< 30", self.block024)

    def test_ends_with_ok_else_branch(self) -> None:
        self.assertIn("else 'ok'", self.block024)

    def test_ffdca_and_form_header_masked_before_body_signals(self) -> None:
        self.assertIn("cosmetic act", self.block024.lower())
        self.assertIn("food\\s+and\\s+drug\\s+administra", self.block024)
        self.assertIn("regexp_replace", self.block024)

    def test_body_signal_guarded_by_pharma_negative_lookup(self) -> None:
        self.assertIn("!~*", self.block024)

    def test_uses_postgres_word_boundary_not_pcre(self) -> None:
        self.assertIn(r"\y", self.block024)
        self.assertNotIn(r"\b", self.block024)


class HctpBodySignalDesignContractTest(unittest.TestCase):
    """(f) 설계 계약(024 헤더 "★설계" 명시) -- HCT/P 는 est_type 으로만 배제하고 본문(④)
    신호로는 배제하지 않는다(Celltex/Liveyon 등 생물의약품으로 규제된 세포치료 29건을 지키기
    위해서다). 코드(주석 제거 후)에 HCT/P·1271·donor eligibilit 문자열이 정규식 안에 없어야
    한다 -- 설계 근거 서술은 주석 몫이라 주석에는 있어도 된다."""

    def setUp(self) -> None:
        self.sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_hctp_body_signal_tokens_in_code(self) -> None:
        for token in ("HCT/P", "1271", "donor eligibilit"):
            self.assertNotIn(token, self.code, f"body-signal token leaked into code: {token!r}")

    def test_hctp_rationale_is_documented_in_comments(self) -> None:
        # 코드에는 없어야 하지만(위), 배제 근거는 주석에 문서화돼 있어야 한다.
        self.assertIn("1271", self.sql)
        self.assertIn("HCT/P", self.sql)


class HctpBackfillScopeTest(unittest.TestCase):
    """(g) 백필이 tissue 라벨 행으로 한정 -- 020/023 처럼 483 전량을 재계산하지 않고 tissue
    계열 라벨 문서만 재분류한다(024 헤더 근거: 전량 재계산 UPDATE 가 커넥터 타임아웃 ->
    롤백을 유발한 전례가 있어 한정 백필로 설계). FDA 483 한정·멱등(UPDATE, not INSERT)은
    020/023 과 동일하게 유지된다."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        )

    def test_backfill_is_document_scoped_via_string_agg(self) -> None:
        self.assertIn("string_agg(f.finding_text, ' ')", self.code)
        self.assertIn("group by f.raw_signal_id", self.code)

    def test_backfill_restricted_to_fda_483(self) -> None:
        # doc_text CTE 의 f.source + 후보 한정 raw_signals 서브쿼리의 rs.source + 바깥
        # UPDATE 의 f.source, 총 3곳.
        self.assertEqual(self.code.count("source = 'FDA 483'"), 3)

    def test_backfill_has_tissue_label_subquery_filter(self) -> None:
        self.assertIn("~* 'tissue'", self.code)
        self.assertIn("establishment_type", self.code)
        # tissue 필터가 raw_signals 서브쿼리(문서 후보 한정) 안, UPDATE 이전에 있어야 한다.
        i_subquery = self.code.index("select rs.raw_signal_id from public.raw_signals rs")
        i_tissue = self.code.index("~* 'tissue'")
        i_update = self.code.index("update public.findings f")
        self.assertLess(i_subquery, i_tissue)
        self.assertLess(i_tissue, i_update)

    def test_backfill_extracts_establishment_type_via_jsonb_cast(self) -> None:
        self.assertIn("(rs.raw_json::jsonb) ->> 'establishment_type'", self.code)

    def test_backfill_passes_firm_name(self) -> None:
        self.assertIn("coalesce(f.firm_name, '')", self.code)

    def test_backfill_is_an_idempotent_update_not_an_insert(self) -> None:
        self.assertIn("update public.findings f", self.code)
        self.assertNotIn("insert into public.findings", self.code)


# ---------------------------------------------------------------------------
# Behavioural pin: 024 의 전체 6-분기 판정을 파이썬으로 재구현해 실제 행 위에서 pin 한다
# (023 의 TieredRuleSemanticsTest 계승 -- 이제 소스는 024). 정규식 문자열은 024 파일에서
# 직접 읽으므로 마이그레이션 쪽 드리프트가 여기서 즉시 깨진다.
# ---------------------------------------------------------------------------

class HctpRuleSemanticsTest(unittest.TestCase):
    """024 회귀 계약(024 자체 헤더 명시): 023 대비 바뀌는 것은 HCT/P 계열 est_type 라벨이
    'non_pharma' 로 바뀌는 것뿐이다. 아래는 023 의 TieredRuleSemanticsTest 가 pin 했던 모든
    실측 행을 024 의 3계층 로직으로 재확인하고, HCT/P 4 라벨이 실제로 'non_pharma' 로
    바뀌는지(수리 목표)·혈액제제·세포치료 라벨은 여전히 'ok' 인지까지 end-to-end 로
    확인한다."""

    # 023/024 공유(byte 동일, HctpFallbackBodyAxisUnchangedTest 로 확인됨) -- ④ 본문 폴백.
    STRONG_DEVICE = (r"(\yMDR\y|medical device report|device history record|device master record"
                     r"|finished devices?\y|user facility|21 CFR 820|\y820\.\d"
                     r"|design (input|output|history file)|marketed device)")
    STRONG_FOOD = (r"(food[- ]contact|\yfoods?\y|animal food|low[- ]acid canned|infant formula"
                   r"|\yHACCP\y|\yjuice\y|seafood|ice cream|\ycheese\y|\ymilk\y)")
    STRONG_PHARMA = (r"(drug products?|drug substance|active pharmaceutical ingredient|aseptic"
                     r"|sterilit|\ysterile\y|compounded?|\yUSP\y|batch record|master production"
                     r"|\y211\.\d|finished pharmaceutical|prescription|\yNDC\y|\yOTC\y|potency"
                     r"|adverse drug|quality control unit|\yDSCSA\y|tablets?|capsules?"
                     r"|injectable|vials?)")
    FOOD_FIRM = (r"(creamer|creamery|dairy|\yfoods?\y|\yfarms?\y|orchard|bakery|baking|tortiller"
                 r"|produce|beverage|brewing|nestle|juice)")
    MASK = (r"(federal food,?\s*drug,?\s*(and|&)\s*cosmetic act|food\s+and\s+drug\s+administra"
            r"|\yFD&C\y|department of health)")

    @classmethod
    def setUpClass(cls) -> None:
        sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        cls.strong_allow, cls.deny, cls.weak_allow = _extract_est_type_regexes(sql)

    @classmethod
    def _classify(cls, est: str, length: int, doc: str, firm: str) -> str:
        clean = _pg_regex(cls.MASK).sub(" ", doc or "")
        if _pg_regex(cls.strong_allow).search(est or ""):
            return "fragment" if length < 30 else "ok"
        if _pg_regex(cls.deny).search(est or ""):
            return "non_pharma"
        if _pg_regex(cls.weak_allow).search(est or ""):
            return "fragment" if length < 30 else "ok"
        if not _pg_regex(cls.STRONG_PHARMA).search(clean) and (
            _pg_regex(cls.STRONG_DEVICE).search(clean)
            or _pg_regex(cls.STRONG_FOOD).search(clean)
            or _pg_regex(cls.FOOD_FIRM).search(firm or "")
        ):
            return "non_pharma"
        return "fragment" if length < 30 else "ok"

    def test_mirrored_fallback_regexes_are_byte_present_in_the_migration(self) -> None:
        sql = _HCTP_MIGRATION_PATH.read_text(encoding="utf-8")
        for name in ("STRONG_DEVICE", "STRONG_FOOD", "STRONG_PHARMA", "FOOD_FIRM", "MASK"):
            self.assertIn(getattr(self, name), sql, f"{name} drifted from the .sql")

    def test_live_food_leaks_are_flagged(self) -> None:
        # Real rows, live at 2026-07-15, all scope_status='ok' before 020.
        cases = [
            ("Blue Bell Creameries, LP", "Manufacturer",
             "Failure to handle and maintain equipment, containers and utensils used to hold "
             "food in a manner that protects against contamination."),
            ("Plainview Milk Products Cooperative", "Manufacturer",
             "Failure to maintain buildings and fixtures in repair sufficient to prevent food "
             "from becoming adulterated."),
            ("Bravo Packing, Inc.", "Manufacturer",
             "You did not hold animal food for distribution under conditions that protect "
             "against contamination and minimize deterioration."),
            ("Sanger Fresh Cut Produce Co. LLC",
             "Initial Distributor, Manufacturer, Specification Developer",
             "Hand-washing facilities lack running water of a suitable temperature."),
            ("San Francisco Herb and Natural Food Company", "Importer/Warehouse/Repacker",
             "Failure to store finished food under conditions that would protect against "
             "microbial contamination."),
            ("Thermo Pac LLC", "Manufacturer",
             "Failure to provide FDA, before packing any new product, information as to the "
             "scheduled process for each low-acid canned food in each container."),
        ]
        for firm, est, doc in cases:
            with self.subTest(firm=firm):
                self.assertEqual(self._classify(est, len(doc), doc, firm), "non_pharma")

    def test_live_device_leaks_are_flagged(self) -> None:
        cases = [
            ("Advanced Medical Optics, Inc",
             "Initial Distributor, Manufacturer, Specification Developer",
             "Design input requirements were not adequately documented. Design output was not "
             "adequately established."),
            ("Advocate Lutheran General Hospital", "Health Care Facility",
             "Written MDR procedures have not been developed."),
            ("Hill-Rom, Inc.", "Manufacturer",
             "Rework and reevaluation activities have not been documented in the device history "
             "record."),
        ]
        for firm, est, doc in cases:
            with self.subTest(firm=firm):
                self.assertEqual(self._classify(est, len(doc), doc, firm), "non_pharma")

    def test_legitimate_pharma_on_generic_est_type_stays_public(self) -> None:
        # The over-blocking risk 020's brief flagged: 392 findings / 68 docs live on a
        # generic est_type and are real pharma. These must survive under 024 too.
        cases = [
            ("Teva Parenteral Medicines, Inc.", "Manufacturer",
             "The aseptic processing area is deficient. Sterile drug products are at risk."),
            ("Hospira Inc. A Pfizer Company", "Manufacturer",
             "Batch record review for drug products was not adequate."),
            ("Gilead Sciences, Inc", "Manufacturer",
             "The quality control unit did not review and approve the batch record for the drug "
             "product prior to release."),
            ("American Family Pharmacy, LLC", "Manufacturer",
             "Compounded sterile preparations were not tested for potency prior to dispensing."),
            ("Premier Pharmacy Labs, Inc.", "",
             "Aseptic technique was deficient during the compounding of sterile drug products."),
        ]
        for firm, est, doc in cases:
            with self.subTest(firm=firm):
                self.assertEqual(self._classify(est, len(doc), doc, firm), "ok")

    def test_preserved_composite_labels_survive_the_denylist(self) -> None:
        # Strong-allow-first 가 이 라벨들을 지킨다 -- deny 토큰(medical device/drug food 등)을
        # 포함해도 ①에서 먼저 매치해 공개 유지된다. 'Human Tissue and Medical Device
        # Manufacturer' 는 024 에서 이 목록에서 제외됨(아래 테스트로 non_pharma 확인).
        doc = "Batch record review for drug products was not adequate."
        for est in _PRESERVED_COMPOSITE_LABELS_024:
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "Hospira Inc."), "ok")

    def test_ffdca_citation_does_not_trigger_food_flag(self) -> None:
        # The measured false-positive source: a 503B outsourcing-facility citation.
        doc = ("Drug products were not compounded in accordance with section 503B of the "
               "Federal Food, Drug, and Cosmetic Act.")
        self.assertEqual(self._classify("", len(doc), doc, "Ameridose, LLC"), "ok")

    def test_fda_form_header_ocr_noise_does_not_trigger_food_flag(self) -> None:
        doc = ("DEPARTMENT OF HEALTH AND HUMAN SERVICES FOOD AND DRUG ADMINISTRATION "
               "Aseptic processing deficiencies were observed in the sterile drug suite.")
        self.assertEqual(self._classify("", len(doc), doc, "Catalent Indiana, LLC"), "ok")

    def test_explicit_pharma_est_type_short_text_is_fragment_not_non_pharma(self) -> None:
        # 010's fragment behaviour is preserved for strong-allowlisted est_types.
        self.assertEqual(
            self._classify("Producer of Sterile Drug Products", 5, "Promised to correct", "X"),
            "fragment",
        )

    def test_020_denylist_est_types_are_still_flagged(self) -> None:
        doc = "Observation text with no decisive signal either way."
        for est in ("Shell Egg Producer", "Cheese Manufacturer", "Pet Food Manufacturer",
                    "Animal Feed Manufacturer", "Infant Formula Manufacturer", "Farm",
                    "Institutional Review Board", "Clinical Investigator", "Bioanalytical Lab",
                    "Sponsor", "Aircraft Conveyance"):
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "non_pharma")

    def test_anda_sponsor_is_not_caught_by_the_sponsor_denylist_anchor(self) -> None:
        # '^sponsor$' is anchored: "ANDA Sponsor" is a drug sponsor and must stay public.
        doc = "The drug product stability program was inadequate."
        self.assertEqual(self._classify("ANDA Sponsor", len(doc), doc, "X"), "ok")

    def test_codex_counterexamples_resolve_to_non_pharma_end_to_end(self) -> None:
        """023 이 F-03 을 고친 이래 유지되는 회귀 계약 -- 반례 8종이 024 에서도 여전히
        'non_pharma' 로 분류되는지 전체 6-분기 파이프라인으로 확인한다."""
        doc = "Observation text with no decisive signal either way."
        for est in _CODEX_COUNTEREXAMPLES:
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "non_pharma")

    def test_previously_safe_narrow_labels_still_resolve_ok(self) -> None:
        # 023 목록에서 'Human Tissue Establishment' 는 제외됨 -- 024 에서 non_pharma 로
        # 계층 이동(아래 test_hctp_labels_now_resolve_non_pharma_end_to_end 로 확인).
        doc = "Routine inspection observation with no other decisive signal."
        for est in ("Blood Bank", "Plasma Derivative Product Manufacturer",
                    "Contract Testing Laboratory", "Repacker/Relabeler"):
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "ok")

    def test_hctp_labels_now_resolve_non_pharma_end_to_end(self) -> None:
        """024 의 수리 목표(핵심 회귀 계약) -- HCT/P 4 라벨이 본문 신호와 무관하게
        'non_pharma' 로 분류되는지 전체 6-분기 파이프라인으로 확인한다."""
        doc = "Routine inspection observation with no other decisive signal."
        for est in _HCTP_EXCLUDED_LABELS:
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "non_pharma")

    def test_blood_and_cell_therapy_labels_stay_ok_end_to_end(self) -> None:
        """혈액제제·세포치료 보존(불가침) -- 본문 신호와 무관하게 'ok' 유지."""
        doc = "Routine inspection observation with no other decisive signal."
        for est in _BLOOD_CELL_THERAPY_LABELS:
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "ok")
        self.assertEqual(self._classify("Blood Bank", len(doc), doc, "X"), "ok")

    def test_cell_therapy_body_signal_labels_stay_ok_via_fallback(self) -> None:
        """024 헤더 핵심 근거 -- Celltex/Liveyon 류(FDA 가 미승인 생물의약품으로 규제한
        세포치료 업체)는 est_type 이 일반값이라도 본문의 제약 신호(STRONG_PHARMA)로 ok
        유지된다(= 본문 축 불변의 실증)."""
        doc = "This is an unapproved biological drug product regulated under the FD&C Act."
        for est in ("Biological Drug Manufacturer", "Manufacturer", ""):
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "Celltex Therapeutics"), "ok")


# ---------------------------------------------------------------------------
# 020/023/024 과 무관하게 유지되는 하류·상류 계약(대상 파일이 바뀌지 않았으므로 변경 없음).
# ---------------------------------------------------------------------------

class DownstreamPredicateInheritanceTest(unittest.TestCase):
    """020 claims every scope_status consumer inherits the fix for free. That claim is
    only true while those consumers actually filter on scope_status='ok' -- pin it, so
    a future migration that drops the predicate fails here instead of silently
    re-publishing non-pharma rows through search/similarity."""

    def test_018_lexical_similar_filters_scope_status(self) -> None:
        path = _MIGRATIONS_DIR / "018_findings_similar_lexical.sql"
        self.assertTrue(path.is_file(), f"missing {path}")
        self.assertIn("scope_status = 'ok'", path.read_text(encoding="utf-8"))

    def test_019_embedding_similar_filters_scope_status_on_both_sides(self) -> None:
        # 019 findings_similar_by_id() gates the base finding AND the candidate set.
        path = _MIGRATIONS_DIR / "019_findings_embeddings.sql"
        self.assertTrue(path.is_file(), f"missing {path}")
        sql = path.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            sql.count("scope_status = 'ok'"), 2,
            "019 must gate both the base finding and the candidate set",
        )


class UpstreamHeaderNoteTest(unittest.TestCase):
    """010 must carry a pointer to 020 as the production source of truth for the
    classify rule, mirroring the 007/008/009 -> 010 convention 010 itself set. (023
    supersedes only 020's (A) body -- 010's own pointer still correctly names 020 as
    the file to consult, and 020 in turn now points on to 023.)"""

    def test_010_has_020_pointer_comment(self) -> None:
        sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn("020_findings_scope_allowlist.sql", sql)

    def test_010_classify_body_left_intact_for_revert(self) -> None:
        sql = _SCOPE_MIGRATION_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "create or replace function public.grm_classify_483_scope"
            "(p_est_type text, p_len integer)",
            sql,
        )


if __name__ == "__main__":
    unittest.main()
