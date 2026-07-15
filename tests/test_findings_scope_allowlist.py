#!/usr/bin/env python3
"""FIND-1 483 범위 분류 마이그레이션 tests -- 020/023.

Offline source-text checks only -- no network, no real Postgres/sqlite connection.

★023(023_findings_scope_tiered.sql) 가 020(findings 순도 2차)의 (A) 분류 함수
grm_classify_483_scope 바디를 create or replace 로 **supersede** 한다 -- Codex 감사 F-03
(허용목록의 넓은 토큰 sterile|plasma|blood|tissue|own label|(control|contract) testing
laborator 가 부정목록보다 먼저라 명백한 기기/식품 라벨을 선점하는 결함) 수리: 허용목록을
강/약 2계층으로 쪼개고 그 사이에 부정목록을 끼운 3계층 판정으로 교체한다. 020 파일의 (A)
함수 바디는 git 히스토리·원복용 원본으로 그대로 남고(파일 상단에 그 사실을 알리는 포인터
주석만 추가했다 -- 007/008/009→010, 018/021→022 관례와 동형), 프로덕션 현행 정의는 023
이다. 시그니처(4-인자)가 불변이므로 020 의 (B) 백필/(C) 트리거/(D) 010 2-인자 함수 drop 은
재배선 없이 그대로 프로덕션 현행이다(023 이 정의하는 함수가 바로 그 트리거가 호출하는
함수) -- 그래서 아래 020 쪽 TriggerTest/BackfillTest/OldFunctionRetiredTest/SearchPathTest
는 변경 없이 계속 020 파일을 검사한다. 020 자체의 (A) 함수 바디 내용·순서 검사(구
ClassifyFunctionTest/RuleSemanticsTest)는 이 파일에서 023 파일을 검사하는 것으로 이관됐고,
020 쪽은 023 를 가리키는 supersede 헤더가 실제로 있는지만 확인한다.

이 파일이 020/023 전체에 걸쳐 고정하는 계약:
  ①시그니처 = grm_classify_483_scope(p_est_type text, p_len integer, p_doc_text text,
    p_firm_name text), 4-인자 불변(020(D)가 010 의 2-인자 버전을 이미 drop)
  ②020(구) 판정 순서 = 허용목록(넓은 토큰 포함) -> 부정목록 -> 본문/업체명 폴백 ->
    fragment -> ok
  ③023(신) 판정 순서 = 강한 허용목록 -> 부정목록 -> 약한 허용목록(020 에서 위험했던 넓은
    토큰들) -> 본문/업체명 폴백 -> fragment -> ok -- 부정목록 자체와 폴백/임계/ok 는 020 과
    byte 동일(무변경, 이관된 것은 허용목록의 강/약 분리뿐)
  ④회귀 계약(023 자체 헤더 명시): FDA 실존 어휘의 분류 결과는 020 과 전량 동일해야 한다 --
    바뀌는 것은 실존하지 않는 위험 라벨(Codex 반례 8종)의 방어뿐이다
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_ALLOWLIST_MIGRATION_PATH = _MIGRATIONS_DIR / "020_findings_scope_allowlist.sql"
_TIERED_MIGRATION_PATH = _MIGRATIONS_DIR / "023_findings_scope_tiered.sql"
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


# 020 이 보존하려던 실존 FDA 복합 라벨 4종 -- 020 헤더·023 헤더 양쪽에 문서화된 목록.
_PRESERVED_COMPOSITE_LABELS = (
    "Pharmaceutical and Medical Device Manufacturer",
    "Medical Food and OTC Drug Manufacturer",
    "Biologics & Medical Device Manufacturer",
    "Human Tissue and Medical Device Manufacturer",
)

# Codex 감사가 지목한 반례 8종 -- 020 에서는 허용목록의 넓은 토큰이 부정목록보다 먼저라
# 전부 'ok' 로 새는 경로였다(실존 0건이지만 방어가 없었다). 023 의 수리 목표.
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
# 023 -- grm_classify_483_scope() 의 프로덕션 현행 (A) 정의. 020 의 함수-바디 내용·순서
# 계약(구 ClassifyFunctionTest/RuleSemanticsTest)이 전부 이리로 옮겨온다.
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
        # 023 자체 표현은 "교체" (영단어 supersede 는 020/010 쪽 포인터 주석의 관용구이지
        # 023 원문에는 없다 -- 023 파일 실제 텍스트로 검증).
        self.assertIn("020", self.sql)
        self.assertIn("교체", self.sql)

    def test_does_not_touch_unrelated_objects(self) -> None:
        """023 자체 헤더 계약: (A) 함수 바디 교체 + 방어적 (B) 백필 재실행뿐 -- 020 이 만든
        (C) 트리거·(D) drop, 010 의 컬럼/정책/RPC 는 재선언하지 않는다."""
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


class TieredFunctionSignatureTest(unittest.TestCase):
    """(h, 부분) 시그니처(4-인자) 불변 -- 020(D)가 이미 010 의 2-인자 버전을 drop 했으므로
    023 은 같은 4-인자 시그니처를 create or replace 로 교체할 뿐, 트리거 재배선이 불요하다.
    immutable·고정 search_path 도 020 의 (A) 원본과 동일하게 유지돼야 한다."""

    def setUp(self) -> None:
        self.sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_signature_matches_020_four_arg_form(self) -> None:
        self.assertIn("create or replace function public.grm_classify_483_scope(", self.sql)
        for arg in ("p_est_type text", "p_len integer", "p_doc_text text", "p_firm_name text"):
            self.assertIn(arg, self.sql, f"missing arg: {arg!r}")
        self.assertIn("returns text", self.sql)
        self.assertIn("language sql", self.code)

    def test_immutable_and_search_path_pinned(self) -> None:
        self.assertIn("immutable", self.code)
        self.assertEqual(self.code.count("set search_path = public"), 1)


class TierOrderTest(unittest.TestCase):
    """(a) 3계층 순서 -- 강한 allow -> deny -> 약한 allow 순으로 when 절이 배치돼야 Codex
    F-03 결함(넓은 허용 토큰이 deny 보다 먼저라 기기/식품 라벨을 선점)이 실제로 수리된다.
    순서가 하나라도 뒤바뀌면 반례 8종(아래 CodexCounterexampleBlockedTest)이 다시 샌다."""

    def setUp(self) -> None:
        self.sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        self.regexes = _extract_est_type_regexes(self.sql)

    def test_exactly_three_est_type_tiers(self) -> None:
        self.assertEqual(len(self.regexes), 3, "023 must define exactly 3 est_type tiers")

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
        # ④ 본문 폴백(아래 FallbackFragmentOkUnchangedTest 대상)도 약한 허용목록보다 뒤에
        # 와야 전체 6-분기 순서(①②③④⑤⑥)가 성립한다.
        _strong, _deny, weak_allow = self.regexes
        fallback_idx = self.sql.index("when regexp_replace(coalesce(p_doc_text, '')")
        self.assertLess(self.sql.index(weak_allow), fallback_idx)


class StrongAllowNarrowTokenTest(unittest.TestCase):
    """(b) 강한 허용목록(①)에는 넓은 토큰이 없어야 한다 -- 있으면 그 토큰이 부정목록보다
    먼저 매치해 Codex 반례(예: 'Sterile Medical Device Manufacturer')를 다시 통과시킨다.
    단 실존 복합 라벨 보존에 필요한 좁은 변형(human tissue/plasma derivative/red cross)은
    예외적으로 ①에 남아 있어야 한다(020 의 "allow 우선" 취지 유지)."""

    def setUp(self) -> None:
        sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        self.strong_allow = _extract_est_type_regexes(sql)[0]

    def test_no_bare_wide_tokens(self) -> None:
        self.assertIsNone(
            re.search(r"plasma(?! derivative)", self.strong_allow),
            "bare 'plasma' (not 'plasma derivative') must not be in strong allow",
        )
        self.assertNotIn("blood", self.strong_allow)
        self.assertIsNone(
            re.search(r"(?<!human )tissue", self.strong_allow),
            "bare 'tissue' (not 'human tissue') must not be in strong allow",
        )
        self.assertNotIn("own label", self.strong_allow)
        self.assertNotIn("(control|contract) testing laborator", self.strong_allow)
        self.assertNotIn("sterile", self.strong_allow)

    def test_narrow_exceptions_still_present(self) -> None:
        self.assertIn("human tissue", self.strong_allow)
        self.assertIn("plasma derivative", self.strong_allow)
        self.assertIn("red cross", self.strong_allow)


class WeakAllowAfterDenyTest(unittest.TestCase):
    """(c) 약한 허용목록(③)은 020 에서 위험했던 넓은 토큰을 그대로 담되, deny(②) 뒤에
    배치되어야만 안전하다 -- 020 은 이 토큰들을 deny 보다 앞에 둬서 결함이 났다."""

    def setUp(self) -> None:
        self.sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        _strong, self.deny, self.weak_allow = _extract_est_type_regexes(self.sql)

    def test_weak_allow_contains_the_formerly_dangerous_wide_tokens(self) -> None:
        for token in ("sterile", "plasma", "blood", "tissue", "nuclear", "own label",
                      "repacker/relabeler", "(control|contract) testing laborator"):
            self.assertIn(token, self.weak_allow, f"missing weak-allow token: {token!r}")

    def test_weak_allow_positioned_after_deny(self) -> None:
        self.assertLess(self.sql.index(self.deny), self.sql.index(self.weak_allow))


class DenylistUnchangedTest(unittest.TestCase):
    """(d) 부정목록(②)은 020 의 부정목록과 byte 단위로 동일해야 한다 -- 023 의 변경 범위는
    허용목록의 강/약 분리뿐, deny 자체는 손대지 않는다는 것이 023 자체 헤더의 명시 계약이다."""

    def test_deny_regex_is_byte_identical_to_020(self) -> None:
        sql020 = _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        sql023 = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        est020 = _extract_est_type_regexes(sql020)
        est023 = _extract_est_type_regexes(sql023)
        self.assertEqual(len(est020), 2, "020 should still define exactly 2 est_type tiers")
        self.assertEqual(len(est023), 3, "023 should define exactly 3 est_type tiers")
        deny_020 = est020[1]
        deny_023 = est023[1]
        self.assertEqual(deny_020, deny_023)
        # 대표 토큰으로 신원도 재확인(우연한 빈 문자열 일치 등 사고 방지).
        self.assertIn("institutional review board", deny_020)


class FallbackFragmentOkUnchangedTest(unittest.TestCase):
    """(e) ④ 본문/업체명 폴백 · ⑤ fragment 임계(30자) · ⑥ ok 가 020 과 핵심 정규식 문자열
    수준에서 동일해야 한다(주석의 원문자 번호만 020=③④, 023=④⑤로 바뀌었을 뿐)."""

    def setUp(self) -> None:
        self.code020 = _strip_sql_comments(
            _ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8")
        )
        self.code023 = _strip_sql_comments(
            _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        )
        self.block020 = _fallback_fragment_ok_block(self.code020)
        self.block023 = _fallback_fragment_ok_block(self.code023)

    def test_fallback_fragment_ok_block_is_byte_identical_to_020(self) -> None:
        self.assertEqual(self.block020, self.block023)

    def test_fragment_threshold_is_still_30(self) -> None:
        self.assertIn("< 30", self.block023)

    def test_ends_with_ok_else_branch(self) -> None:
        self.assertIn("else 'ok'", self.block023)

    def test_ffdca_and_form_header_masked_before_body_signals(self) -> None:
        self.assertIn("cosmetic act", self.block023.lower())
        self.assertIn("food\\s+and\\s+drug\\s+administra", self.block023)
        self.assertIn("regexp_replace", self.block023)

    def test_body_signal_guarded_by_pharma_negative_lookup(self) -> None:
        self.assertIn("!~*", self.block023)

    def test_uses_postgres_word_boundary_not_pcre(self) -> None:
        self.assertIn(r"\y", self.block023)
        self.assertNotIn(r"\b", self.block023)


class PreservedCompositeLabelTest(unittest.TestCase):
    """(f) 실존 복합 라벨 4종(020 의 allow-first 설계가 지키려던 라벨)이 023 주석에 보존
    목록으로 명시돼 있고, 각각 실제로 강한 허용목록(①) 정규식에 매치하는지 파이썬 re 로
    검증한다(SQL `~*`/`\\y` -> 파이썬 re.I/`\\b` 치환은 _pg_regex 재사용)."""

    def setUp(self) -> None:
        self.sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        strong_allow = _extract_est_type_regexes(self.sql)[0]
        self.strong_allow_re = _pg_regex(strong_allow)

    def test_labels_documented_in_file_comments(self) -> None:
        for label in _PRESERVED_COMPOSITE_LABELS:
            self.assertIn(label, self.sql, f"preserved label not documented: {label!r}")

    def test_labels_actually_match_strong_allow_regex(self) -> None:
        for label in _PRESERVED_COMPOSITE_LABELS:
            with self.subTest(label=label):
                self.assertIsNotNone(
                    self.strong_allow_re.search(label),
                    f"{label!r} must match the strong-allow (①) regex",
                )


class CodexCounterexampleBlockedTest(unittest.TestCase):
    """(g) Codex 감사가 지목한 반례 8종 -- 020 에서는 넓은 허용 토큰(sterile/blood/plasma/
    tissue/own label/contract testing laborator)이 부정목록보다 먼저라 전부 'ok' 로 새는
    경로였다(실존 라벨 0건이지만 방어가 없었다). 023 에서는 ①(강한 allow)에 비매치 &
    ②(deny)에 매치해야 하고, when/case 순서상 그 조합이 곧 함수가 'non_pharma' 로 가는
    경로다(①이 스킵되고 ②가 히트하면 ③은 평가되지 않는다)."""

    def setUp(self) -> None:
        sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
        strong_allow, deny, _weak_allow = _extract_est_type_regexes(sql)
        self.strong_allow_re = _pg_regex(strong_allow)
        self.deny_re = _pg_regex(deny)

    def test_counterexamples_miss_strong_allow_and_hit_deny(self) -> None:
        for label in _CODEX_COUNTEREXAMPLES:
            with self.subTest(label=label):
                self.assertIsNone(
                    self.strong_allow_re.search(label),
                    f"{label!r} must NOT match strong allow (① regression -> would stay 'ok')",
                )
                self.assertIsNotNone(
                    self.deny_re.search(label),
                    f"{label!r} must match deny (② -> classified 'non_pharma')",
                )


class TieredBackfillTest(unittest.TestCase):
    """(h) (B) 방어적 재정렬 백필 -- 020(B)와 동형: 문서 단위 string_agg, FDA 483 한정,
    establishment_type 은 raw_json 캐스트로 추출, firm_name 폴백, 멱등 UPDATE."""

    def setUp(self) -> None:
        self.code = _strip_sql_comments(
            _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
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

    def test_backfill_is_an_idempotent_update_not_an_insert(self) -> None:
        self.assertIn("update public.findings f", self.code)
        self.assertNotIn("insert into public.findings", self.code)

    def test_backfill_matches_020_shape_byte_for_byte(self) -> None:
        """"020(B)와 동형" 의 가장 강한 증거 -- CTE+UPDATE 골격이 020 과 byte 일치."""
        code020 = _strip_sql_comments(_ALLOWLIST_MIGRATION_PATH.read_text(encoding="utf-8"))
        start_marker = "with doc_text as ("
        end_marker = "and f.source = 'FDA 483';"
        b020 = code020[code020.index(start_marker): code020.index(end_marker) + len(end_marker)]
        b023 = self.code[self.code.index(start_marker): self.code.index(end_marker) + len(end_marker)]
        self.assertEqual(b020, b023)


# ---------------------------------------------------------------------------
# Behavioural pin: 023 의 전체 6-분기 판정을 파이썬으로 재구현해 실제 행 위에서 pin 한다
# (020 의 옛 RuleSemanticsTest 계승 -- 이제 소스는 023). 정규식 문자열은 023 파일에서 직접
# 읽으므로 마이그레이션 쪽 드리프트가 여기서 즉시 깨진다.
# ---------------------------------------------------------------------------

class TieredRuleSemanticsTest(unittest.TestCase):
    """023 회귀 계약(023 자체 헤더 명시): FDA 실존 어휘의 분류 결과는 020 과 전량 동일해야
    한다 -- 바뀌는 것은 실존하지 않는 위험 라벨의 방어뿐이다. 아래는 020 의 RuleSemanticsTest
    가 pin 했던 모든 실측 행을 023 의 3계층 로직으로 재확인하고, Codex 반례 8종이 실제로
    'non_pharma' 로 바뀌는지(수리 목표)까지 end-to-end 로 확인한다."""

    # 020/023 공유(byte 동일, FallbackFragmentOkUnchangedTest 로 확인됨) -- ④ 본문 폴백 정규식.
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
        sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
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
        sql = _TIERED_MIGRATION_PATH.read_text(encoding="utf-8")
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
        # generic est_type and are real pharma. These must survive under 023 too.
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

    def test_combination_labels_survive_the_denylist(self) -> None:
        # Strong-allow-first is what makes these pass -- they contain deny tokens too.
        doc = "Batch record review for drug products was not adequate."
        for est in _PRESERVED_COMPOSITE_LABELS:
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
        """023 자체 헤더의 수리 목표(검증 ①) -- 반례 8종이 실제로 'non_pharma' 로 분류되는지
        전체 6-분기 파이프라인으로 확인한다(CodexCounterexampleBlockedTest 의 개별 정규식
        매치 확인을 완결된 함수 출력 수준까지 끌어올린 end-to-end 증거)."""
        doc = "Observation text with no decisive signal either way."
        for est in _CODEX_COUNTEREXAMPLES:
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "non_pharma")

    def test_previously_safe_narrow_labels_still_resolve_ok(self) -> None:
        # 3계층 분리가 020 에서 이미 안전했던 좁은 실존 라벨에 회귀를 만들지 않았는지(어느
        # 계층을 거치든-①이든 ③이든- 최종 분류만 확인).
        doc = "Routine inspection observation with no other decisive signal."
        for est in ("Blood Bank", "Plasma Derivative Product Manufacturer",
                    "Human Tissue Establishment", "Contract Testing Laboratory",
                    "Repacker/Relabeler"):
            with self.subTest(est=est):
                self.assertEqual(self._classify(est, len(doc), doc, "X"), "ok")


# ---------------------------------------------------------------------------
# 020/023 과 무관하게 유지되는 하류·상류 계약(대상 파일이 바뀌지 않았으므로 변경 없음).
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
