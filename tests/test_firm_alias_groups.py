#!/usr/bin/env python3
"""업체 별칭 26그룹 tests (105_firm_alias_groups.sql, grm_findings.FIRM_KEY_ALIASES, web/render.py).
오프라인: SQL 시드/본문 계약 + 파이썬 사전 일치만 본다."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import grm_findings

_MIG = Path(__file__).resolve().parent.parent / "web" / "migrations"
_SQL = (_MIG / "105_firm_alias_groups.sql").read_text(encoding="utf-8")
_P65 = (_MIG / "065_profile_categories_and_repeats.sql").read_text(encoding="utf-8")
_P63 = (_MIG / "063_fda_inspection_firm.sql").read_text(encoding="utf-8")
_RENDER = (Path(__file__).resolve().parent.parent / "web" / "render.py").read_text(encoding="utf-8")


def _seed() -> dict[str, str]:
    block = _SQL.split("insert into public.firm_alias (alias_key, canonical_key) values", 1)[1].split("on conflict", 1)[0]
    return {a.replace("''", "'"): c.replace("''", "'")
            for a, c in re.findall(r"\('((?:[^']|'')*)', '((?:[^']|'')*)'\)", block)}


def _fn(src: str, head: str) -> str:
    i = src.index(head)
    return src[i:src.index("\n$$;", i) + 4]


class AliasSeed(unittest.TestCase):
    def test_sql_seed_equals_python_dict(self):
        self.assertEqual(_seed(), grm_findings.FIRM_KEY_ALIASES)

    def test_26_groups_no_chains_no_self(self):
        d = grm_findings.FIRM_KEY_ALIASES
        self.assertEqual(len(d), 26)
        for a, c in d.items():
            self.assertNotEqual(a, c)
            self.assertNotIn(c, d, f"체인 금지: {a} -> {c}")

    def test_canonical_helper(self):
        self.assertEqual(grm_findings.canonical_firm_key("akorn ag"), "akorn")
        self.assertEqual(grm_findings.canonical_firm_key("akorn"), "akorn")
        self.assertEqual(grm_findings.canonical_firm_key("hospira"), "hospira")

    def test_v2_of_alias_lands_on_canonical(self):
        # 별칭 키 텍스트를 v2 에 다시 넣어도 정본이 나오는 대표 사례(괄호/접미사 계열).
        for alias, canon in (("akorn ag", "akorn"), ("sanofi spa", "sanofi"),
                             ("biocon sdn bhd", "biocon"), ("jd and sn", "jd & sn")):
            self.assertEqual(grm_findings.normalize_firm_name_v2(alias), canon)


class RpcContract(unittest.TestCase):
    def test_profile_body_is_065_with_only_key_compare_changed(self):
        old = _fn(_P65, "create or replace function public.findings_firm_profile(p_firm_key text)")
        new = _fn(_SQL, "create or replace function public.findings_firm_profile(p_firm_key text)")
        self.assertEqual(
            new.replace("firm_key = any(public.grm_firm_group_keys(p_firm_key))", "firm_key = p_firm_key"), old)
        self.assertEqual(new.count("grm_firm_group_keys"), 11)

    def test_inspection_body_is_063_with_only_key_compare_changed(self):
        old = _fn(_P63, "create or replace function public.fda_inspection_firm(p_firm_key text)")
        new = _fn(_SQL, "create or replace function public.fda_inspection_firm(p_firm_key text)")
        self.assertEqual(
            new.replace("i.firm_key = any(public.grm_firm_group_keys(k.key))", "i.firm_key = k.key"), old)

    def test_additive_and_locked_down(self):
        code = "\n".join(l for l in _SQL.splitlines() if not l.strip().startswith("--")).lower()
        self.assertNotRegex(code, r"drop\s+(table|column|function)|alter table public\.findings")
        self.assertIn("alter table public.firm_alias enable row level security", code)
        self.assertIn("revoke all on table public.firm_alias from public, anon, authenticated", code)
        self.assertIn("revoke all on function public.grm_firm_group_keys(text) from public, anon, authenticated", code)


class StaticPages(unittest.TestCase):
    def test_render_groups_and_links_through_canonical(self):
        self.assertIn('key = _canonical_firm_key(d.get("firm_key") or "")', _RENDER)
        self.assertIn('firm_page_slug_by_key.get(_canonical_firm_key(doc.get("firm_key") or ""))', _RENDER)


if __name__ == "__main__":
    unittest.main()
