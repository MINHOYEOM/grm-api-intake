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
