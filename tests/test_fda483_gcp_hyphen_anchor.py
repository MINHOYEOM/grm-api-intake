"""483 GCP/BIMO 양식 하이픈 번호 관찰 앵커 (2026-11-13).

#1602 로 의약품 임상 스폰서·CRO 483 이 범위 안이 되었는데, 이 양식은 관찰 번호가
`OBSERVED: 1-In study … 2-In study … 3- In study …` 꼴이라 `OBSERVATION N`·`N.`·`N)` 앵커 어디에도
걸리지 않아 관찰 0건이 됐다(실측 fda483-193617 ICON plc). 아래 `_ICON` 은 그 문서
`fda483_excerpt` 의 고정값(원문 그대로)이다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402

_ICON = (
    "DURING AN INSPECTION OF YOUR FIRM (1) (WE) OBSERVED: 1-In study Je for theOA the firm used "
    "calibration standards and QC samples outside the established stability time period of 35 days. "
    "Specifically, ISR run 16 was performed on 01/26/2024 with calibration standards and QCs prepared "
    "on 12/07/2023. ISR run 16 was accepted. The calibration standards and QCs were used after being "
    "stored for 50 days at -70°C when only 35 days oflong-term stability for ee in plasma at "
    "-70°C was established. 2-In study JO for the Te a run was stopped due to instrument related "
    "errors and samples already injected were reinjected, but samples reinjection was not included in "
    "the study report. Specifically, run 1 was stopped and subsequently reinjected. 3- In study JO for "
    "theA, samples for subject (@) were reprocessed but not reported. Specifically, subject samples in "
    "a plate for run 7 did not meet plate acceptance criteria and were subsequently reprocessed in run "
    "15. Add Continuation Page EMPLOYEE(S) SIGNATURE EMPLOYEE(S) NAME AND TITLE (Print or Type) DATE "
    "ISSUED SEE REVERSE a oFees Sylvestre K. Dossou -S Pistaly sisned by Sylvestre K Dossou-S | "
    "Sylvestre K Dossou, Ph.D., Biologist 06/13/2025 FORM FDA 483 (9/08) PREVIOUS EDITION OBSOLETE "
    "INSPECTIONAL OBSERVATIONS Page 1 of 1 The observations of objectionable conditions and practices "
    "listed on the front of this form are reported: 2. To assist firms inspected in complying with the "
    "Acts and regulations enforced by the Food and Drug Administration. 1. Purs"
)

_MARKER = "DURING AN INSPECTION OF YOUR FIRM (I) (WE) OBSERVED: "
_O1 = ("In study ABC-101 the sponsor failed to ensure proper monitoring of the investigation. "
       "Specifically, monitoring visits were not conducted at site 12.")
_O2 = ("In study ABC-102 the sponsor did not maintain adequate records of drug disposition. "
       "Specifically, shipment logs were missing for three sites.")
_O3 = ("In study ABC-103 the firm failed to report deviations to the sponsor. "
       "Specifically, two protocol deviations were not reported.")


def _rows(text: str) -> list[dict[str, str]]:
    return f._extract_483_observations_from_text(text)


class IconFixtureTest(unittest.TestCase):
    def test_three_observations_numbered(self):
        rows = _rows(_ICON)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3"])

    def test_heading_is_in_study_sentence(self):
        rows = _rows(_ICON)
        self.assertEqual(
            rows[0]["deficiency"],
            "In study Je for theOA the firm used calibration standards and QC samples outside "
            "the established stability time period of 35 days.")
        self.assertTrue(rows[1]["deficiency"].startswith("In study JO for the Te a run was stopped"))
        self.assertEqual(rows[2]["deficiency"],
                         "In study JO for theA, samples for subject (@) were reprocessed but not reported.")

    def test_detail_kept_whole_and_no_boilerplate(self):
        rows = _rows(_ICON)
        self.assertIn("-70°C was established.", rows[0]["detail"])
        self.assertTrue(rows[0]["detail"].startswith("Specifically, ISR run 16"))
        self.assertIn("reprocessed in run 15.", rows[2]["detail"])
        for r in rows:
            self.assertNotIn("FORM FDA 483", r["detail"])
            self.assertNotIn("To assist firms", r["detail"])


class HyphenVariantTest(unittest.TestCase):
    def _assert_three(self, sep1: str, sep2: str, sep3: str) -> None:
        text = f"{_MARKER}1{sep1}{_O1} 2{sep2}{_O2} 3{sep3}{_O3}"
        rows = _rows(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2", "3"], text)
        self.assertTrue(rows[0]["deficiency"].startswith("In study ABC-101"))
        self.assertTrue(rows[2]["deficiency"].startswith("In study ABC-103"))

    def test_no_space(self):
        self._assert_three("-", "-", "-")

    def test_space_after_hyphen(self):
        self._assert_three("- ", "- ", "- ")

    def test_spaces_around_hyphen(self):
        self._assert_three(" - ", " - ", " - ")

    def test_en_dash(self):
        self._assert_three("–", "– ", "–")

    def test_dot_form_still_three(self):
        # `N. In study` 는 기존 번호 목록 경로(③)가 잡는다 — 같은 결과여야 한다.
        self._assert_three(". ", ". ", ". ")

    def test_line_head_form(self):
        text = f"{_MARKER}\n1-{_O1}\n2-{_O2}\n3-{_O3}"
        self.assertEqual([r["number"] for r in _rows(text)], ["1", "2", "3"])


class FalseAnchorTest(unittest.TestCase):
    def test_compound_word_not_anchor(self):
        # `7-In-process` 는 하이픈 복합어 — 관찰 표제가 아니다.
        o1 = ("In study ABC-101 the firm failed to test samples. Specifically, the "
              "results for lot 7. 7-In-process controls were not documented for batch 12.")
        rows = _rows(f"{_MARKER}1-{o1} 2-{_O2}")
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertIn("7-In-process controls", rows[0]["detail"])

    def test_compound_word_regex_rejects(self):
        # 순차 번호(2)여도 하이픈 복합어는 앵커 정규식 단계에서 걸린다.
        self.assertEqual(list(f._HYPHEN_NUMBERED_OBS_RE.finditer(". 2-In-process controls")), [])
        self.assertEqual(len(list(f._HYPHEN_NUMBERED_OBS_RE.finditer(". 2-In study X"))), 1)

    def test_prose_number_hyphen_not_anchor(self):
        # 실측 82100 꼴 `15-Day Report` — 순차 번호가 아니므로 표제가 아니다.
        o1 = ("In study ABC-101 the sponsor failed to submit safety reports. Specifically, "
              "the deadline was missed. 15-Day Report Submission Due Date was not met.")
        rows = _rows(f"{_MARKER}1-{o1} 2-{_O2}")
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertIn("15-Day Report", rows[0]["detail"])

    def test_first_anchor_must_follow_marker(self):
        # 마커 바로 뒤가 `1-` 가 아니면 하이픈 앵커를 쓰지 않는다.
        text = (f"{_MARKER}The firm reviewed records. Specifically, see item list. "
                f"2-{_O2} 3-{_O3}")
        nums = [r["number"] for r in _rows(text)]
        self.assertNotIn("2", nums)
        self.assertNotIn("3", nums)

    def test_selector_rejects_non_one_start(self):
        s = ": 2-In study X failed. 3-In study Y failed."
        ms = list(f._HYPHEN_NUMBERED_OBS_RE.finditer(s))
        self.assertEqual(len(ms), 2)
        self.assertEqual(f._select_hyphen_numbered_anchors(s, ms), [])


    def test_selector_rejects_one_not_at_marker(self):
        # `1-` 이 마커 바로 뒤가 아니라 산문 뒤에 오면 쓰지 않는다.
        s = ": The firm reviewed records. 1-In study X failed. 2-In study Y failed."
        ms = list(f._HYPHEN_NUMBERED_OBS_RE.finditer(s))
        self.assertEqual(len(ms), 2)
        self.assertEqual(f._select_hyphen_numbered_anchors(s, ms), [])
        nums = [r["number"] for r in _rows(
            f"{_MARKER}The firm reviewed records. 1-{_O1} 2-{_O2}")]
        self.assertNotEqual(nums, ["1", "2"])


class ObservationAnchorUnchangedTest(unittest.TestCase):
    def test_observation_n_wins_over_hyphen(self):
        # `OBSERVATION N` 표제가 있는 문서는 정상 경로 결과 그대로다(하이픈 꼴은 상세 속 산문).
        text = (
            "DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
            "OBSERVATION 1\n"
            "There is a failure to thoroughly review any unexplained discrepancy. Specifically, "
            "1-In study ABC the review was not completed.\n"
            "OBSERVATION 2\n"
            "Written procedures for cleaning are not followed. Specifically, 2-In study DEF "
            "logbook entries were missing.\n"
        )
        rows = _rows(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertEqual(rows[0]["deficiency"],
                         "There is a failure to thoroughly review any unexplained discrepancy.")
        self.assertIn("1-In study ABC", rows[0]["detail"])
        self.assertEqual(rows[1]["deficiency"], "Written procedures for cleaning are not followed.")

    def test_dot_numbered_list_unchanged(self):
        text = (f"{_MARKER}1. There is no written procedure for cleaning. Specifically, none. "
                "2. Equipment is not calibrated. Specifically, the balance was overdue.")
        rows = _rows(text)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertEqual(rows[0]["deficiency"], "There is no written procedure for cleaning.")


if __name__ == "__main__":
    unittest.main()
