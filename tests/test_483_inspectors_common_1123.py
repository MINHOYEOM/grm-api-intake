"""[2026-11-23] 483 실사관 추출 공통 함수 — 수용기준 고정.

배경: GRM 수집완전성 점검 11-22 9-3 '483 실사관 176'. 서명란이 있는데 못 뽑은 130행의 원인은
R3(OCR 복구가 실사관을 안 넘김) · R5(전문 백필이 실사관을 안 뽑음) · R4(파서가 서명란 형식을 못 읽음).

수용기준(대표 결정 11-22):
  ① 지어낸 이름 0 — 뽑은 이름은 전부 그 문서 전문 안에 **글자 그대로** 있다.
  ② `(b)(6)` 가림은 이름이 되지 않는다.
  ③ 일일 수집·OCR 복구·전문 백필·재추출 도구가 **같은 함수**를 부른다(규칙 베끼기 금지).
아래 서명란 문안은 저장 전문 163행(11-23 조회)에서 실측한 꼴을 줄여 옮긴 것이다.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backfill_483_body_full as bb  # noqa: E402
import backfill_483_inspectors_from_body as tool  # noqa: E402
import backfill_483_ocr_recovery as rec  # noqa: E402
import collect_fda_483 as f  # noqa: E402
import raw_versioning as rv  # noqa: E402

SIG = "EMPLOYEE(S) SIGNATURE\nEMPLOYEE(S) NAME AND TITLE (Print or Type)\nDATE ISSUED\nSEE\nREVERSE\n"

# (서명란 문안, 기대 이름) — 실측 형식별 1건씩.
FORMATS: list[tuple[str, list[str]]] = [
    (SIG + "Margaret M Annes, cso\n8/28/2026\nOF THIS PAGE\nMegan T Ziegler, I nvestigat or\n",
     ["Margaret M Annes", "Megan T Ziegler"]),
    (SIG + "Xiuju Lu, Lead CSO, Lead Inspector\n06/06/2025\n", ["Xiuju Lu"]),
    (SIG + "Nicole Li, Lead Consumer Safety Officer\nJin Sung Hong, Lead Biologist\n05/14/2025\n",
     ["Nicole Li", "Jin Sung Hong"]),
    (SIG + "Pratik S. Upadhyay, DDC Investigator\n05/15/2026\n", ["Pratik S. Upadhyay"]),
    (SIG + "Steven A. Brettler, GDUFA- Investigator\n09/13/2024\n", ["Steven A. Brettler"]),
    (SIG + "Rowena S. Nguyen - Investigator\n04/10/2026\n", ["Rowena S. Nguyen"]),
    (SIG + "Paula A. Trost/Investigator\nRobert W. Fisher/Staff Fellow\n06/21/2012\n",
     ["Paula A. Trost", "Robert W. Fisher"]),
    (SIG + "Francis Guidry /Investigator\n12/16/2016\nXiaokuang Lai / Microbiologist\n",
     ["Francis Guidry", "Xiaokuang Lai"]),
    (SIG + "Prabhu P Raju. Investigator\nOF THIS\nYonggang Wang. Ph.D. Biologist\n331 219\n",
     ["Prabhu P Raju", "Yonggang Wang"]),
    (SIG + "Liqun Zhao, Ph.D., Pharmaceutical Scientist\n12/16/2025\n", ["Liqun Zhao"]),
    (SIG + "Hung H. Do, M.Sc.. Investigator\n06/17/2026\n", ["Hung H. Do"]),
    (SIG + "Madushini Dharmasena, Ph.D.,\nSEE\n", ["Madushini Dharmasena"]),
    ("EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE | Robert J.\nHam,\nInvestigator\n3/27/2023\n",
     ["Robert J. Ham"]),
    ("EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE\n|\nMilton J De Jesus\nTavarez,\nInvestigator\n",
     ["Milton J De Jesus Tavarez"]),
    ("EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE | Jackson Y See,\nInvestigator\n3/6/2026\n",
     ["Jackson Y See"]),
]


class NoFabricatedNamesAcceptanceTest(unittest.TestCase):
    """수용기준 ① — 지어낸 이름 0. 실패하면 이 PR 은 머지되면 안 된다."""

    def test_every_extracted_name_is_verbatim_in_its_text(self):
        for text, _ in FORMATS + [(t, []) for t in _NEGATIVE_TEXTS]:
            for name in f._extract_483_inspectors(text):
                self.assertTrue(f._inspector_name_in_text(name, text), (name, text[:80]))

    def test_name_not_in_text_is_never_emitted(self):
        """다른 경로가 전문에 없는 이름을 만들어도 마지막 관문에서 버려진다(관문 자체를 고정)."""
        text = SIG + "Jose F Velez, Investigator 3/5/2026\n"
        self.assertTrue(f._inspector_name_in_text("Jose F Velez", text))
        self.assertFalse(f._inspector_name_in_text("Jose F. Velez", text))      # 마침표 하나도 다르면 아님
        self.assertFalse(f._inspector_name_in_text("Jose Velez", text))         # 빠진 토큰
        self.assertFalse(f._inspector_name_in_text("Shafig Ahadi", "Shafigq Ahadi, Investigator"))
        self.assertTrue(f._inspector_name_in_text("Robert J. Ham", "| Robert J.\nHam,\nInvestigator"))

    def test_guard_runs_inside_the_common_function(self):
        """`_inspector_name_in_text` 를 거짓으로 바꾸면 이름이 하나도 안 나온다 = 관문이 실제로 걸려 있다."""
        text = FORMATS[0][0]
        orig = f._inspector_name_in_text
        try:
            f._inspector_name_in_text = lambda _n, _t: False
            self.assertEqual(f._extract_483_inspectors(text), [])
        finally:
            f._inspector_name_in_text = orig


class FormatsTest(unittest.TestCase):
    def test_measured_signature_formats(self):
        for text, want in FORMATS:
            self.assertEqual(f._extract_483_inspectors(text), want, text[:120])


_NEGATIVE_TEXTS = [
    # 가림 — 이름 자리가 (b)(6)
    SIG + "(b)(6), Investigator\n3/5/2026\n(b) (6), CSO\n",
    SIG + "<Redacted B6>, Investigator\n3/5/2026\n",
    # 직함 낱말이 이름 자리에 — 종전에 'Team Lead' 가 저장돼 있었다(fda483-195531)
    SIG + "Thomas Finn, Team Lead, Microbiologist;\n8/2/2019\n",
    SIG + "Preapproval Manager/ Investigator\n",
    # 관찰 산문(직함이 앞) — 서명란이 아니다
    "OBSERVATION 1 Investigator Smith noted that the batch record was incomplete.",
]


class NegativeTest(unittest.TestCase):
    def test_redaction_is_not_turned_into_a_name(self):
        names, why = f._extract_483_inspectors_detail(_NEGATIVE_TEXTS[0])
        self.assertEqual(names, [])
        self.assertEqual(why, f.INSPECTORS_REDACTED)
        self.assertEqual(f._extract_483_inspectors(_NEGATIVE_TEXTS[1]), [])

    def test_title_words_are_not_names(self):
        self.assertEqual(f._extract_483_inspectors(_NEGATIVE_TEXTS[2]), ["Thomas Finn"])
        self.assertEqual(f._extract_483_inspectors(_NEGATIVE_TEXTS[3]), [])
        self.assertEqual(f._extract_483_inspectors(_NEGATIVE_TEXTS[4]), [])

    def test_ocr_noise_prefix_is_dropped_when_clean_reading_dominates(self):
        """`Qvrtee Margaret M. Annes`(1번) vs `Margaret M. Annes`(3번) — 깨끗한 판독이 남는다(196309)."""
        text = "".join(SIG + f"{p}Margaret M. Annes, CSO 11/17/2023\n"
                        for p in ("| ", "(ty ", "Qvrtee ", "eonsea. | "))
        self.assertEqual(f._extract_483_inspectors(text), ["Margaret M. Annes"])

    def test_glued_piv_token_is_not_part_of_name(self):
        text = SIG + "Petes Feonen-S Andrea Franco, Pharmaceutical Scientist\n3/5/2026\n"
        self.assertEqual(f._extract_483_inspectors(text), ["Andrea Franco"])

    def test_two_spellings_of_one_person_drop_the_document(self):
        """같은 서명을 두 철자로 읽은 문서 — 어느 쪽인지 모르므로 고르지 않는다(사유만 남김)."""
        text = (SIG + "SEE REVERSE| Shafig Ahadi,\nInvestigator\n11/25/2025\n"
                + SIG + "SEE REVERSE| Shafiq Ahadi,\nInvestigator\n11/25/2025\n")
        self.assertEqual(f._extract_483_inspectors_detail(text), ([], f.INSPECTORS_OCR_CONFLICT))

    def test_same_person_variants_are_merged_not_conflicted(self):
        text = (SIG + "Melissa Ray, PhD, Microbiologist\nThuy Thanh Nguyen, DHSc., Branch Chief\n"
                "April 4, 2025\n" + SIG + "Thuy T. Nguyen, Branch Chief 4/4/2025\n")
        self.assertEqual(f._extract_483_inspectors(text), ["Melissa Ray", "Thuy Thanh Nguyen"])

    def test_piv_line_needs_two_identical_readings(self):
        once = SIG + "Digitally signed by\nWAYNE MCGRATH -S\n05/29/2026\n"
        self.assertEqual(f._extract_483_inspectors(once), [])
        twice = once + "Page 2\n" + once
        self.assertEqual(f._extract_483_inspectors(twice), ["WAYNE MCGRATH"])

    def test_reason_codes(self):
        self.assertEqual(f._extract_483_inspectors_detail("")[1], f.INSPECTORS_NO_TEXT)
        self.assertEqual(f._extract_483_inspectors_detail("OBSERVATION 1 text only")[1],
                         f.INSPECTORS_NO_SIGNATURE)
        self.assertEqual(f._extract_483_inspectors_detail(SIG + "~~ ## ~~\n")[1],
                         f.INSPECTORS_PARSER_LIMIT)


class SingleCommonFunctionTest(unittest.TestCase):
    """수용기준 ③ — 세 경로가 같은 함수를 부른다(규칙을 베끼지 않는다)."""

    def test_paths_call_the_common_function(self):
        for mod, fn in ((rec, rec._rebuild_item), (bb, bb.new_record), (tool, tool.evaluate)):
            src = inspect.getsource(fn)
            self.assertIn("_extract_483_inspectors", src, mod.__name__)
            self.assertNotIn("_INSPECTOR_", src, mod.__name__)   # 정규식·직함 목록을 복사하지 않음

    def test_ocr_recovery_rebuild_carries_inspectors(self):
        nrow = f._datatable_norm_rows([[
            "05/27/2026", "BPI Labs, LLC", "3012345678", '<a href="/media/555001/download">483</a>',
            "FL", "", "Pharmaceutical Manufacturer", "06/01/2026", ""]])[0]
        text = ("OBSERVATION 1\nAseptic processing operations were not validated for the sterile "
                "drug product.\nSpecifically, media fills were not performed.\n" + SIG
                + "Jose F Velez, Investigator\n2/27/2026\n")
        item = rec._rebuild_item(nrow, text, "pdf-ok-ocr")
        self.assertEqual(item.raw_payload.get("fda483_inspectors"), ["Jose F Velez"])


def _db_row(doc: str, raw: dict) -> dict:
    text, sha = rv.canonical_raw(raw)
    return {"raw_signal_id": f"rid-{doc}", "document_id": doc, "raw_json": text,
            "row_json": "{}", "raw_sha256": sha}


class BodyFullBackfillTest(unittest.TestCase):
    def test_new_record_adds_inspectors_but_never_overwrites(self):
        body = SIG + "Jose F Velez, Investigator\n2/27/2026\n"
        rec1, _ = bb.new_record(_db_row("a", {"media_id": "1"}), body)
        self.assertEqual(json.loads(rec1["raw_json"])["fda483_inspectors"], ["Jose F Velez"])
        rec2, _ = bb.new_record(_db_row("b", {"media_id": "1", "fda483_inspectors": ["Kept Name"]}), body)
        self.assertEqual(json.loads(rec2["raw_json"])["fda483_inspectors"], ["Kept Name"])
        rec3, _ = bb.new_record(_db_row("c", {"media_id": "1"}), "OBSERVATION 1 only")
        self.assertNotIn("fda483_inspectors", json.loads(rec3["raw_json"]))

    def test_apply_patches_empty_findings_with_same_names(self):
        body = SIG + "Jose F Velez, Investigator\n2/27/2026\n"
        row = _db_row("d", {"media_id": "1", "fda_483_observations": [{"number": "1"}]})
        patched: list = []
        rep = bb.run([row], set(), apply=True, probe_pdf=False, notion_index={"d": "p"}, since="x",
                     notion_body=lambda _p: body, pdf_body=lambda *_a: ("", "x"),
                     applier=lambda **kw: {"status": "applied"}, sleeper=lambda _s: None,
                     findings_patcher=lambda rid, names: patched.append((rid, names)) or "")
        self.assertEqual(rep.inspectors_added, 1)
        self.assertEqual(patched, [("rid-d", ["Jose F Velez"])])


class ReextractToolTest(unittest.TestCase):
    def _rows(self):
        return [
            _db_row("ok", {"fda483_body_full": SIG + "Jose F Velez, Investigator\n2/27/2026\n"}),
            _db_row("red", {"fda483_body_full": _NEGATIVE_TEXTS[0]}),
            _db_row("nosig", {"fda483_body_full": "OBSERVATION 1 text"}),
            _db_row("filled", {"fda483_body_full": SIG + "A B, Investigator",
                               "fda483_inspectors": ["Someone Else"]}),
            _db_row("oos", {"fda483_body_full": "x", "fda483_text_status": "out-of-scope"}),
        ]

    def test_dry_run_table_and_reasons(self):
        rep = tool.run(self._rows(), apply=False)
        self.assertEqual(rep.targets, 3)                     # 이미 채움·범위 밖은 대상 아님
        self.assertEqual(rep.not_verbatim, 0)
        by_doc = {r["document_id"]: r for r in rep.rows}
        self.assertEqual(by_doc["ok"]["after"], ["Jose F Velez"])
        self.assertIn("Jose F Velez", by_doc["ok"]["evidence"][0])
        self.assertEqual(by_doc["red"]["reason"], f.INSPECTORS_REDACTED)
        self.assertEqual(by_doc["nosig"]["reason"], f.INSPECTORS_NO_SIGNATURE)
        self.assertIn("| ok |", tool.markdown(rep))

    def test_apply_writes_raw_then_empty_findings_only(self):
        calls, patched = [], []
        rep = tool.run(self._rows(), apply=True,
                       applier=lambda **kw: calls.append(kw) or {"status": "applied"},
                       findings_patcher=lambda rid, names: patched.append((rid, names)) or (2, ""))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["change_kind"], "repair")
        self.assertEqual(json.loads(calls[0]["record"]["raw_json"])["fda483_inspectors"],
                         ["Jose F Velez"])
        self.assertEqual(patched, [("rid-ok", ["Jose F Velez"])])
        self.assertEqual(rep.findings_rows_patched, 2)

    def test_rows_json_mode_refuses_apply(self):
        self.assertEqual(tool.main(["--rows-json", "x.json", "--apply"]), 2)


if __name__ == "__main__":
    unittest.main()
