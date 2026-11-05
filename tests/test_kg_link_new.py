#!/usr/bin/env python3
"""일일 지식그래프 연결(108 · kg_link_new.py · kg_link_health.py · grm-kg-link.yml) tests. 오프라인."""

from __future__ import annotations

import io
import json
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import findings_store
import kg_edges
import kg_link_health
import kg_link_new

_ROOT = Path(__file__).resolve().parent.parent
_MIG = _ROOT / "web" / "migrations"
_S101 = (_MIG / "101_findings_fei.sql").read_text(encoding="utf-8")
_S106 = (_MIG / "106_finding_clause_edges.sql").read_text(encoding="utf-8")
_S108 = (_MIG / "108_kg_link_new_findings.sql").read_text(encoding="utf-8")
_WF = (_ROOT / ".github" / "workflows" / "grm-kg-link.yml").read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _stmt(sql: str, start: str, end: str) -> str:
    i = _code(sql).index(start)
    j = _code(sql).index(end, i)
    return _norm(_code(sql)[i:j])


class SameCodeAsBackfill(unittest.TestCase):
    """백필(101·106)과 일일 연결(108 *_since)이 같은 SQL 이라는 것을 고정한다."""

    def test_fei_core_identical_except_window(self):
        old = _stmt(_S101, "update public.findings f", "get diagnostics")
        new = _stmt(_S108, "update public.findings f", "get diagnostics")
        self.assertEqual(new.replace(" and f.ingested_at >= p_since", ""), old)
        self.assertIn("f.ingested_at >= p_since", new)

    def test_clause_core_identical_except_window(self):
        old = _stmt(_S106, "insert into public.finding_clause", "get diagnostics")
        new = _stmt(_S108, "insert into public.finding_clause", "get diagnostics")
        self.assertEqual(new.replace(" where f.ingested_at >= p_since", ""), old)
        self.assertIn("where f.ingested_at >= p_since", new)

    def test_zero_arg_functions_are_thin_shells(self):
        code = _norm(_code(_S108))
        self.assertIn("grm_backfill_findings_fei() returns integer language sql", code)
        self.assertIn("select public.grm_backfill_findings_fei_since('-infinity'::timestamptz)", code)
        self.assertIn("select public.grm_backfill_finding_clause_since('-infinity'::timestamptz)", code)

    def test_signatures_unchanged(self):
        for sql, name in ((_S101, "grm_backfill_findings_fei()"), (_S106, "grm_backfill_finding_clause()")):
            self.assertIn(f"function public.{name}", _code(sql))
            self.assertIn(f"function public.{name}", _code(_S108))


class MigrationContract(unittest.TestCase):
    def test_additive_only_no_writes(self):
        code = _code(_S108).lower()
        self.assertNotRegex(code, r"drop\s+(table|column|function)|alter table")
        self.assertNotRegex(code, r"\binsert into public\.(findings|raw_signals)\b")
        self.assertNotIn("delete from", code)

    def test_every_function_locked_down(self):
        code = _code(_S108)
        fns = re.findall(r"create or replace function public\.(\w+)\(([^)]*)\)", code)
        self.assertEqual(len(fns), 6)
        for name, args in fns:
            types = ", ".join(a.strip().split()[1] for a in args.split(",") if a.strip())
            self.assertRegex(code, rf"revoke all on function public\.{name}\({re.escape(types)}\) from public, anon, authenticated")

    def test_health_has_no_hardcoded_thresholds(self):
        body = _code(_S108).split("create or replace function public.kg_link_health", 1)[1]
        self.assertNotRegex(body, r"0\.99|99\s*%")
        self.assertIn("p_grace_hours", body)  # 방금 들어온 행을 미연결로 세지 않는다

    def test_ingest_path_not_touched(self):
        # 적재 payload 에 fei 를 넣으면 101 미적용 DB 에서 모든 INSERT 가 거부된다(적재 장애).
        self.assertNotIn("fei", findings_store.FINDING_SQLITE_COLUMNS)
        self.assertNotIn("fei", (_ROOT / "findings_supabase_append.py").read_text(encoding="utf-8"))


