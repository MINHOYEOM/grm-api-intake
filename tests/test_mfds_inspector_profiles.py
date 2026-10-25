"""MFDS UI의 이름순·건수 미표기·멤버십 및 정적 사실 계약."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
import render
import findings_docs_refresh as refresh


def run_ui(script, scenario):
    result = subprocess.run(["node", str(ROOT / "tests/js/mfds_inspector_ui.js"),
        str(ROOT / "web/assets" / script), json.dumps(scenario, ensure_ascii=False)],
        capture_output=True, text=True, encoding="utf-8", timeout=10)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("node"), "Node.js required")
class MFDSClientPolicyTest(unittest.TestCase):
    def test_index_names_only_sorted_even_if_counts_injected(self):
        scenario = {"responses": {"mfds_inspector_index": [
            {"inspector_key": "나상가", "display_name": "나상가", "documents": 999},
            {"inspector_key": "가상가", "display_name": "가상가", "documents": 123}]}}
        out = run_ui("inspector.js", scenario)
        self.assertEqual([a["text"] for a in out["indexLinks"]], ["가상가", "나상가"])
        self.assertNotRegex(out["indexText"], r"\d")
        self.assertTrue(all("source=mfds&key=" in a["href"] for a in out["indexLinks"]))
        self.assertFalse(any("findings_inspector_" in c["url"] for c in out["calls"]))
        self.assertTrue(out["fdaNoticeHidden"])
        self.assertFalse(out["mfdsNoticeHidden"])

    def test_profile_denominators_and_document_membership(self):
        profile = {"display_name": "가상가", "totals": {"documents": 5},
            "by_year": [{"year": "2026", "documents": 1, "named_documents": 2, "total_documents": 89, "table_documents": 23}],
            "manufacturers": [], "by_agency_department": [], "observer_documents": [],
            "documents": [{"document_id": "visible", "manufacturer": "원문제조소", "inspection_start": "2026-01-01"},
                          {"document_id": "no-page", "manufacturer": "다른원문제조소", "inspection_start": "2026-02-01"}]}
        scenario = {"query": "?source=mfds&key=가상가", "responses": {"mfds_inspector_profile": profile},
            "membership": {"schema": "grm-doc-pages/v1", "document_ids": ["visible"], "ko_only_document_ids": ["visible"]}}
        out = run_ui("inspector.js", scenario)
        self.assertFalse(out["profileHidden"])
        self.assertIn("2 / 89", out["years"])
        self.assertIn("23 / 89", out["years"])
        self.assertEqual(out["docLinks"], ["../../findings/doc/visible/"])
        scenario["lang"] = "en"
        self.assertEqual(run_ui("inspector.js", scenario)["docLinks"], [])

    def test_null_profile_and_transport_failure_converge(self):
        scenario = {"query": "?source=mfds&key=비공개", "responses": {"mfds_inspector_profile": None}, "membership": None}
        missing = run_ui("inspector.js", scenario)
        scenario["fail"] = True
        failed = run_ui("inspector.js", scenario)
        self.assertEqual(missing["state"], failed["state"])
        self.assertTrue(missing["profileHidden"] and failed["profileHidden"])

    def test_document_links_use_only_runtime_cohort_and_preserve_facts(self):
        scenario = {"documentNames": ["가상가", "다상가"],
            "responses": {"mfds_inspector_index": [{"inspector_key": "가상가", "display_name": "가상가"}]}}
        out = run_ui("mfds-doc-inspectors.js", scenario)
        self.assertEqual([a["text"] for a in out["documentLinks"]], ["가상가"])
        self.assertIn("다상가", out["documentText"])
        scenario["fail"] = True
        out = run_ui("mfds-doc-inspectors.js", scenario)
        self.assertEqual(out["documentLinks"], [])
        self.assertEqual(out["documentText"], "가상가다상가")


class MFDSStaticFactsTest(unittest.TestCase):
    def test_static_facts_no_cohort_computation_and_fda_untouched(self):
        doc = {"agency": "MFDS", "mfds_attendees": [
            {"name": "가상가", "is_observer": False}, {"name": "가상가", "is_observer": False},
            {"name": "다상가", "is_observer": True}, {"name": "<script>", "is_observer": False}]}
        self.assertEqual(render.doc_mfds_attendees(doc), [
            {"name": "가상가", "is_observer": False}, {"name": "다상가", "is_observer": True}])
        self.assertEqual(render.doc_mfds_attendees(dict(doc, agency="FDA")), [])
        fda = {"document_id": "fda-x", "agency": "FDA", "inspector_names": ["FDA Fixture"]}
        before = json.dumps(fda, sort_keys=True)
        mfds = {"document_id": "mfds-x", "agency": "MFDS"}
        count = refresh.enrich_mfds_attendees("https://example.test", "anon", [fda, mfds],
            post=lambda ids: [{"document_id": "mfds-x", "attendees": [{"name": "다상가", "is_observer": True}]}])
        self.assertEqual(count, 1)
        self.assertEqual(before, json.dumps(fda, sort_keys=True))
        self.assertEqual(mfds["mfds_attendees"], [{"name": "다상가", "is_observer": True}])

    def test_export_failure_never_becomes_an_empty_success(self):
        for response in ({}, [{"document_id": "foreign", "attendees": []}],
                         [{"document_id": "mfds-x", "attendees": [{"name": "가상가", "is_observer": None}]}]):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                refresh.enrich_mfds_attendees("https://example.test", "anon", [{"document_id": "mfds-x", "agency": "MFDS"}], post=lambda ids: response)
