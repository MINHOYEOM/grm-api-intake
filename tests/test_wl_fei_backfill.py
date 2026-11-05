#!/usr/bin/env python3
"""110 경고서한(WL) FEI 정확 파싱 백필 tests. 오프라인 -- SQL 계약 + 정규식 동작을 파이썬 re 로 재현한다."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_MIG = Path(__file__).resolve().parent.parent / "web" / "migrations"
_S110 = (_MIG / "110_wl_fei_backfill.sql").read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


def _regexes() -> tuple[str, str]:
    """SQL 안의 두 정규식(회신·통지 식별 문장 / 모든 FEI 표기)을 꺼낸다."""
    pats = re.findall(r"'((?:\(\?:|FEI)[^']*\[0-9\]\{7,10\}\)\\y)'", _code(_S110))
    assert len(pats) == 2, pats
    return pats[0], pats[1]


def _py(pat: str) -> str:
    return pat.replace(r"\y", r"\b")


def parse(text: str):
    """grm_wl_fei_from_text 의 파이썬 재현."""
    resp_p, any_p = _regexes()
    resp = set(re.findall(_py(resp_p), text))
    anyf = set(re.findall(_py(any_p), text, flags=re.I))
    if len(resp) == 1 and len(anyf) == 1 and resp == anyf:
        return next(iter(resp))
    return None


class ParserBehavior(unittest.TestCase):
    def test_response_sentence(self):
        self.assertEqual(parse("Identify your response with FEI 3004153045 and ATTN: Maria."), "3004153045")
        self.assertEqual(parse("Please identify your response with FEI #3014117482. Sincerely"), "3014117482")
        self.assertEqual(parse("Please identify your response with FEI: 3011347036 and ATTN: X"), "3011347036")
        self.assertEqual(parse("Identify your response with FEI 2111173 and ATTN: X"), "2111173")  # 7자리

    def test_notification_sentence(self):
        self.assertEqual(
            parse("Your written notification should refer to Warning Letter #636636 and include FEI: 3010840309. If"),
            "3010840309")

    def test_other_site_mentions_are_ignored(self):
        # 과거 편지의 다른 업체 / 위탁 제조소 / 등록 확인 -- 수신 업체가 아니므로 비운다.
        self.assertIsNone(parse("a Warning Letter was issued to Cipla Limited, Goa FEI 3004081307, citing"))
        self.assertIsNone(parse("manufactured at Gopaldas Visram & Co., Ltd., FEI 3009565091, at Plot No."))
        self.assertIsNone(parse("showed that Shuzy Rock Inc (FEI: 3013835844) was never registered"))

    def test_ambiguous_letters_are_left_empty(self):
        # 식별 문장은 있어도 다른 사이트 FEI 가 섞이면 모호 -> 비움.
        self.assertIsNone(parse("made at X, FEI 3011111111. Identify your response with FEI 3022222222 and ATTN: Y"))
        self.assertIsNone(parse("Identify your response with FEI 3011111111. Identify your response with FEI 3022222222."))

    def test_invalid_lengths_rejected(self):
        self.assertIsNone(parse("Identify your response with FEI 123456 and ATTN: Y"))      # 6자리
        self.assertIsNone(parse("Identify your response with FEI 12345678901 and ATTN: Y"))  # 11자리
        self.assertIsNone(parse("no fei here"))


class MigrationContract(unittest.TestCase):
    def test_additive_only_no_destructive(self):
        code = _code(_S110).lower()
        self.assertNotRegex(code, r"drop\s+(table|column|function)|alter table|delete from")
        self.assertNotRegex(code, r"\binsert into\b")
        # findings 는 fei 한 열만 갱신하고, fei is null · WL 행만 건드린다(멱등).
        self.assertIn("set fei = x.fei", re.sub(r"\s+", " ", code))
        self.assertIn("and f.fei is null", code)
        self.assertIn("f.source = 'fda warning letter'", code)

    def test_no_name_matching(self):
        code = _code(_S110).lower()
        for banned in ("similarity", "levenshtein", "ilike", "firm_name", "firm_key", "soundex"):
            self.assertNotIn(banned, code)

    def test_every_function_locked_down(self):
        code = _code(_S110)
        fns = re.findall(r"create or replace function public\.(\w+)\(([^)]*)\)", code)
        self.assertEqual([n for n, _ in fns],
                         ["grm_wl_fei_from_text", "grm_backfill_wl_fei_since", "grm_backfill_wl_fei", "grm_link_new_findings"])
        for name, args in fns:
            types = ", ".join(a.strip().split()[1] for a in args.split(",") if a.strip())
            self.assertRegex(code, rf"revoke all on function public\.{name}\({re.escape(types)}\) from public, anon, authenticated")

    def test_daily_entry_keeps_existing_keys(self):
        code = re.sub(r"\s+", " ", _code(_S110))
        for key in ("'since'", "'fei_filled'", "'clause_edges'", "'clause_mfds_rule'"):
            self.assertIn(key, code)
        self.assertIn("grm_backfill_wl_fei_since(v_since)", code)
        self.assertIn("grm_backfill_finding_clause_mfds_rule_since(v_since)", code)
        # 순서: fei(483) -> WL fei -> clause -> mfds_rule
        order = [code.index(k) for k in ("grm_backfill_findings_fei_since(v_since)", "grm_backfill_wl_fei_since(v_since)",
                                         "grm_backfill_finding_clause_since(v_since)",
                                         "grm_backfill_finding_clause_mfds_rule_since(v_since)")]
        self.assertEqual(order, sorted(order))


_S112 = (_MIG / "112_wl_fei_backfill_narrow.sql").read_text(encoding="utf-8")


class NarrowedBackfill(unittest.TestCase):
    """112 는 110 과 같은 규칙에서 findings 를 먼저 좁히기만 한다."""

    def test_signature_and_lockdown(self):
        code = _code(_S112)
        self.assertEqual(re.findall(r"create or replace function public\.(\w+)\(([^)]*)\)", code),
                         [("grm_backfill_wl_fei_since", "p_since timestamptz")])
        self.assertIn("returns integer", code)
        self.assertIn("revoke all on function public.grm_backfill_wl_fei_since(timestamptz) from public, anon, authenticated", code)

    def test_findings_narrowed_before_raw_signals(self):
        code = re.sub(r"\s+", " ", _code(_S112))
        self.assertIn("with t as materialized ( select distinct f0.raw_signal_id from public.findings f0 "
                      "where f0.source = 'FDA Warning Letter' and f0.fei is null and f0.ingested_at >= p_since )", code)
        self.assertIn("from t join public.raw_signals r on r.raw_signal_id = t.raw_signal_id", code)

    def test_same_write_rule_as_110(self):
        norm = lambda s: re.sub(r"\s+", " ", s[s.index("update public.findings f"):s.index("get diagnostics")])
        tail = lambda s: norm(s)[norm(s).index("where f.raw_signal_id"):]
        self.assertEqual(tail(_code(_S112)), tail(_code(_S110)))


if __name__ == "__main__":
    unittest.main()