class HealthSummary(unittest.TestCase):
    OK = {"window_days": 7, "grace_hours": 36,
          "fei": {"fillable": 1200, "filled": 1198},
          "clause": [{"source": "FDA 483", "expected": 100, "linked": 100},
                     {"source": "MFDS", "expected": 200, "linked": 199}],
          "term": [{"source": "FDA 483", "texts": 1000, "linked": 470}]}

    def test_ok_line(self):
        line, breaches = kg_link_health.summarize(self.OK)
        self.assertEqual(breaches, [])
        self.assertTrue(line.startswith("KG-LINK OK 7d | fei(483) 99.8% (1,198/1,200) | clause 99.7% (299/300) | term 47.0%"), line)

    def test_fei_breach(self):
        p = {**self.OK, "fei": {"fillable": 1200, "filled": 1152}}
        line, breaches = kg_link_health.summarize(p)
        self.assertTrue(line.startswith("KG-LINK WARN"))
        self.assertIn("<99%", line)
        self.assertEqual(len(breaches), 1)

    def test_term_floor_is_not_99(self):
        # 정상 상태(약 48%)는 위반이 아니고, 25% 미만만 위반이다.
        self.assertEqual(kg_link_health.summarize(self.OK)[1], [])
        low = {**self.OK, "term": [{"source": "FDA 483", "texts": 1000, "linked": 100}]}
        self.assertEqual(len(kg_link_health.summarize(low)[1]), 1)

    def test_small_sample_is_skipped_not_failed(self):
        p = {**self.OK, "fei": {"fillable": 5, "filled": 0}}
        line, breaches = kg_link_health.summarize(p)
        self.assertEqual(breaches, [])
        self.assertIn("SKIP(표본 5)", line)

    def test_exit_code(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump({**self.OK, "fei": {"fillable": 1000, "filled": 900}}, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = kg_link_health.main(["--from-json", fh.name])
        self.assertEqual(rc, 1)
        self.assertIn("KG-LINK WARN", buf.getvalue())


class Runner(unittest.TestCase):
    def test_dry_run_touches_no_network(self):
        with mock.patch("kg_link_new.requests") as req:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(kg_link_new.main(["--dry-run"]), 0)
            req.get.assert_not_called()
            req.post.assert_not_called()

    def test_missing_credentials_exit_2(self):
        with mock.patch.dict("os.environ", {}, clear=True), redirect_stderr(io.StringIO()):
            self.assertEqual(kg_link_new.main([]), 2)

    def test_unapplied_rpc_and_table_are_skipped(self):
        resp = mock.Mock(status_code=404)
        with mock.patch("kg_link_new.requests.post", return_value=resp):
            self.assertIsNone(kg_link_new.link_db("https://x.supabase.co", "k", 3))
            self.assertIsNone(kg_link_new.write_term_edges(
                "https://x.supabase.co", "k", [{"finding_id": "f", "term_id": "sop", "surface": "SOP", "confidence": 1.0}]))

    def test_term_write_is_idempotent_upsert(self):
        resp = mock.Mock(status_code=201)
        with mock.patch("kg_link_new.requests.post", return_value=resp) as post:
            n = kg_link_new.write_term_edges(
                "https://x.supabase.co", "k", [{"finding_id": "f", "term_id": "sop", "surface": "SOP", "confidence": 1.0}])
        self.assertEqual(n, 1)
        kw = post.call_args.kwargs
        self.assertIn("ignore-duplicates", kw["headers"]["Prefer"])
        self.assertEqual(kw["params"], {"on_conflict": "finding_id,term_id"})

    def test_only_product_skips_rpc_and_terms(self):
        env = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}
        with mock.patch.dict("os.environ", env, clear=True),                 mock.patch("kg_link_new.link_similar", return_value=None), mock.patch("kg_link_new.link_db") as db,                 mock.patch("kg_link_new.link_text_clauses") as lt,                 mock.patch("kg_link_new.fetch_recent_texts") as ft,                 mock.patch("kg_link_new.kg_product.link_recent", return_value=(3, 3)) as lr:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(kg_link_new.main(["--only-product", "--days", "5000"]), 0)
        db.assert_not_called()
        lt.assert_not_called()
        ft.assert_not_called()
        lr.assert_called_once()
        self.assertIn("제품군(only)", buf.getvalue())

    def test_default_path_still_runs_all_steps(self):
        env = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}
        with mock.patch.dict("os.environ", env, clear=True),                 mock.patch("kg_link_new.link_similar", return_value=None), mock.patch("kg_link_new.link_db", return_value={}) as db,                 mock.patch("kg_link_new.link_text_clauses", return_value=0) as lt,                 mock.patch("kg_link_new.fetch_recent_texts", return_value=[]) as ft,                 mock.patch("kg_link_new.write_term_edges", return_value=0),                 mock.patch("kg_link_new.kg_product.link_recent", return_value=(0, 0)) as lr:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(kg_link_new.main([]), 0)
        db.assert_called_once()
        lt.assert_called_once()
        ft.assert_called_once()
        lr.assert_called_once()

    def test_workflow_exposes_only_product_input(self):
        wf = (_ROOT / ".github/workflows/grm-kg-link.yml").read_text(encoding="utf-8")
        self.assertIn("only_product:", wf)
        self.assertIn("--only-product", wf)

    def test_skip_rpc_runs_terms_and_product_only(self):
        env = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}
        with mock.patch.dict("os.environ", env, clear=True),                 mock.patch("kg_link_new.link_similar", return_value=None), mock.patch("kg_link_new.link_db") as db,                 mock.patch("kg_link_new.link_text_clauses") as lt,                 mock.patch("kg_link_new.fetch_recent_texts", return_value=[]) as ft,                 mock.patch("kg_link_new.write_term_edges", return_value=0),                 mock.patch("kg_link_new.kg_product.link_recent", return_value=(0, 0)) as lr:
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(kg_link_new.main(["--skip-rpc", "--days", "5000"]), 0)
        db.assert_not_called()
        lt.assert_not_called()
        ft.assert_called_once()
        lr.assert_called_once()
        self.assertIn("SKIP(--skip-rpc)", buf.getvalue())

    def test_workflow_exposes_skip_rpc_input(self):
        wf = (_ROOT / ".github/workflows/grm-kg-link.yml").read_text(encoding="utf-8")
        self.assertIn("skip_rpc:", wf)
        self.assertIn("--skip-rpc", wf)

    def test_same_engine_as_backfill(self):
        import json as _j
        terms = _j.loads((_ROOT / "web/data/glossary.json").read_text(encoding="utf-8"))
        rows = [{"finding_id": "f1", "text_ko": "표준작업지침서(SOP)의 관리가 미흡했다. 밸리데이션 계획이 없다."}]
        a, _ = kg_link_new.compute_edges(rows, terms)
        b, _ = kg_edges.build_term_edges([{"findings": rows}], terms)
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_error_message_never_contains_key(self):
        import requests
        with mock.patch("kg_link_new.requests.post", side_effect=requests.ConnectionError("boom SECRETKEY")):
            with self.assertRaises(RuntimeError) as cm:
                kg_link_new.link_db("https://x.supabase.co", "SECRETKEY", 3)
        self.assertNotIn("SECRETKEY", str(cm.exception))


