#!/usr/bin/env python3
"""apply_483_human_verdicts — 사람 판독 결과의 원천 칸 반영(10차 B·D) + 재추출 도구의 사람 확인 행 보호.

이름은 전부 가짜다(공개 저장소). 실측 꼴만 옮겼다 — 글자층 OCR 철자와 이미지 판독 철자가 다른 경우,
한 문서 findings 행 중 일부만 비어 있는 경우(판독 SQL 뒤 새로 붙은 행), 서명란 전체 (b)(6) 가림.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import apply_483_human_verdicts as h  # noqa: E402
import backfill_483_inspectors_from_body as fb  # noqa: E402

AT = "2026-11-26"


def _row(doc: str, raw: dict, rid: str = "") -> dict:
    return {"raw_signal_id": rid or f"rawsig-{doc}", "document_id": doc, "raw_sha256": "x" * 64,
            "raw_json": json.dumps(raw, ensure_ascii=False), "row_json": "{}"}


BODY = "EMPLOYEE(S) SIGNATURE Velmor Ka1pa, Investigator SEE REVERSE OF THIS PAGE"   # 글자층 오독 꼴


class FromFindingsTest(unittest.TestCase):
    def test_single_name_set_is_copied_and_marked_human(self):
        rows = [_row("fda483-100001", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={"fda483-100001": [["Velmor Kalpa", "Quorvala Teskamal"]] * 3 + [[]]},
                    names_input={}, absent_input={}, at=AT)
        self.assertEqual((rep.planned, rep.skipped), (1, 0))
        self.assertEqual(rep.rows[0]["action"], "fill")
        self.assertEqual(rep.rows[0]["names"], 2)

    def test_disagreeing_name_sets_are_skipped(self):
        rows = [_row("fda483-100002", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={"fda483-100002": [["Velmor Kalpa"], ["Velmor Ka1pa"]]},
                    names_input={}, absent_input={}, at=AT)
        self.assertEqual(rep.planned, 0)
        self.assertEqual(rep.rows[0]["reason"], "findings-disagree")

    def test_empty_findings_are_skipped(self):
        rows = [_row("fda483-100003", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={"fda483-100003": [[], []]}, names_input={}, absent_input={}, at=AT)
        self.assertEqual(rep.rows[0]["reason"], "findings-empty")

    def test_apply_writes_raw_and_fills_empty_findings_rows(self):
        rows = [_row("fda483-100004", {"fda483_body_full": BODY, "firm": "Quillmere Pharma"})]
        written = {}

        def _replacer(row, new, **kw):
            written["raw"] = new
            written["kw"] = kw
            return {"status": "applied"}
        patched = []
        rep = h.run(rows, from_findings={"fda483-100004": [["Velmor Kalpa"], []]}, names_input={},
                    absent_input={}, at=AT, apply=True, replacer=_replacer,
                    findings_patcher=lambda rid, names: (patched.append((rid, names)) or (1, "")))
        self.assertEqual(rep.applied, 1)
        self.assertEqual(written["raw"]["fda483_inspectors"], ["Velmor Kalpa"])
        self.assertEqual(written["raw"]["fda483_inspectors_check"], {"method": "human-pdf-image", "at": AT})
        self.assertEqual(written["raw"]["firm"], "Quillmere Pharma")          # 나머지 칸은 그대로
        self.assertEqual(written["kw"]["change_kind"], "repair")
        self.assertEqual(patched, [("rawsig-fda483-100004", ["Velmor Kalpa"])])
        self.assertEqual(rep.findings_rows_patched, 1)


class NamesInputTest(unittest.TestCase):
    def test_names_need_evidence(self):
        rows = [_row("fda483-100005", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={}, names_input={"fda483-100005": {"names": ["Torvald Brennick"]}},
                    absent_input={}, at=AT)
        self.assertEqual(rep.rows[0]["reason"], "no-evidence")
        ok = h.run(rows, from_findings={}, absent_input={}, at=AT,
                   names_input={"fda483-100005": {"names": ["Torvald Brennick"], "evidence": "p1 타자 이름 칸"}})
        self.assertEqual(ok.planned, 1)

    def test_redaction_or_digits_are_not_names(self):
        for bad in ("(b)(6)", "(b) (6), (b) (7)(C)", "Inspector 2", ""):
            self.assertFalse(h.valid_name(bad), bad)
        for good in ("Pelvik (Okonder) Ravel", "RAVINOR K TASINDRA", "Orsolya Venn"):
            self.assertTrue(h.valid_name(good), good)


class AbsentMarkerTest(unittest.TestCase):
    def test_absent_marker_on_empty_row(self):
        written = {}
        rows = [_row("fda483-100006", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={}, names_input={}, absent_input={"fda483-100006": "redacted-b6"},
                    at=AT, apply=True, replacer=lambda row, new, **kw: written.update(raw=new) or {"status": "applied"})
        self.assertEqual(rep.applied, 1)
        self.assertEqual(written["raw"]["fda483_inspectors_status"], "absent-in-source")
        self.assertEqual(written["raw"]["fda483_inspectors_absent_reason"], "redacted-b6")
        self.assertNotIn("fda483_inspectors", written["raw"])

    def test_absent_marker_refused_when_names_exist(self):
        rows = [_row("fda483-100007", {"fda483_body_full": BODY, "fda483_inspectors": ["Orsolya Venn"]})]
        rep = h.run(rows, from_findings={}, names_input={}, absent_input={"fda483-100007": "redacted-b6"}, at=AT)
        self.assertEqual(rep.rows[0]["reason"], "has-names")

    def test_bad_reason_and_conflicting_inputs(self):
        rows = [_row("fda483-100008", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={}, names_input={}, absent_input={"fda483-100008": "no names"}, at=AT)
        self.assertEqual(rep.rows[0]["reason"], "bad-reason")
        both = h.run(rows, from_findings={"fda483-100008": [["Orsolya Venn"]]}, names_input={},
                     absent_input={"fda483-100008": "redacted-b6"}, at=AT)
        self.assertEqual(both.rows[0]["reason"], "conflicting-inputs")

    def test_not_found_and_out_of_scope(self):
        rep = h.run([], from_findings={}, names_input={}, absent_input={"fda483-100009": "redacted-b6"}, at=AT)
        self.assertEqual(rep.rows[0]["reason"], "not-found")
        rows = [_row("fda483-100010", {"fda483_text_status": "out-of-scope"})]
        rep = h.run(rows, from_findings={}, names_input={}, absent_input={"fda483-100010": "redacted-b6"}, at=AT)
        self.assertEqual(rep.rows[0]["reason"], "out-of-scope")


class ReportHasNoNamesTest(unittest.TestCase):
    def test_markdown_and_report_rows_carry_counts_not_names(self):
        rows = [_row("fda483-100011", {"fda483_body_full": BODY})]
        rep = h.run(rows, from_findings={"fda483-100011": [["Velmor Kalpa"]]}, names_input={},
                    absent_input={}, at=AT)
        self.assertNotIn("Kalpa", h.markdown(rep))
        self.assertNotIn("Kalpa", json.dumps(rep.rows))


class FromBodyRespectsHumanCheckTest(unittest.TestCase):
    """재추출 도구는 사람 확인 행을 채우기·감사 대상으로 보지 않는다 — 감사가 이미지 판독 이름을
    '전문에 글자 그대로 없음' 으로 OCR 결과(Ka1pa)로 덮어쓰면 안 된다."""

    def test_human_checked_and_absent_rows_are_not_targets(self):
        checked = _row("fda483-100012", {"fda483_body_full": BODY, "fda483_inspectors": ["Velmor Kalpa"],
                                         "fda483_inspectors_check": {"method": "human-pdf-image", "at": AT}})
        absent = _row("fda483-100013", {"fda483_body_full": BODY, "fda483_inspectors_status": "absent-in-source"})
        plain_empty = _row("fda483-100014", {"fda483_body_full": BODY})
        plain_filled = _row("fda483-100015", {"fda483_body_full": BODY, "fda483_inspectors": ["Velmor Kalpa"]})
        self.assertFalse(fb.is_audit_target(checked))
        self.assertFalse(fb.is_target(absent))
        self.assertTrue(fb.is_target(plain_empty))
        self.assertTrue(fb.is_audit_target(plain_filled))


if __name__ == "__main__":
    unittest.main()
