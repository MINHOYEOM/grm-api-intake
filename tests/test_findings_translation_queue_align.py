#!/usr/bin/env python3
"""FIND-1 번역 큐 정합 마이그레이션 tests — 035_findings_translation_queue_align.sql.

오프라인 소스텍스트 검사만. 일일 번역 루틴이 읽는 findings_translation_queue 가 공개 게이트
(034)와 동일하게 review_status='rejected' 를 제외하는지, 009 의 나머지 계약(scope_status='ok'
필터·정렬·상한)을 보존하는지 고정한다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "web" / "migrations"
_ALIGN_PATH = _MIGRATIONS_DIR / "035_findings_translation_queue_align.sql"


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


class TranslationQueueAlignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_ALIGN_PATH.is_file(), f"missing {_ALIGN_PATH}")
        self.sql = _ALIGN_PATH.read_text(encoding="utf-8")
        self.code = _strip_sql_comments(self.sql)

    def test_no_crlf(self) -> None:
        self.assertNotIn(b"\r\n", _ALIGN_PATH.read_bytes())

    def test_redefines_queue_rpc(self) -> None:
        self.assertIn(
            "create or replace function public.findings_translation_queue(", self.code
        )
        self.assertIn("security definer", self.code)

    def test_excludes_rejected_in_both_count_and_items(self) -> None:
        # count·items 두 where 절 모두에 rejected 제외 필터가 있어야 한다(집계·목록 정합).
        occurrences = [m.start() for m in re.finditer(r"scope_status = 'ok'", self.code)]
        self.assertEqual(len(occurrences), 2, "queue 는 count·items 두 곳에서 scope 를 건다")
        for pos in occurrences:
            window = self.code[pos:pos + 90]
            self.assertIn("review_status <> 'rejected'", window)

    def test_preserves_009_contract(self) -> None:
        # 정렬·상한·핵심 필드 보존(009 계약 불변 — 필터만 추가).
        self.assertIn("order by published_date desc, finding_id asc", self.code)
        self.assertIn("least(coalesce(p_limit, 200), 500)", self.code)
        self.assertIn("'untranslated_total'", self.code)
        self.assertIn("'finding_text'", self.code)


class TranslationQueueUsesPartialIndex1126Test(unittest.TestCase):
    """★11-26 170: 큐가 anon(3초 벽)에서 시간 초과 — `coalesce(finding_text_ko, '') = ''` 가 부분 인덱스
    ops_findings_untranslated_idx 의 조건(`finding_text_ko = ''`)과 글자가 달라 findings 전 행을 두 번 훑었다.
    **최신** 큐 정의의 조건이 인덱스 조건과 같은 글자여야 한다(파일명을 손으로 적지 않고 최신 정의를 찾는다)."""

    _DEF = re.compile(r"create\s+or\s+replace\s+function\s+public\.findings_translation_queue\(", re.I)
    _IDX = re.compile(r"create\s+index[^;]*ops_findings_untranslated_idx[^;]*?\swhere\s+([^;]*);",
                      re.I | re.S)

    def _latest_queue(self) -> str:
        hits = [p for p in sorted(_MIGRATIONS_DIR.glob("*.sql"))
                if self._DEF.search(_strip_sql_comments(p.read_text(encoding="utf-8")))]
        self.assertTrue(hits)
        return _strip_sql_comments(hits[-1].read_text(encoding="utf-8"))

    def _index_predicate(self) -> str:
        for p in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            m = self._IDX.search(_strip_sql_comments(p.read_text(encoding="utf-8")))
            if m:
                return m.group(1)
        self.fail("ops_findings_untranslated_idx 정의를 못 찾았다")

    def test_queue_predicate_matches_partial_index(self):
        code = self._latest_queue()
        self.assertNotIn("coalesce(finding_text_ko", code, "coalesce 를 두르면 부분 인덱스를 못 탄다")
        self.assertIn("finding_text_ko = ''", self._index_predicate().replace("(", "").replace(")", ""))
        self.assertEqual(code.count("where finding_text_ko = '' and scope_status = 'ok'"), 2,
                         "count·items 두 where 절 모두 인덱스 조건 글자여야 한다")


if __name__ == "__main__":
    unittest.main()
