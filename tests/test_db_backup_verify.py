"""db_backup_verify — 덤프 행 수 대조 로직(순수 함수) 회귀.

[2026-09-10] grm-db-backup.yml 의 유일한 검증층이므로, 여기서 "잘린 덤프·빠진 표·0행" 이
전부 빨강이 되는지를 고정한다. 실제 PostgREST 호출(counts)은 네트워크라 여기서 다루지 않는다.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db_backup_verify as dbv  # noqa: E402

SCHEMA = """
CREATE TABLE public.findings (
    id text NOT NULL
);
CREATE TABLE IF NOT EXISTS "public"."raw_signals" (
    "id" text
);
CREATE VIEW public.some_view AS SELECT 1;
CREATE TABLE public.findings (dup text);
"""

DATA = """--
-- Data for Name: findings
--
COPY public.findings (id, text) FROM stdin;
a\tx
b\ty
c\tz
\\.

COPY "public"."raw_signals" ("id") FROM stdin;
r1
\\.

COPY public.empty_tbl (id) FROM stdin;
\\.
"""


class SchemaTableListTest(unittest.TestCase):
    def test_tables_from_schema_dedupes_and_ignores_views(self):
        self.assertEqual(dbv.tables_from_schema(SCHEMA), ["findings", "raw_signals"])


class CopyCountTest(unittest.TestCase):
    def test_counts_rows_per_copy_block_both_quoting_styles(self):
        counts = dbv.copy_row_counts(io.StringIO(DATA))
        self.assertEqual(counts, {"findings": 3, "raw_signals": 1, "empty_tbl": 0})

    def test_truncated_dump_is_marked(self):
        truncated = DATA.rsplit("\\.", 1)[0]  # 마지막 종료 마커 제거
        counts = dbv.copy_row_counts(io.StringIO(truncated))
        self.assertEqual(counts.get("__truncated__"), 1)


class CompareTest(unittest.TestCase):
    def _cmp(self, live, dumped, **kw):
        kw.setdefault("min_ratio", 0.99)
        kw.setdefault("require", ["findings", "raw_signals"])
        kw.setdefault("min_tables", 2)
        return dbv.compare(dict(live), dict(dumped), **kw)

    def test_pass(self):
        ok, rows, problems = self._cmp({"findings": 3, "raw_signals": 1, "empty_tbl": 0},
                                       {"findings": 3, "raw_signals": 1, "empty_tbl": 0})
        self.assertTrue(ok, problems)
        self.assertEqual([r["table"] for r in rows], ["empty_tbl", "findings", "raw_signals"])

    def test_missing_table_fails(self):
        ok, _, problems = self._cmp({"findings": 3, "raw_signals": 1}, {"findings": 3})
        self.assertFalse(ok)
        self.assertTrue(any("raw_signals" in p and "COPY 블록 없음" in p for p in problems), problems)

    def test_ratio_below_threshold_fails_but_small_drift_passes(self):
        ok, _, problems = self._cmp({"findings": 1000, "raw_signals": 1}, {"findings": 989, "raw_signals": 1})
        self.assertFalse(ok, problems)
        ok2, _, problems2 = self._cmp({"findings": 1000, "raw_signals": 1}, {"findings": 995, "raw_signals": 1})
        self.assertTrue(ok2, problems2)

    def test_zero_rows_for_required_table_fails(self):
        ok, _, problems = self._cmp({"findings": 0, "raw_signals": 1}, {"findings": 0, "raw_signals": 1})
        self.assertFalse(ok)
        self.assertTrue(any("findings" in p and "필수" in p for p in problems), problems)

    def test_truncated_marker_fails(self):
        ok, _, problems = self._cmp({"findings": 3, "raw_signals": 1},
                                    {"findings": 3, "raw_signals": 1, "__truncated__": 1})
        self.assertFalse(ok)
        self.assertTrue(any("절단" in p for p in problems), problems)

    def test_too_few_tables_fails(self):
        ok, _, problems = self._cmp({"findings": 3, "raw_signals": 1}, {"findings": 3, "raw_signals": 1},
                                    min_tables=20)
        self.assertFalse(ok)
        self.assertTrue(any("최소 20" in p for p in problems), problems)


class CliTest(unittest.TestCase):
    def test_verify_cli_exit_codes_and_report(self):
        with tempfile.TemporaryDirectory() as d:
            dump = os.path.join(d, "data.sql")
            counts = os.path.join(d, "counts.json")
            report = os.path.join(d, "report.md")
            with open(dump, "w", encoding="utf-8") as fh:
                fh.write(DATA)
            with open(counts, "w", encoding="utf-8") as fh:
                json.dump({"findings": 3, "raw_signals": 1, "empty_tbl": 0}, fh)
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            base = [sys.executable, os.path.join(os.path.dirname(dbv.__file__), "db_backup_verify.py"),
                    "verify", "--dump", dump, "--counts", counts, "--report", report, "--min-tables", "2"]
            rc = subprocess.run(base, env=env, capture_output=True).returncode
            self.assertEqual(rc, 0)
            with open(report, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("결과: 통과", text)
            self.assertIn("| findings | 3 | 3 | ok |", text)
            # 라이브가 더 많으면(덤프 절단) 1
            with open(counts, "w", encoding="utf-8") as fh:
                json.dump({"findings": 300, "raw_signals": 1, "empty_tbl": 0}, fh)
            rc = subprocess.run(base, env=env, capture_output=True).returncode
            self.assertEqual(rc, 1)

    def test_counts_cli_requires_env(self):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        env.pop("SUPABASE_URL", None)
        env.pop("SUPABASE_SERVICE_ROLE_KEY", None)
        rc = subprocess.run([sys.executable, os.path.join(os.path.dirname(dbv.__file__), "db_backup_verify.py"),
                             "counts", "--schema", "x.sql", "--out", "y.json"],
                            env=env, capture_output=True).returncode
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
