#!/usr/bin/env python3
"""업체 페이지 "식약처 GMP 적합판정 이력" — 스냅샷 쓰기(findings_docs_refresh) ↔ 읽기(render) 계약."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import findings_docs_refresh as fdr  # noqa: E402
import render  # noqa: E402

ROWS = [
    {"firm_key": "(주)예시제약", "firm_name": "(주)예시제약", "factory_addr": "충청북도  청주시 ",
     "product_kind": "완제의약품", "dosage_group": "", "valid_until": "2027-01-01",
     "listed_now": False, "measured_on": "2026-11-11"},
    {"firm_key": "(주)예시제약", "firm_name": "(주)예시제약", "factory_addr": "경기도 화성시",
     "product_kind": "원료의약품", "dosage_group": "무균제제", "valid_until": "2028-05-24",
     "listed_now": True, "measured_on": "2026-11-11"},
    {"firm_key": "akorn ag", "firm_name": "Akorn AG", "factory_addr": "", "product_kind": "완제의약품",
     "dosage_group": "", "valid_until": "2029-01-01", "listed_now": True, "measured_on": "2026-11-11"},
]


class GmpCertSnapshotTests(unittest.TestCase):
    def _write(self, rows):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "mfds_gmp_cert.json"
        n = fdr.write_gmp_certs(p, fdr.fetch_gmp_certs("u", "k", post=lambda: rows))
        return p, n

    def test_round_trip_and_ordering(self):
        p, n = self._write(ROWS)
        self.assertEqual(n, 2)
        snap = render.load_mfds_gmp_certs(p)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["measured_on"], "2026-11-11")
        idx = render.firm_gmp_cert_index(snap, render._canonical_firm_key)
        rows = idx["(주)예시제약"]
        self.assertEqual([r["valid_until"] for r in rows], ["2028-05-24", "2027-01-01"])  # 현재 목록 먼저
        self.assertEqual(rows[1]["factory_addr"], "충청북도 청주시")
        self.assertNotIn("bizrno", json.dumps(snap, ensure_ascii=False))
        # 별칭 키는 정본으로 접힌다(grm_findings.FIRM_KEY_ALIASES = firm_alias 시드)
        self.assertIn(render._canonical_firm_key("akorn ag"), idx)

    def test_empty_or_bad_rpc_keeps_last_file(self):
        with self.assertRaises(RuntimeError):
            fdr.write_gmp_certs(Path(tempfile.gettempdir()) / "never.json", [])
        with self.assertRaises(RuntimeError):
            fdr.fetch_gmp_certs("u", "k", post=lambda: [{"firm_key": ""}])

    def test_bad_snapshot_disables_display(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            p.write_text(json.dumps({"schema_version": "other", "measured_on": "2026-11-11",
                                     "by_firm": {}}), encoding="utf-8")
            self.assertIsNone(render.load_mfds_gmp_certs(p))
        self.assertEqual(render.firm_gmp_cert_index(None, render._canonical_firm_key), {})
        bad = {"by_firm": {"a": [{"product_kind": "", "valid_until": "2028-01-01"},
                                 {"product_kind": "완제", "valid_until": "모름"}]}}
        self.assertEqual(render.firm_gmp_cert_index(bad, render._canonical_firm_key), {})

    def test_template_renders_line_only_when_rows(self):
        tpl = (ROOT / "web" / "templates" / "findings_firm_page.html").read_text(encoding="utf-8")
        self.assertIn("{%- if gmp_certs %}", tpl)
        self.assertIn("식약처 GMP 적합판정 이력", tpl)

    def test_committed_snapshot_is_valid(self):
        if not render.MFDS_GMP_CERT_FILE.is_file():
            self.skipTest("스냅샷 없음")
        snap = render.load_mfds_gmp_certs(render.MFDS_GMP_CERT_FILE)
        self.assertIsNotNone(snap)
        self.assertTrue(render.firm_gmp_cert_index(snap, render._canonical_firm_key))


NEW_FIRMS = [
    {"node_key": "mfds-gmp-00aa11bb22cc", "firm_name": "예시가스(주)", "cert_count": 1,
     "product_kinds": "완제의약품", "listed_now": True, "measured_on": "2026-11-11"},
    {"node_key": "mfds-gmp-ffee00112233", "firm_name": "예시바이오", "cert_count": 2,
     "product_kinds": "완제의약품,원료의약품", "listed_now": True, "measured_on": "2026-11-11"},
]


class GmpNewFirmNodeTests(unittest.TestCase):
    """대표 결정 11-11 "지적 생기면 페이지": 새 업체는 지식 그래프 노드만, 업체 페이지는 없다."""

    def _snap(self, new_firms):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "mfds_gmp_cert.json"
        fdr.write_gmp_certs(p, ROWS, fdr.fetch_gmp_new_firms("u", "k", post=lambda: new_firms))
        return p, render.load_mfds_gmp_certs(p)

    def test_round_trip_keeps_by_firm_and_adds_new_firms(self):
        p, snap = self._snap(NEW_FIRMS)
        self.assertIsNotNone(snap)
        self.assertEqual([r["node_key"] for r in snap["new_firms"]],
                         ["mfds-gmp-00aa11bb22cc", "mfds-gmp-ffee00112233"])
        self.assertNotIn("measured_on", snap["new_firms"][0])
        self.assertIn("(주)예시제약", render.firm_gmp_cert_index(snap, render._canonical_firm_key))
        self.assertNotIn("bizrno", p.read_text(encoding="utf-8"))

    def test_bad_new_firm_rows_rejected(self):
        for bad in ([{"node_key": "1234567890", "firm_name": "x"}],
                    [{"node_key": "mfds-gmp-1", "firm_name": ""}], {"x": 1}):
            with self.assertRaises(RuntimeError):
                fdr.fetch_gmp_new_firms("u", "k", post=lambda b=bad: b)

    def test_graph_gets_firm_nodes_without_page(self):
        import graph_export as ge
        from grm_findings import canonical_firm_key
        g = ge.build_graph(
            documents=[], brief_slugs=[], en_brief_slugs=set(), card_issue_index={},
            en_doc_slugs=set(), glossary_terms=[], term_ids_with_pages=[],
            doc_term_ids=lambda d: [], firm_page_slug_by_key={},
            canonical_firm_key=canonical_firm_key, cfr_section_of=lambda x: "",
            clause_slugs=set(), clause_slug_of=lambda s: s,
            gmp_new_firms=NEW_FIRMS + [{"node_key": "bogus", "firm_name": "x"}])
        self.assertEqual(ge.validate_graph(g), [])
        firms = g["nodes-firm"]
        self.assertEqual([f["id"] for f in firms],
                         ["firm:mfds-gmp-00aa11bb22cc", "firm:mfds-gmp-ffee00112233"])
        for f in firms:
            self.assertNotIn("url", f)          # 업체 페이지 없음
            self.assertEqual(f["source"], "mfds_gmp_cert")
            self.assertEqual(f["agency"], "MFDS")

    def test_committed_snapshot_new_firms_are_nodes_only(self):
        snap = render.load_mfds_gmp_certs(render.MFDS_GMP_CERT_FILE)
        if not snap or not snap.get("new_firms"):
            self.skipTest("새 업체 목록 없음")
        keys = [r["node_key"] for r in snap["new_firms"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(k.startswith("mfds-gmp-") for k in keys))
        # 연결된 업체 키와 겹치지 않는다(겹치면 페이지 있는 업체가 노드로 중복된다)
        self.assertFalse(set(keys) & set(snap["by_firm"]))


class NewFirmMigrationContractTests(unittest.TestCase):
    SQL = (ROOT / "web" / "migrations" / "144_mfds_gmp_cert_new_firm_nodes.sql").read_text(encoding="utf-8")

    def test_public_rpc_hides_bizrno_and_excludes_candidates(self):
        sig = self.SQL.split("returns table (")[1].split(")")[0]
        self.assertNotIn("bizrno", sig)
        self.assertIn("c.link_note = 'no_firm_node' and c.candidate_firm_key is null", self.SQL)
        self.assertIn("grant execute on function public.mfds_gmp_cert_new_firms_public() to anon", self.SQL)

    def test_daily_kg_link_relinks_gmp_cert(self):
        src = (ROOT / "kg_link_new.py").read_text(encoding="utf-8")
        self.assertIn("rpc/grm_link_mfds_gmp_cert", src)


if __name__ == "__main__":
    unittest.main()
