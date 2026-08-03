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
