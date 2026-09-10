#!/usr/bin/env python3
"""FIND-1 A-S2 WL scope 분류 마이그레이션 tests — 033_findings_wl_scope.sql.

오프라인 소스텍스트 검사만 (실 네트워크·실 Postgres 없음) — 020/023/024 scope 테스트와
동형. WL 분류기 함수·트리거 WL 경로·소급 백필의 구조 계약과, 483 경로 불변(회귀 0)을 고정한다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_WL_SCOPE_PATH = _MIGRATIONS_DIR / "033_findings_wl_scope.sql"


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


class WlScopeMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_WL_SCOPE_PATH.is_file(), f"missing {_WL_SCOPE_PATH}")
        self.sql = _WL_SCOPE_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _WL_SCOPE_PATH.read_bytes())

    def test_reversible_flag_not_delete(self) -> None:
        # 삭제 아닌 플래그(scope_status) — 되돌림 가능.
        self.assertIn("scope_status", self.code)
        self.assertIn("되돌", self.sql)
        self.assertNotIn("delete from public.findings", self.code.lower())

    def test_no_new_status_value(self) -> None:
        # ok/non_pharma/fragment 3종만 — 4번째 상태값 도입 금지(스코프 정책 미확정).
        self.assertIn("'non_pharma'", self.code)
        self.assertIn("'fragment'", self.code)
        self.assertIn("'ok'", self.code)
        self.assertNotIn("out_of_gmp_scope", self.code)

    def test_classifier_function_defined(self) -> None:
        self.assertIn(
            "create or replace function public.grm_classify_wl_scope(", self.code
        )
        # 문서 본문 축(est_type 없음 — WL 은 483 분류기를 그대로 못 쓴다).
        self.assertIn("p_doc_text", self.code)
        self.assertIn("p_firm", self.code)

    def test_pharma_signal_keeps_ok_asymmetric(self) -> None:
        # 제약/의약품/생물의약품/미승인drug 신호 → ok (비대칭 안전). 대표 토큰 몇 개 고정.
        for token in ("drug product", "biolog", "section 505", "unapproved", "OTC"):
            self.assertIn(token, self.sql, f"pharma signal token missing: {token!r}")

    def test_nonpharma_signal_tokens(self) -> None:
        # 기기(820)/식품/화장품/IRB/임상 신호 → non_pharma. 대표 토큰 고정.
        for token in ("21 CFR 820", "medical device", "cosmetic", "IRB", "clinical investigat"):
            self.assertIn(token, self.sql, f"non_pharma signal token missing: {token!r}")

    def test_trigger_adds_wl_branch_preserving_483(self) -> None:
        # 483 경로 보존(회귀 0) + WL elsif 경로 추가.
        self.assertIn("if new.source = 'FDA 483' then", self.code)
        self.assertIn("public.grm_classify_483_scope(", self.code)  # 483 분류기 그대로 호출
        self.assertIn("elsif new.source = 'FDA Warning Letter' then", self.code)
        self.assertIn("public.grm_classify_wl_scope(", self.code)
        # WL 은 wl_body(파서 원천)를 문서 본문으로 쓴다.
        self.assertIn("wl_body_full", self.code)
        self.assertIn("wl_body_excerpt", self.code)

    def test_trigger_defensive_null_default_ok(self) -> None:
        # raw_signal 미가시 등 방어 상황 → 안전측 'ok'(신규 숨김 방지).
        self.assertIn("new.scope_status := 'ok';", self.code)

    def test_backfill_updates_scope_only_when_changed(self) -> None:
        # 소급 백필: scope_status 만, 바뀌는 행만(불필요 write·부하 방지).
        self.assertIn("update public.findings", self.code)
        self.assertIn("set scope_status = r.new_scope", self.code)
        self.assertIn("is distinct from", self.code)
        self.assertIn("source = 'FDA Warning Letter'", self.code)

    def test_trigger_rewired(self) -> None:
        self.assertIn("drop trigger if exists findings_scope_status_biu", self.code)
        self.assertIn("before insert on public.findings", self.code)


if __name__ == "__main__":
    unittest.main()


def _latest_wl_scope_sql() -> tuple[Path, str]:
    """`grm_classify_wl_scope` 를 정의하는 **가장 최신** 마이그레이션(= 프로덕션 정본).

    ★파일명을 손으로 적어 두면 다음 `create or replace` 때 테스트가 낡은 파일을 붙들고
      초록으로 남는다 — 이 저장소가 이미 여러 번 당한 표류(CI shim 손열거·046 계약 테스트).
    """
    hits = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        if re.search(r"create\s+or\s+replace\s+function\s+public\.grm_classify_wl_scope",
                     body, re.I):
            hits.append((path, body))
    assert hits, "grm_classify_wl_scope 를 정의하는 마이그레이션이 없다"
    return hits[-1]


class WlDeviceScopeTest(unittest.TestCase):
    """★2026-08-03. 공개 WL findings 에 **순수 의료기기 QSR 지적 94건**이 섞여 있었다
    (Abiomed 심장펌프·Integra·Sol-Millennium 주사기·Robbins·Edge Biologicals 체외진단 등
    22개 업체 39문서). 내용은 전부 21 CFR 820 이다.

    ★033 이 못 잡은 이유는 **규칙 순서**다. ①(제약 신호 → ok)이 ②(기기 → non_pharma)보다
      먼저 평가되는데 ① 의 어휘가 넓어 기기 편지를 먼저 삼켰다 — `\ysterile`(sterile wound
      dressings), `\ybiolog`(Edge **Biolog**icals), `pharmaceutic`(**상호** "Aquavit
      Pharmaceuticals" — FDA 는 이 회사 제품을 201(h) 로 device 라고 명시 판정했다).
      즉 ② 의 기기 토큰에 **도달조차 못 했다**.
    """

    def setUp(self) -> None:
        self.path, self.sql = _latest_wl_scope_sql()
        self.code = _strip_sql_comments(self.sql)

    def test_latest_migration_is_the_device_rule(self) -> None:
        """정본이 051 이후여야 한다 — 033 이 정본이면 기기 규칙이 프로덕션에 없다는 뜻."""
        self.assertNotEqual(self.path.name, "033_findings_wl_scope.sql",
                            "기기 규칙(051)이 정본을 supersede 하지 못했다")

    def test_device_rule_precedes_the_pharma_allow_rule(self) -> None:
        """★핵심 계약. 기기 규칙이 제약 허용 규칙보다 **먼저** 와야 한다 — 뒤에 두면
        `sterile`·`biolog`·`pharmaceutic` 에 먼저 걸려 도달하지 못한다(033 실패 원인)."""
        device_at = self.code.find("201\(h\)")
        pharma_at = self.code.find("unapproved.{0,4}drug")
        self.assertGreater(device_at, -1, "기기 정의 조항(201(h)) 규칙이 없다")
        self.assertGreater(pharma_at, -1, "제약 허용 규칙이 없다")
        self.assertLess(device_at, pharma_at,
                        "기기 규칙이 제약 허용 규칙보다 뒤에 있다 — 도달하지 못한다")

    def test_device_rule_requires_both_conditions(self) -> None:
        """기기 근거만으로 배제하지 않는다 — 의약품 특정 근거가 **없을 때만**."""
        self.assertIn("!~*", self.code, "부정 조건(의약품 근거 없음)이 빠졌다")
        for token in ("21 CFR ?21[01]", "drug product", "503\(b\)"):
            self.assertIn(token, self.code, f"의약품 특정 근거 토큰 누락: {token!r}")

    def test_cgmp_is_not_drug_evidence_in_the_device_rule(self) -> None:
        """★21 CFR 820 이 곧 **기기 CGMP** 라 기기 편지도 그 표현을 쓴다. 이걸 의약품 근거로
        넣으면 Synovo·InfuTronix·Magnolia 처럼 단어 하나로 기기 편지가 보호받는다."""
        device_clause = self.code.split("!~*", 1)[1].split("then", 1)[0]
        self.assertNotIn("current good manufacturing", device_clause.lower())

    def test_firm_name_is_not_used_by_the_device_rule(self) -> None:
        """★상호는 판정 축이 아니다 — "Pharmaceuticals" 라는 이름의 기기 제조사가 실재한다."""
        device_clause = self.code.split("select case", 1)[1].split("then", 1)[0]
        self.assertNotIn("p_firm", device_clause,
                         "기기 규칙이 상호를 본다 — 본문(p_doc_text)만 써야 한다")

    def test_unapproved_drug_policy_is_untouched(self) -> None:
        """★033 ①(CODEX 검수 rubric §4) 정책 불변 — 미승인drug·OTC 는 계속 'ok'."""
        for token in ("unapproved.{0,4}drug", "section 505", "OTC"):
            self.assertIn(token, self.sql, f"미승인drug 정책 토큰이 사라졌다: {token!r}")

    def test_hctp_is_not_excluded_by_body_signal(self) -> None:
        """★024 가 근거와 함께 기각한 축이다(est_type 만 쓰고 본문 신호는 안 쓴다).
        WL 에는 est_type 이 없어 본문으로 배제하면 그 결정을 뒤집는 셈이 된다."""
        self.assertNotIn("1271", self.code)
        self.assertNotIn("umbilical", self.code.lower())

    def test_backfill_updates_only_changed_rows(self) -> None:
        self.assertIn("is distinct from", self.code)
        self.assertIn("update public.findings", self.code.lower())
        self.assertNotIn("delete from public.findings", self.code.lower())

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", self.path.read_bytes())


class WlBimoDeviceScopeTest(unittest.TestCase):
    """★2026-09-10. BIMO(Bioresearch Monitoring)가 **기기 임상시험자·IRB** 에게 보낸 경고서한이
    제약 범위로 공개돼 있었다 — Stephen J. Fallon, Ph.D.(HIV 자가검사 기기 연구, 21 CFR 812)
    findings 4건, MIT(IRB, Part 56 + Part 812) 2건. 내용은 informed consent·IRB 기록이지 GMP 가
    아니다.

    ★새는 구멍은 **발신 기관명**이다. CBER 소관 기기라 서한에 "Center for **Biolog**ics
      Evaluation and Research" 가 찍히고, ① 의 `\ybiolog` 가 거기 걸려 'ok' 가 먼저 확정된다.
      051 ⓪ 도 못 잡는다 — 임상시험자 서한은 QSR(820)·201(h) 를 인용하지 않는다.
      해법은 051 과 같은 자리(① 앞)에 좁은 규칙 ⓪′(21 CFR 812 ∧ 약물 임상·의약품 근거 전무)를
      두는 것이다.
    """

    def setUp(self) -> None:
        self.path, self.sql = _latest_wl_scope_sql()
        self.code = _strip_sql_comments(self.sql)

    def _bimo_clause(self) -> str:
        """⓪′ 분기의 본문(when … then)."""
        at = self.code.find("812")
        self.assertGreater(at, -1, "BIMO 기기 임상(21 CFR 812) 규칙이 없다")
        start = self.code.rfind("when", 0, at)
        end = self.code.find("then", at)
        return self.code[start:end]

    def test_latest_migration_carries_the_bimo_rule(self) -> None:
        """정본이 081 이후여야 한다 — 051 이 정본이면 BIMO 규칙이 프로덕션에 없다는 뜻."""
        self.assertIn("812", self.code, "BIMO 기기 임상 규칙(081)이 정본을 supersede 하지 못했다")
        self.assertIn("investigational device", self.code)

    def test_bimo_rule_precedes_the_pharma_allow_rule(self) -> None:
        """★핵심 계약. ① 의 `biolog` 에 걸리기 **전에** 판정돼야 한다."""
        bimo_at = self.code.find("812")
        pharma_at = self.code.find("unapproved.{0,4}drug")
        self.assertGreater(pharma_at, -1, "제약 허용 규칙이 없다")
        self.assertLess(bimo_at, pharma_at, "BIMO 규칙이 제약 허용 규칙보다 뒤에 있다 — 도달하지 못한다")

    def test_device_qsr_rule_is_kept_and_still_first(self) -> None:
        """051 ⓪(기기 QSR)은 그대로 첫 분기다 — 081 은 그 뒤에 더할 뿐 빼지 않는다."""
        self.assertLess(self.code.find("201\(h\)"), self.code.find("812"))

    def test_bimo_rule_requires_both_conditions(self) -> None:
        """기기 임상 근거만으로 배제하지 않는다 — 약물 임상(312)·의약품 특정 근거가 **없을 때만**.
        약물 임상시험자 서한은 033 이 명시한 대로 ① 의 investigational drug 로 'ok' 에 남는다."""
        clause = self._bimo_clause()
        self.assertIn("!~*", clause, "부정 조건(약물 임상·의약품 근거 없음)이 빠졌다")
        for token in ("21 CFR ?312", "investigational (new )?drug", "drug product"):
            self.assertIn(token, clause, f"약물 임상·의약품 근거 토큰 누락: {token!r}")

    def test_biolog_is_not_drug_evidence_in_the_bimo_rule(self) -> None:
        """★`biolog` 를 (b) 에 넣으면 CBER 기관명 하나로 다시 보호받는다 — 그 토큰이 구멍이다.
        생물의약품 임상은 IND(312) 를 인용하므로 312 로 보호된다."""
        negative = self._bimo_clause().split("!~*", 1)[1]
        self.assertNotIn("biolog", negative.lower())
        self.assertNotIn("bioresearch", negative.lower())

    def test_firm_name_is_not_used_by_the_bimo_rule(self) -> None:
        """★상호는 판정 축이 아니다(051 과 동일 원칙)."""
        self.assertNotIn("p_firm", self._bimo_clause())

    def test_unapproved_drug_and_device_policies_are_untouched(self) -> None:
        for token in ("unapproved.{0,4}drug", "section 505", "OTC", "201\(h\)", "503\(b\)"):
            self.assertIn(token, self.sql, f"기존 정책 토큰이 사라졌다: {token!r}")

    def test_backfill_updates_only_changed_rows(self) -> None:
        self.assertIn("is distinct from", self.code)
        self.assertIn("update public.findings", self.code.lower())
        self.assertNotIn("delete from public.findings", self.code.lower())