class WorkflowAndDocs(unittest.TestCase):
    def test_workflow_shape(self):
        self.assertIn("cron:", _WF)
        self.assertIn("python kg_link_new.py", _WF)
        self.assertIn("python kg_link_health.py", _WF)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}", _WF)
        self.assertRegex(_WF, r"permissions:\s*\n\s*contents: read")
        self.assertNotRegex(_WF, r"git (push|commit)")
        self.assertNotIn("continue-on-error", _WF)

    def test_dispatch_days_input_defaults_to_7_and_is_sanitized(self):
        self.assertRegex(_WF, r'days:\s*\n\s*description:.*\n\s*required: false\n\s*default: "7"')
        self.assertIn('DAYS="${{ inputs.days }}"', _WF)
        self.assertIn("*[!0-9]*) DAYS=7", _WF)       # schedule/비정수 -> 7
        self.assertIn('ARGS="--days $DAYS"', _WF)
        self.assertIn('default: "true"', _WF)         # dry_run 기본 true 유지

    def test_checklist_covers_required_keys(self):
        doc = (_ROOT / "docs" / "kg_new_source_checklist.md").read_text(encoding="utf-8")
        for key in ("firm_key", "fei", "country_key", "category_code", "cfr_refs", "mfds_refs",
                    "finding_clause", "finding_term", "NODE_TYPES"):
            self.assertIn(key, doc, key)


if __name__ == "__main__":
    unittest.main()
