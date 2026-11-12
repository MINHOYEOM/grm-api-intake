"""483 '구체 내용' "원문 일부" 대상 표 (UI 2단 2026-11-12).

판정은 content_completeness.partial_detail_notice 한 곳 — 표는 그 결과의 지적 id 사본이다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import content_completeness as cc  # noqa: E402
import partial_detail_map as pdm  # noqa: E402
import render  # noqa: E402

CUT = "Review of the preparation re"


class BuildTest(unittest.TestCase):
    def test_only_function_true_rows_survive(self):
        rows = [
            {"source": "FDA 483", "document_id": "fda483-163788", "number": "9", "finding_id": "a", "detail": CUT},
            {"source": "FDA 483", "document_id": "fda483-163788", "number": "9", "finding_id": "b", "detail": CUT + " done."},
            {"source": "FDA 483", "document_id": "fda483-163788", "number": "1", "finding_id": "c", "detail": CUT},
            {"source": "FDA 483", "document_id": "fda483-1", "number": "9", "finding_id": "d", "detail": CUT},
            {"source": "FDA 483", "document_id": "fda483-163788", "number": 14, "finding_id": "e", "detail": CUT},
        ]
        out = pdm.build(rows, "2026-11-12")
        self.assertEqual(out["finding_ids"], ["a", "e"])
        self.assertEqual(out["schema"], "grm-partial-detail/v1")

    def test_committed_table_matches_known_documents(self):
        data = json.loads((ROOT / "web" / "data" / "partial_detail_findings.json").read_text(encoding="utf-8"))
        ids = data["finding_ids"]
        self.assertTrue(ids, "표가 비었다")
        self.assertEqual(ids, sorted(set(ids)))
        n_obs = sum(len(v) for v in cc.KNOWN_PARTIAL_DETAILS["FDA 483"].values())
        self.assertLessEqual(len(ids), n_obs, "판정 목록보다 많은 지적 id")

    def test_loader_degrades_to_empty(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_pd_"))
        self.assertEqual(render.load_partial_detail_ids(tmp / "none.json"), frozenset())
        bad = tmp / "bad.json"
        bad.write_text(json.dumps({"schema": "x", "finding_ids": ["a"]}), encoding="utf-8")
        self.assertEqual(render.load_partial_detail_ids(bad), frozenset())
        good = tmp / "good.json"
        good.write_text(json.dumps({"schema": "grm-partial-detail/v1", "finding_ids": ["a", ""]}), encoding="utf-8")
        self.assertEqual(render.load_partial_detail_ids(good), frozenset({"a"}))

    def test_js_sentence_rule_matches_python(self):
        js = (ROOT / "web" / "assets" / "doc-detail.js").read_text(encoding="utf-8")
        self.assertIn(r"""var SENT_END = /[.!?][)"'”’]?\s*$/;""", js)
        self.assertEqual(cc._SENTENCE_END_RE.pattern, r"[.!?][)\"'”’]?\s*$")


if __name__ == "__main__":
    unittest.main()
