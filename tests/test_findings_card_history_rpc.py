#!/usr/bin/env python3
"""120_findings_card_history.sql -- offline text-contract tests (014 test 와 같은 계열).

DB 없이 SQL 파일을 텍스트 계약으로 검사한다: 서명/security definer/search_path, 반환 키 화이트리스트(집계만),
원문 필드 미노출, 009 슬라이스 괄호 함정, 한도, 세는 기준(앞선·자기 제외·형제 제외·scope ok), revoke→grant,
가산만(기존 함수 재정의 금지). 라이브 검증은 비서실의 적용 단계 몫.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

_MIG_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_PATH = _MIG_DIR / "120_findings_card_history.sql"


def _strip(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


class FileTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(_PATH.is_file())
        self.sql = _PATH.read_text(encoding="utf-8")

    def test_no_crlf(self):
        self.assertNotIn(b"\r\n", _PATH.read_bytes())

    def test_has_comments(self):
        self.assertGreaterEqual(self.sql.count("--"), 20)

    def test_number_is_next_after_119(self):
        # 119 는 #1350 이 사용 — 이 파일은 120. 번호 충돌 회귀 방지.
        self.assertEqual(sorted(p.name for p in _MIG_DIR.glob("120_*.sql")), ["120_findings_card_history.sql"])

    def test_documents_009_pitfall(self):
        self.assertIn("009", self.sql)
        self.assertIn("42601", self.sql)


class ShapeTest(unittest.TestCase):
    def setUp(self):
        self.sql = _PATH.read_text(encoding="utf-8")
        m = re.search(r"create or replace function public\.findings_card_history\((.*?)\)\s*returns jsonb"
                      r"(.*?)as \$\$(.*?)\$\$;", self.sql, re.DOTALL)
        self.assertIsNotNone(m)
        self.args, self.header, self.body = m.group(1), m.group(2), m.group(3)

    def test_signature_and_security(self):
        self.assertIn("p_cards jsonb", self.args)
        self.assertIn("p_exclude text[]", self.args)
        for token in ("language sql", "stable", "security definer", "set search_path = public"):
            self.assertIn(token, self.header)

    def test_read_only_no_dml(self):
        low = _strip(self.sql).lower()
        for bad in ("insert ", "update ", "delete ", "truncate", "create table", "create temp", "alter ", "drop "):
            self.assertNotIn(bad, low.replace("create or replace function", ""), bad)

    def test_returned_keys_are_aggregates_only(self):
        obj = self.body[self.body.index("jsonb_build_object("):self.body.index("order by c.ord")]
        keys = set(re.findall(r"'([a-z_]+)'\s*,", obj))
        self.assertEqual(keys, {"id", "fei_n", "fei_last", "name_n", "name_last", "name_sites"})

    def test_forbidden_text_fields_absent(self):
        for f in ("finding_text", "finding_text_ko", "evidence_url", "raw_json", "row_json", "firm_name", "site_name"):
            self.assertNotIn(f, self.body, f)

    def test_scope_ok_everywhere(self):
        self.assertEqual(self.body.count("scope_status = 'ok'"), 2)

    def test_only_earlier_not_self_not_siblings(self):
        self.assertEqual(self.body.count("f.published_date < c.card_date"), 2)  # 같은 날은 안 센다
        self.assertEqual(self.body.count("f.document_id <> c.id"), 2)
        self.assertEqual(self.body.count("f.document_id not in (select document_id from ex)"), 2)

    def test_counts_distinct_documents_not_findings(self):
        self.assertEqual(self.body.count("count(distinct f.document_id)"), 2)
        self.assertNotIn("count(*)", self.body)

    def test_fei_exact_and_format_gate(self):
        self.assertIn("f.fei = c.fei", self.body)
        self.assertIn("'^[0-9]{7,10}$'", self.body)
        self.assertIn("c.fei is not null", self.body)

    def test_name_group_exact_match_via_alias_function_no_fuzzy(self):
        self.assertIn("f.firm_key = any(public.grm_firm_group_keys(c.firm_key))", self.body)
        low = self.body.lower()
        for fuzzy in (" like ", "ilike", "similarity", "levenshtein", "%"):
            self.assertNotIn(fuzzy, low, fuzzy)

    def test_array_slice_parenthesized_and_limits(self):
        self.assertIn("(coalesce(p_exclude, '{}'::text[]))[1:500]", self.body)
        self.assertNotRegex(self.body, r"coalesce\([^()]*\)\[1:")
        self.assertIn("t.ord <= 200", self.body)

    def test_exclude_nulls_filtered(self):  # NOT IN + NULL 함정
        self.assertIn("where x.document_id is not null", self.body)

    def test_input_validation(self):
        self.assertIn("jsonb_typeof(p_cards) = 'array'", self.body)
        self.assertIn("'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'", self.body)

    def test_ordered_by_input_order(self):
        self.assertIn("order by c.ord", self.body)


class GrantsTest(unittest.TestCase):
    def setUp(self):
        self.sql = _PATH.read_text(encoding="utf-8")

    def test_revoke_then_grant(self):
        r = "revoke all on function public.findings_card_history(jsonb, text[]) from public;"
        g = "grant execute on function public.findings_card_history(jsonb, text[]) to anon, authenticated;"
        self.assertIn(r, self.sql)
        self.assertIn(g, self.sql)
        self.assertLess(self.sql.index(r), self.sql.index(g))

    def test_no_grant_to_public_or_service_only_widening(self):
        self.assertNotRegex(self.sql, r"grant\s+[^;]*\bto\s+public\b")


class AdditiveOnlyTest(unittest.TestCase):
    def test_defines_exactly_one_function_and_touches_no_existing(self):
        code = _strip(_PATH.read_text(encoding="utf-8"))
        defs = re.findall(r"create or replace function (public\.\w+)", code)
        self.assertEqual(defs, ["public.findings_card_history"])
        self.assertNotIn("drop function", code.lower())

    def test_prerequisites_exist(self):
        for n in ("002_findings.sql", "010_findings_scope_purity.sql", "013_findings_firm_key.sql",
                  "101_findings_fei.sql", "105_firm_alias_groups.sql", "014_findings_firm_counts.sql"):
            self.assertTrue((_MIG_DIR / n).is_file(), n)

    def test_014_untouched(self):
        self.assertIn("create or replace function public.findings_firm_counts(p_firm_keys text[])",
                      (_MIG_DIR / "014_findings_firm_counts.sql").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
