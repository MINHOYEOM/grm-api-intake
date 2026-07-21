#!/usr/bin/env python3
"""FIND-1 rejected 공개 게이트 숨김 마이그레이션 tests — 034_findings_hide_rejected.sql.

오프라인 소스텍스트 검사만 (실 네트워크·실 Postgres 없음). 공개 read RLS 와 findings_stats
집계가 review_status='rejected' 를 배제하는지, 그리고 017 top_firms(firm_key group by +
대표 표시명 lateral)를 되돌리지 않았는지(025 헤더 경고와 동일)를 고정한다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_HIDE_PATH = _MIGRATIONS_DIR / "034_findings_hide_rejected.sql"


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


class HideRejectedMigrationFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_HIDE_PATH.is_file(), f"missing {_HIDE_PATH}")
        self.sql = _HIDE_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _HIDE_PATH.read_bytes())

    def test_rls_policy_excludes_rejected(self) -> None:
        self.assertIn("create policy findings_public_read", self.code)
        self.assertIn("scope_status = 'ok'", self.code)
        self.assertIn("review_status <> 'rejected'", self.code)
        # 기존 번역 게이트도 유지(회귀 0).
        self.assertIn("finding_text_ko <> ''", self.code)
        self.assertIn("finding_language = 'KO'", self.code)

    def test_stats_applies_filter_to_every_scope_ok_clause(self) -> None:
        # 모든 `scope_status = 'ok'` 는 `review_status <> 'rejected'` 를 동반해야 한다
        # (일부만 걸면 집계 불일치). raw_signals count 는 findings 가 아니라 제외.
        occurrences = [m.start() for m in re.finditer(r"scope_status = 'ok'", self.code)]
        self.assertGreaterEqual(len(occurrences), 9)  # totals*4 + by_*5 + top_firms*2 근방
        for pos in occurrences:
            window = self.code[pos:pos + 80]
            self.assertIn(
                "review_status <> 'rejected'", window,
                f"scope_status='ok' at {pos} lacks the rejected filter: {window!r}",
            )

    def test_017_top_firms_preserved(self) -> None:
        # firm_key group by + 대표 표시명 lateral 을 되돌리지 않는다(017 회귀 금지).
        self.assertIn("group by firm_key", self.code)
        self.assertIn("join lateral", self.code)
        self.assertIn("'firm_key', firm_key", self.code)

    def test_by_review_status_key_present(self) -> None:
        # 파셋 축 자체는 유지(rejected 만 빠지고 accepted/needs_review 는 남는다).
        self.assertIn("'by_review_status'", self.code)

    def test_reversible_not_delete(self) -> None:
        self.assertNotIn("delete from public.findings", self.code.lower())
        self.assertIn("숨긴", self.sql)


if __name__ == "__main__":
    unittest.main()
