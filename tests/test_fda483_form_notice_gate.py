"""483 서식 뒷면 안내문·법령문이 관찰 표제가 되지 않게 (정상 경로 게이트, 2026-11-13).

483 양식 뒷면에는 "The observations … are reported: 1. Pursuant to Section 704(b) of the Federal
Food, Drug and Cosmetic Act, or 2. To assist firms inspected in complying with the Acts and
regulations enforced by the Food and Drug Administration." 가 인쇄돼 있다. 번호 목록 앵커가 이
`1.`·`2.` 를 관찰 번호로 잡으면 안내문이 관찰 표제로 공개됐다(실측 4문서: fda483-195517 Henogen
`Toassist firms…` · 165565 `Toassist firms…` · 70841 `Pursuant te Section 704(b)…` · 89974
`P ursuant to Section 704(b)…`). OCR 이 붙이거나 가른 꼴은 `_FORM_BOILERPLATE_RE` 의 `\\s+` 를
빠져나갔고, 정상 경로 게이트(`_is_publishable_deficiency`)에는 양식 검사가 아예 없었다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# 실측 표제 고정값(라이브 findings 원문 그대로).
_HENOGEN_8CFD = ("Toassist firms inspected in complying with the Acts and regulations enforced by "
                 "the Food and Drug Administration.")
_NOTICE_VARIANTS = (
    _HENOGEN_8CFD,                                                                  # 195517·165565
    "To assist firms inspected in complying with the Acts and regulations enforced by the Food "
    "and Drug Administration.",
    "To assist firms in spected in co mplying wi th th e Ac ts an d re gulations en forced by the "
    "Fo od and Drug Administration.",                                               # 89974 상세 꼴
    "Pursuant te Section 704(b) of the Federal Food, Drug and Cosmetic a Act, or : ; .",  # 70841
    "P ursuant to Section 704(b) of the Federal Food, Drug and Cosmetic Act, or 2 .",     # 89974
    "Pursuant to Section 704(b) of the Federal Food, Drug and Cosmetic Act, or",
    "Pursuant to Section 704 (6) of the Federal Food, Drug and Cosmetic Act, or",   # b→6 OCR
)
# 정상 표제 — 서식 낱말이 섞여도 관찰이다(오탐 방지). 라이브 상세·표제 실측 문형에서 뽑았다.
_REAL_HEADINGS = (
    # 704(a) 는 실사 통지·기록 요청 근거라 진짜 관찰에 나온다(실측 145127·189059 상세).
    "Records requested pursuant to Section 704(a)(4) of the Federal Food, Drug, and Cosmetic Act "
    "were not provided.",
    "Notice of Inspection is hereby given pursuant to Section 704(a)(1) of the Federal Food, Drug, "
    "and Cosmetics Act and the firm did not allow access.",
    "Corrective actions committed in response to the previous Form FDA 483 were not implemented.",
    "Procedures designed to assist operators in complying with gowning requirements are not followed.",
    # 꼬리에 서명란이 붙은 진짜 관찰(실측 83317) — 넓은 _FORM_BOILERPLATE_RE 라면 통째로 버렸다.
    "Specifically, you do not note the time on all of your SE prevention records to include: "
    "Rodent Control Inspection Log.Employee(s) Signature Christian L.",
    "There is a lack of adequate control data with regard to manufacturing of viral vector.",
)

_OLD_BOILERPLATE_RE = re.compile(
    r"OR\s+PLAN\s+TO\s+IMPLEMENT\s+CORRECTIVE\s+ACTION"
    r"|PURSUANT\s+TO\s+SECTION\s+704"
    r"|SEE\s+REVERSE\s+OF\s+THIS\s+PAGE"
    r"|THIS\s+DOCUMENT\s+LISTS\s+OBSERVATIONS\s+MADE"
    r"|DURING\s+AN\s+INSPECTION\s+OF\s+YOUR"
    r"|FORM\s+FDA\s+483"
    r"|EMPLOYEE\(S\)\s+SIGNATURE"
    r"|ANNOTATIONS?\s+TO\s+OBSERVATIONS?"
    r"|ADD\s+CONTINUATION\s+PAGE"
    r"|DEPARTMENT\s+OF\s+HEALTH\s+AND\s+HUMAN\s+SERVICES"
    r"|INSPECTIONAL\s+OBSERVATIONS?\s*$"
    r"|NAME\s+AND\s+TITLE\s+OF\s+INDIVIDUAL"
    r"|TO\s+ASSIST\s+FIRMS\s+INSPECTED\s+IN\s+COMPLYING",
    re.I,
)


def _old_publishable(deficiency: str) -> bool:
    return f._is_legible_deficiency(deficiency) and f._has_function_word(deficiency)


def _rows(text: str) -> list[dict[str, str]]:
    return f._extract_483_observations_from_text(text)


def _rows_with_old_gates(text: str) -> list[dict[str, str]]:
    """변경 전 게이트(양식 검사 없는 정상 게이트 + 옛 양식 정규식)로 같은 텍스트를 돌린다."""
    with mock.patch.object(f, "_is_publishable_deficiency", _old_publishable), \
            mock.patch.object(f, "_FORM_BOILERPLATE_RE", _OLD_BOILERPLATE_RE):
        return f._extract_483_observations_from_text(text)


def _dump(rows) -> str:
    return json.dumps(rows, ensure_ascii=False, sort_keys=True)


_BACK_PAGE = (
    "The observations of objectionable conditions and practices listed on the front of this form\n"
    "are reported:\n1.\nPursuant to Section 704(b) of the Federal Food, Drug and Cosmetic Act, or\n"
    "2.\nToassist firms inspected in complying with the Acts and regulations enforced by the\n"
    "Food and Drug Administration.\n"
    "Section 704(b) of the Federal Food, Drug, and Cosmetic Act (21 USC 374(b)) provides:\n"
    "\"Upon completion of any such inspection of a factory, warehouse, consulting\n"
    "laboratory, or other establishment, and prior to leaving the premises, the officer or\n"
    "employee making the inspection shall give to the owner a report in writing.\"\nPage 2 of 2\n"
)

# fda483-195517(Henogen) fda483_body_full 의 구조를 그대로 줄인 꼴 — 관찰은 번호 없이 마커 바로
# 뒤 한 건이고, 뒷면 안내문만 `1.`·`2.` 번호를 가진다. 옛 코드는 ③ 번호 목록이 `2. Toassist…` 를
# 관찰로 냈다(라이브 8cfd8f30 행).
_HENOGEN_LIKE = (
    "This document lists observations made by the FDA representative(s) during the inspection of "
    "your facility.\nDURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
    "There is a lack of adequate control data with regard to manufacturing of viral vector.\n"
    "Specifically, the batch records do not include a reconciliation of production yield.\n"
    "EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE OF THIS PAGE\nPage 1 of 2\n\n" + _BACK_PAGE
)

# 165565·70841·89974 꼴 — 번호 목록 관찰 사이에 뒷면 안내문 번호가 끼어 있다.
_NUMBERED_WITH_NOTICE = (
    "DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
    "1. Procedures for cleaning equipment are not written or followed. Specifically, no SOP.\n"
    "2. Toassist firms inspected in complying with the Acts and regulations enforced by the "
    "Food and Drug Administration.\n"
    "3. Laboratory controls do not include the establishment of scientifically sound "
    "specifications. Specifically, no limits.\n"
)


class NoticeRegexTest(unittest.TestCase):
    def test_variants_are_notice(self):
        for text in _NOTICE_VARIANTS:
            self.assertTrue(f._FORM_NOTICE_RE.search(text), text)

    def test_variants_fail_normal_gate(self):
        for text in _NOTICE_VARIANTS:
            self.assertFalse(f._is_publishable_deficiency(text), text)

    def test_variants_fail_recovery_gate(self):
        for text in _NOTICE_VARIANTS:
            self.assertFalse(f._is_recovered_deficiency_publishable(text), text)

    def test_henogen_fixed_value(self):
        # 라이브 8cfd8f30 표제 — 옛 정규식(`TO\s+ASSIST`)을 빠져나간 바로 그 꼴.
        self.assertIsNone(_OLD_BOILERPLATE_RE.search(_HENOGEN_8CFD))
        self.assertFalse(f._is_publishable_deficiency(_HENOGEN_8CFD))

    def test_real_headings_not_notice(self):
        for text in _REAL_HEADINGS:
            self.assertIsNone(f._FORM_NOTICE_RE.search(text), text)
            self.assertEqual(f._is_publishable_deficiency(text), _old_publishable(text), text)


class ExtractionTest(unittest.TestCase):
    def test_henogen_notice_not_published(self):
        old = _rows_with_old_gates(_HENOGEN_LIKE)
        self.assertIn(_HENOGEN_8CFD, [r["deficiency"].replace("\n", " ") for r in old],
                      "전제: 옛 게이트는 안내문을 관찰로 냈다")
        new = _rows(_HENOGEN_LIKE)
        for r in new:
            self.assertIsNone(f._FORM_NOTICE_RE.search(r["deficiency"]), r)

    def test_normal_path_gate_drops_notice_heading(self):
        # 정상 경로(OBSERVATION N 앵커)에서 안내문이 표제 자리에 오면 그 행만 빠진다 — 이 경로는
        # 회수 게이트를 거치지 않으므로 정상 게이트의 양식 검사만이 막는다.
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\nOBSERVATION 1\n"
                "Procedures for cleaning equipment are not written or followed. Specifically, no SOP.\n"
                "OBSERVATION 2\n" + _HENOGEN_8CFD + "\n"
                "OBSERVATION 3\nLaboratory controls are deficient. Specifically, no limits.\n")
        old = _rows_with_old_gates(text)
        new = _rows(text)
        self.assertEqual([r["number"] for r in old], ["1", "2", "3"], "전제: 옛 정상 게이트는 통과시켰다")
        self.assertEqual([r["number"] for r in new], ["1", "3"])
        self.assertEqual(_dump(new), _dump([r for r in old if r["number"] != "2"]))

    def test_numbered_list_drops_only_notice_row(self):
        old = _rows_with_old_gates(_NUMBERED_WITH_NOTICE)
        new = _rows(_NUMBERED_WITH_NOTICE)
        self.assertEqual([r["number"] for r in old], ["1", "2", "3"])
        self.assertEqual([r["number"] for r in new], ["1", "3"])
        # 남은 행은 옛 출력과 바이트 동일(안내문 행만 빠진다).
        self.assertEqual(_dump(new), _dump([r for r in old if r["number"] != "2"]))


class UnchangedOutputTest(unittest.TestCase):
    """서식 안내문이 표제가 되지 않는 문서는 옛 게이트와 출력이 바이트 동일하다."""

    def test_fixture_texts_identical(self):
        names = sorted(n for n in os.listdir(_FIXTURES)
                       if n.startswith("fda483_") and n.endswith(".txt"))
        self.assertTrue(names)
        for name in names:
            with open(os.path.join(_FIXTURES, name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertEqual(_dump(_rows(text)), _dump(_rows_with_old_gates(text)), name)

    def test_recovery_path_without_notice_identical(self):
        # 회수 경로(③ 번호 목록) — 안내문이 없으면 옛 게이트와 같다.
        text = _NUMBERED_WITH_NOTICE.replace(
            "2. Toassist firms inspected in complying with the Acts and regulations enforced by the "
            "Food and Drug Administration.\n",
            "2. Written procedures for production are not followed. Specifically, steps skipped.\n")
        rows = _rows(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3"])
        self.assertEqual(_dump(rows), _dump(_rows_with_old_gates(text)))

    def test_normal_path_with_back_page_identical(self):
        # 정상 경로(OBSERVATION N) 문서는 뒷면 안내문이 본문에 있어도 표제가 되지 않는다.
        text = ("DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\nOBSERVATION 1\n"
                "Procedures for cleaning equipment are not written or followed. Specifically, no SOP.\n"
                "OBSERVATION 2\nLaboratory controls are deficient. Specifically, no limits.\n"
                + _BACK_PAGE)
        rows = _rows(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertEqual(_dump(rows), _dump(_rows_with_old_gates(text)))


if __name__ == "__main__":
    unittest.main()
