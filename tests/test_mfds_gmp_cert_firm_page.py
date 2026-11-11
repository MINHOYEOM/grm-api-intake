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


if __name__ == "__main__":
    unittest.main()
