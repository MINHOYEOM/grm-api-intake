#!/usr/bin/env python3
"""지식그래프 1·2순위 tests (101_findings_fei.sql, 102_firm_name_normalize_v2.sql,
grm_findings.normalize_firm_name_v2). 오프라인: 파이썬 함수 동작 + SQL 텍스트 계약만 본다."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import grm_findings

_MIG = Path(__file__).resolve().parent.parent / "web" / "migrations"
_FEI_SQL = (_MIG / "101_findings_fei.sql").read_text(encoding="utf-8")
_V2_SQL = (_MIG / "102_firm_name_normalize_v2.sql").read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


# 2026-11-03 라이브 시뮬레이션에서 합쳐진 26그룹 중 대표 + 오병합 방지 사례.
_MERGE_FIXTURES = (
    ("Bausch and Lomb Incorporated", "bausch & lomb"),
    ("Bausch & Lomb, Inc.", "bausch & lomb"),
    ("Bausch & Lomb Inc.", "bausch & lomb"),
    ("AGC Biologics S.p.A.", "agc biologics"),
    ("AGC Biologics, Inc.", "agc biologics"),
    ("Akorn AG", "akorn"),
    ("Akorn, Inc.", "akorn"),
    ("Aurobindo Pharma Ltd. (Unit VII)", "aurobindo pharma"),
    ("Central Admixture Pharmacy Services, Inc., (CAPS)", "central admixture pharmacy services"),
    ("College Pharmacy Incorporated", "college pharmacy"),
    ("Divi&#039;s Laboratories", "divi's laboratories"),
    ("Imprimis NJOF, -LLC", "imprimis njof"),
    ("JD and SN Inc", "jd & sn"),
    ("Biocon Sdn. Bhd.", "biocon"),
    ("The Wellness Pharmacy, LLC", "wellness pharmacy"),
    ("The American National Red Cross", "american national red cross"),
    ("Sanofi S.p.A.", "sanofi"),
    ("Pharmacia and Upjohn Company, LLC", "pharmacia & upjohn"),
)
# 앞쪽 단어는 접미사가 아니다 / 한글은 v1 그대로 / 빈 입력.
_KEEP_FIXTURES = (
    ("AG Hair", "ag hair"),
    ("(주)성일바이오엑스", grm_findings.normalize_firm_name("(주)성일바이오엑스")),
    ("한미약품(주)", grm_findings.normalize_firm_name("한미약품(주)")),
    ("", ""),
    (None, ""),
)


class FirmNameV2(unittest.TestCase):
    def test_merge_fixtures(self):
        for raw, want in _MERGE_FIXTURES:
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_firm_name_v2(raw), want)

    def test_keep_fixtures(self):
        for raw, want in _KEEP_FIXTURES:
            with self.subTest(raw=raw):
                self.assertEqual(grm_findings.normalize_firm_name_v2(raw), want)

    def test_hangul_names_keep_parens(self):
        self.assertIn("주", grm_findings.normalize_firm_name_v2("(주)종근당"))

    def test_v1_unchanged(self):
        self.assertEqual(grm_findings.normalize_firm_name("Bausch and Lomb Incorporated"),
                         "bausch and lomb incorporated")

    def test_idempotent(self):
        for raw, _ in _MERGE_FIXTURES:
            once = grm_findings.normalize_firm_name_v2(raw)
            self.assertEqual(grm_findings.normalize_firm_name_v2(once), once)


class FeiMigration(unittest.TestCase):
    def test_additive_only(self):
        code = _code(_FEI_SQL).lower()
        self.assertNotRegex(code, r"drop\s+(table|column|function)")
        self.assertIn("add column if not exists fei text", code)
        self.assertRegex(code, r"\^\[0-9\]\{7,10\}\$")
        self.assertIn("and f.fei is null", code)  # 멱등: 채운 행은 건드리지 않는다
        self.assertIn("source = 'fda 483'", code)
        self.assertNotIn("create or replace function public.findings_", code)  # 기존 RPC 불변

    def test_backfill_not_callable_by_clients(self):
        self.assertRegex(_code(_FEI_SQL), r"revoke all on function public\.grm_backfill_findings_fei\(\) from public, anon, authenticated")


class FirmV2Migration(unittest.TestCase):
    def test_does_not_touch_firm_key(self):
        code = _code(_V2_SQL).lower()
        self.assertNotIn("alter table", code)
        self.assertNotIn("generated always", code)
        self.assertIn("create or replace function public.grm_normalize_firm_name_v2", code)
        self.assertIn("immutable", code)
        self.assertRegex(code, r"revoke all on function public\.grm_normalize_firm_name_v2")

    def test_rules_present_in_order(self):
        code = _code(_V2_SQL)
        order = [
            "[가-힣]", r"\([^)]*\)", "[.,]", r"\s+and\s+", r"\s*&\s*",
            "^the", "incorporated|plc|ag|bv|nv|spa|sas|sarl|kg|bhd", "sdn|pte|pty",
        ]
        pos = [code.find(tok) for tok in order]
        self.assertTrue(all(p >= 0 for p in pos), pos)
        # 한글 가드가 맨 앞, 나머지 변환은 안쪽(앞 순서)부터 실행되므로 텍스트상 역순으로 쓰인다.
        self.assertEqual(pos[0], min(pos))
        self.assertLess(code.find("^the"), code.find("sdn|pte|pty"))
        self.assertLess(code.find(r"\([^)]*\)"), code.find("[.,]"))


if __name__ == "__main__":
    unittest.main()
