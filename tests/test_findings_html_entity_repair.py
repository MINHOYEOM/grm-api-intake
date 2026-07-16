#!/usr/bin/env python3
"""FIND-1 HTML 엔티티 오염 정정(028_findings_html_entity_repair.sql) tests.

무네트워크·무 Postgres — 013/010/007 관례와 동일하게 마이그레이션 SQL 을 **텍스트 계약**
으로 고정하고, 치환 순서 의미론은 파이썬 시뮬레이션으로 `html.unescape`(수집기 정본)와의
파리티로 고정한다. 실 SQL 실행(라이브 dry-run)은 (B) 블록이 사람 손에서 담당한다.

핵심 계약 두 가지:
  1) `&amp;` 치환이 **맨 마지막** — 먼저 풀면 `&amp;lt;` 가 `<` 로 이중 복원돼 원문 훼손.
  2) update 는 `source = 'FDA 483'` + 엔티티 정규식으로 범위가 좁혀져 있다(023 타임아웃 교훈).
"""

from __future__ import annotations

import html
import re
import unittest
from pathlib import Path

_MIGRATION_PATH = (Path(__file__).resolve().parent.parent / "web" / "migrations"
                   / "028_findings_html_entity_repair.sql")

# 028 (A) grm_html_unescape 의 중첩 replace 순서를 그대로 재현한 시뮬레이터.
_SQL_REPLACEMENTS = (
    ("&#039;", "'"),
    ("&#39;", "'"),
    ("&apos;", "'"),
    ("&quot;", '"'),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&nbsp;", " "),
    ("&amp;", "&"),      # ← 반드시 맨 마지막
)


def _sql_unescape(s: str) -> str:
    for src, dst in _SQL_REPLACEMENTS:
        s = s.replace(src, dst)
    return s


class UnescapeSemanticsTest(unittest.TestCase):
    """치환 순서 = 계약. 파이썬 정본(html.unescape)과 같은 값을 내야 한다."""

    def test_amp_last_prevents_double_unescape(self):
        # 리터럴 `&lt;` 를 표현한 `&amp;lt;` 는 한 겹만 풀려야 한다.
        self.assertEqual(_sql_unescape("&amp;lt;"), "&lt;")
        self.assertEqual(_sql_unescape("&amp;amp;"), "&amp;")

    def test_amp_first_would_corrupt(self):
        # 반례 고정 — 순서가 바뀌면 왜 깨지는지를 테스트가 증언한다.
        def amp_first(s: str) -> str:
            return s.replace("&amp;", "&").replace("&lt;", "<")
        self.assertEqual(amp_first("&amp;lt;"), "<")          # 훼손(이중 복원)
        self.assertNotEqual(amp_first("&amp;lt;"), _sql_unescape("&amp;lt;"))

    def test_parity_with_python_html_unescape(self):
        # 라이브 실측 표기(2026-07-16) + 순서 함정 표본.
        for s in [
            "H &amp; P Industries, Inc.",
            "California Pharmacy &amp; Compounding Center",
            "Dr. Reddy&#039;s Laboratories Ltd.",
            "Nature&#039;s Pharmacy &amp; Compounding Center",
            "Pacific Healthcare, Inc. dba B &amp; B Pharmacy",
            "Vetter Pharma-Fertigung GmbH &amp; Co. KG",
            "A &quot;B&quot; Pharma",
            "plain name with no entity",
        ]:
            self.assertEqual(_sql_unescape(s), html.unescape(s), f"파리티 불일치: {s!r}")

    def test_no_entity_survives(self):
        ent = re.compile(r"&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")
        for s in ["H &amp; P Industries, Inc.", "Dr. Reddy&#039;s Laboratories Ltd."]:
            self.assertIsNone(ent.search(_sql_unescape(s)))


class MigrationTextContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = _MIGRATION_PATH.read_text(encoding="utf-8")

    def _update_statements(self) -> list[str]:
        """update 문 본문만 추출. 문 끝은 '줄 끝의 ;' 로 잡는다 — 엔티티 정규식 리터럴
        안에도 ';' 가 들어 있어(`...{1,31});`) 단순 `.*?;` 는 문 중간에서 끊긴다."""
        return re.findall(r"update public\.\w+.*?;$", self.sql, re.S | re.M)

    def test_migration_exists_and_is_documented(self):
        self.assertTrue(_MIGRATION_PATH.exists())
        self.assertGreaterEqual(self.sql.count("--"), 20)
        # 실측 수치가 근거로 박혀 있어야 한다(추정 금지 규율).
        self.assertIn("439", self.sql)
        self.assertIn("H &amp; P Industries, Inc.", self.sql)

    def test_amp_replacement_is_last(self):
        """SQL 본문에서 `&amp;` 치환이 다른 모든 엔티티 치환보다 뒤에 와야 한다.

        중첩 replace 는 **가장 바깥이 마지막 적용**이므로, 소스 텍스트에서 `&amp;` 가
        가장 늦게(= 가장 바깥에) 등장하는지로 순서를 고정한다.
        """
        body = self.sql.split("create or replace function public.grm_html_unescape")[1]
        body = body.split("$$;")[0]
        amp_at = body.index("'&amp;'")
        for other in ("'&#039;'", "'&#39;'", "'&apos;'", "'&quot;'", "'&lt;'", "'&gt;'", "'&nbsp;'"):
            self.assertLess(body.index(other), amp_at,
                            f"{other} 치환이 '&amp;' 보다 뒤에 있다 — 이중 복원 위험")

    def test_unescape_function_is_immutable_and_search_path_pinned(self):
        self.assertIn("immutable", self.sql)
        self.assertIn("set search_path = public", self.sql)

    def test_updates_are_narrowly_scoped(self):
        """023 타임아웃 교훈 — 무조건 전량 update 금지. 모든 update 가 두 겹으로 좁혀져야."""
        updates = self._update_statements()
        self.assertEqual(len(updates), 5,
                         f"예상 update 5건(findings 2 + raw_signals 3), 실제 {len(updates)}")
        for stmt in updates:
            self.assertIn("source = 'FDA 483'", stmt)
            self.assertIn("~ '&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});'", stmt)

    def test_raw_json_is_never_touched(self):
        """원본 보존층 계약 — raw_json/row_json 은 건드리지 않는다(raw_sha256 정합성)."""
        for stmt in self._update_statements():
            self.assertNotIn("set raw_json", stmt)
            self.assertNotIn("set row_json", stmt)
            self.assertNotIn("raw_sha256", stmt)

    def test_finding_text_is_never_touched(self):
        """findings_rawsig_text_md5_uq (raw_signal_id, md5(finding_text)) 무관함의 근거."""
        for stmt in self._update_statements():
            self.assertNotIn("set finding_text", stmt)

    def test_dry_run_block_present_before_updates(self):
        """(B) dry-run 이 (C) update 보다 먼저 있어야 한다(사람이 눈으로 확인 후 적용)."""
        self.assertLess(self.sql.index("DRY-RUN"), self.sql.index("update public.findings"))
        self.assertIn("double_escaped", self.sql)
        self.assertIn("key_moves", self.sql)


if __name__ == "__main__":
    unittest.main()
