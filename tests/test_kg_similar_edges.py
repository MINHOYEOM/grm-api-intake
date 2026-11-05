#!/usr/bin/env python3
"""유사 지적 엣지(114 · kg_link_new.link_similar · grm-kg-link.yml) tests. 오프라인."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

import kg_link_new

_ROOT = Path(__file__).resolve().parent.parent
_SQL = (_ROOT / "web" / "migrations" / "114_finding_similar.sql").read_text(encoding="utf-8")
_WF = (_ROOT / ".github" / "workflows" / "grm-kg-link.yml").read_text(encoding="utf-8")


def _code(sql: str) -> str:
    return "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class MigrationContract(unittest.TestCase):
    def test_additive_only(self):
        code = _code(_SQL).lower()
        self.assertNotRegex(code, r"drop\s+(table|column|function|index)")
        self.assertEqual(re.findall(r"alter table public\.(\w+) (\w+)", code), [("finding_similar", "enable"), ("finding_similar_done", "enable")])
        self.assertNotIn("create or replace function public.findings_similar", code)
        self.assertNotRegex(code, r"update public\.findings\b")

    def test_tables_locked_down_and_rls(self):
        code = _code(_SQL)
        for t in ("finding_similar", "finding_similar_done"):
            self.assertIn(f"alter table public.{t} enable row level security", code)
            self.assertIn(f"revoke all on table public.{t} from public, anon, authenticated", code)

    def test_every_function_revoked_explicitly(self):
        code = _code(_SQL)
        fns = re.findall(r"create or replace function public\.(\w+)\(([^)]*)\)", code)
        self.assertEqual(len(fns), 4)
        for name, args in fns:
            types = ", ".join(a.strip().split()[1] for a in args.split(",") if a.strip())
            self.assertRegex(code, rf"revoke all on function public\.{name}\({re.escape(types)}\) from public, anon, authenticated")

    def test_every_edge_has_score_and_basis_and_floor(self):
        code = _code(_SQL)
        self.assertIn("score      numeric(5,4) not null check (score >= 0.35", code)
        self.assertIn("basis      text not null", code)
        self.assertIn("p_min_score numeric default 0.40", code)
        self.assertIn("check (finding_id <> similar_id)", code)
        self.assertIn("rank       smallint not null check (rank between 1 and 5)", code)

    def test_same_document_and_self_excluded_and_same_type_only(self):
        code = " ".join(_code(_SQL).split())
        self.assertIn("c.crid is distinct from b.rid and c.cid <> b.fid", code)
        self.assertIn("= bg.ftype", code)
        self.assertIn("c.bftype = b.ftype", code)

    def test_score_formula_matches_findings_similar_to(self):
        code = " ".join(_code(_SQL).split())
        self.assertIn("0.6 * c.sim + 0.4 * c.fr", code)
        self.assertIn("left(btrim(b.txt), 500)", code)

    def test_ftype_scope(self):
        code = " ".join(_code(_SQL).split())
        self.assertIn("'FDA 483', 'Health Canada Inspection', 'FDA Warning Letter'", code)
        self.assertIn("p_document_type = 'recall-quality'", code)
        self.assertNotIn("gmp-inspection'", code.split("grm_similar_ftype", 1)[1].split("revoke", 1)[0])

    def test_next_migration_number(self):
        nums = sorted(int(p.name[:3]) for p in (_ROOT / "web" / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
        self.assertEqual(nums.count(114), 1)


class LinkSimilar(unittest.TestCase):
    def test_loops_until_pending_zero(self):
        seq = [Resp(200, {"processed": 40, "edges": 90, "pending": 30}),
               Resp(200, {"processed": 30, "edges": 50, "pending": 0})]
        with mock.patch("kg_link_new.requests.post", side_effect=seq) as post:
            r = kg_link_new.link_similar("https://x.supabase.co", "k", 7)
        self.assertEqual(r, {"calls": 2, "processed": 70, "edges": 140, "pending": 0})
        self.assertEqual(post.call_count, 2)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["p_limit"], 40)
        self.assertEqual(sent["p_stale_days"], 30)
        self.assertIn("p_since", sent)

    def test_not_applied_returns_none(self):
        with mock.patch("kg_link_new.requests.post", return_value=Resp(404)):
            self.assertIsNone(kg_link_new.link_similar("https://x.supabase.co", "k", 7))

    def test_failure_halves_batch_then_raises(self):
        with mock.patch("kg_link_new.requests.post", return_value=Resp(500)) as post:
            with self.assertRaises(RuntimeError) as cm:
                kg_link_new.link_similar("https://x.supabase.co", "SECRETKEY", 7, limit=40)
        self.assertEqual([c.kwargs["json"]["p_limit"] for c in post.call_args_list], [40, 20, 10, 5])
        self.assertNotIn("SECRETKEY", str(cm.exception))

    def test_stops_when_nothing_processed(self):
        with mock.patch("kg_link_new.requests.post", return_value=Resp(200, {"processed": 0, "edges": 0, "pending": 12})) as post:
            r = kg_link_new.link_similar("https://x.supabase.co", "k", 7)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(r["pending"], 12)


class Workflow(unittest.TestCase):
    def test_only_similar_input_is_wired(self):
        self.assertIn("only_similar:", _WF)
        self.assertIn("--only-similar", _WF)


if __name__ == "__main__":
    unittest.main()
