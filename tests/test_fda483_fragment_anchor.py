"""483 조각 표제 앵커 — 재수집하면 관찰이 앞 관찰에 합쳐져 사라지던 문제 (2026-11-12).

근거: _ops/reports/GRM_483추출수리_dry_2026-11-12.md §1-b. `OBSERVATION 3.` 다음이
줄바꿈+`(REPEAT OBSERVATION) …` · `... Records…` · `. Your…` · 줄 중간 `A. Annual…` 꼴이면 표제가
"." 가 되고, `_select_observation_anchors` ②가 그 앵커를 상호참조로 보고 기각해 **관찰 전문이 앞
관찰 상세 끝에 합쳐지고 행이 사라졌다**(실측 151431 관찰 3 · 70711 관찰 3 · 91684 관찰 2).

본문은 실측 문서(151431·70711)의 꼴만 남긴 합성 최소 조각이다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as f  # noqa: E402

_PREAMBLE = (
    "FORM FDA 483 (09/08) PREVIOUS EDITION OBSOLETE INSPECTIONAL OBSERVATIONS\n"
    "DURING AN INSPECTION OF YOUR FIRM WE OBSERVED:\n"
)
_OBS1 = (
    "OBSERVATION 1\n"
    "There is a failure to thoroughly review any unexplained discrepancy. Specifically, "
    "the review of Adverse Event Investigation ADE2020081 was not completed.\n"
)
_OBS2 = (
    "OBSERVATION 2\n"
    "Written procedures for cleaning and maintenance of equipment are not followed. "
    "Specifically, the cleaning logbook entries were missing for several batches.\n"
)
_OBS2_HEAD = "Written procedures for cleaning and maintenance of equipment are not followed."


def _rows(text: str) -> list[dict[str, str]]:
    return f._extract_483_observations_from_text(text)


def _by_number(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {r["number"]: r for r in rows}


class FragmentHeadingObservationKeptTest(unittest.TestCase):
    """조각 표제 뒤 진짜 관찰은 제 번호의 행으로 남고, 앞 관찰 상세에 합쳐지지 않는다."""

    def _assert_kept(self, tail: str, number: str, head: str, leaked: str) -> None:
        rows = _rows(_PREAMBLE + _OBS1 + _OBS2 + tail)
        self.assertEqual([r["number"] for r in rows], ["1", "2", number])
        got = _by_number(rows)
        self.assertEqual(got[number]["deficiency"], head)
        self.assertEqual(got["2"]["deficiency"], _OBS2_HEAD)
        self.assertNotIn(leaked, got["2"]["detail"])

    def test_repeat_observation_on_next_line(self) -> None:
        # 151431 관찰 3: `OBSERVATION 3.⏎(REPEAT OBSERVATION) Procedures designed…`
        self._assert_kept(
            "OBSERVATION 3.\n(REPEAT OBSERVATION) Procedures designed to prevent microbiological "
            "contamination of drug products purporting to be sterile are not established. "
            "Specifically, the media fill does not represent routine interventions.\n",
            "3",
            "Procedures designed to prevent microbiological contamination of drug products "
            "purporting to be sterile are not established.",
            "Procedures designed",
        )

    def test_ellipsis_on_next_line(self) -> None:
        # 151431 관찰 10: `OBSERVATION 10⏎... Records…`
        self._assert_kept(
            "OBSERVATION 10\n... Records are not maintained for the distribution of each lot "
            "of drug product. Specifically, the shipping records for three lots were not kept.\n",
            "10",
            "Records are not maintained for the distribution of each lot of drug product.",
            "Records are not maintained",
        )

    def test_dot_on_next_line(self) -> None:
        # 151431 관찰 8: `OBSERVATION 8⏎. Your outsourcing facility…`
        self._assert_kept(
            "OBSERVATION 8\n. Your outsourcing facility compounds drug products that have been "
            "withdrawn from the market. Specifically, your firm compounded the following drugs.\n",
            "8",
            "Your outsourcing facility compounds drug products that have been withdrawn from the market.",
            "Your outsourcing facility",
        )

    def test_mid_line_enumerator_heading(self) -> None:
        # 70711 관찰 3: 줄 중간 `OBSERVATION 3. A. Annual Reports…` — 앞은 표 잔재 `() ()`.
        self._assert_kept(
            "Post-Approval Reporting) () OBSERVATION 3. A. Annual Reports for ANDA's that were submitted to FDA "
            "did not include out of specification results. Specifically, only passing data "
            "points were submitted.\n",
            "3",
            "Annual Reports for ANDA's that were submitted to FDA did not include out of "
            "specification results.",
            "Annual Reports",
        )

    def test_detail_of_revived_observation_is_its_own_rest(self) -> None:
        rows = _rows(_PREAMBLE + _OBS1 + _OBS2 + (
            "OBSERVATION 3.\n(REPEAT OBSERVATION) Procedures designed to prevent microbiological "
            "contamination of drug products purporting to be sterile are not established. "
            "Specifically, the media fill does not represent routine interventions.\n"))
        self.assertEqual(_by_number(rows)["3"]["detail"],
                         "Specifically, the media fill does not represent routine interventions.")


class CrossReferenceStillMergedTest(unittest.TestCase):
    """정상 상호참조 보정(앵커 기각 → 부모 관찰 상세에 남김)은 불변이다."""

    def test_refer_to_observation_for_details(self) -> None:
        # 193490 꼴 — ① "refer to" 로 기각.
        rows = _rows(_PREAMBLE + (
            "OBSERVATION 1\n"
            "The quality control unit lacks the responsibility to approve all procedures. "
            "Specifically,\na. A deviation was not investigated. Please refer to OBSERVATION 2 "
            "for details.\nb. A second deviation was not trended either.\n") + _OBS2)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertIn("refer to OBSERVATION 2 for details", rows[0]["detail"])

    def test_refer_to_with_bare_dot(self) -> None:
        # `refer to⏎⏎OBSERVATION 2: .⏎b. …` — ①·② 둘 다 걸리는 꼴.
        rows = _rows(_PREAMBLE + (
            "OBSERVATION 1\n"
            "The quality control unit lacks the responsibility to approve all procedures. "
            "Specifically,\na. A deviation was not investigated. Please refer to\n\n"
            "OBSERVATION 2: .\nb. A second deviation was not trended either.\n") + _OBS2)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])

    def test_dot_fragment_whose_number_has_a_real_heading_is_rejected(self) -> None:
        # ②로만 걸리는 참조("as described in")이고 조각 뒤가 대문자 열거자 — 번호 2 에 정상
        # 표제가 있으므로(가짜 조각) 종전대로 기각해 관찰 1 상세에 남긴다.
        rows = _rows(_PREAMBLE + (
            "OBSERVATION 1\n"
            "The quality control unit lacks the responsibility to approve all procedures. "
            "Specifically,\na. A deviation was not investigated as described in\n"
            "OBSERVATION 2: .\nB. A second deviation was not trended either.\n") + _OBS2)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertEqual(rows[1]["deficiency"], _OBS2_HEAD)
        self.assertIn("A second deviation was not trended", rows[0]["detail"])

    def test_mid_sentence_reference_to_orphan_number_is_rejected(self) -> None:
        # 문장 중간 참조("as noted in Observation 2. The …") — 참조 대상이 문서에 없어도 앞이
        # 산문 낱말로 끝나면 표제가 아니다(test_fda_483 의 116184 회귀 고정과 같은 꼴).
        rows = _rows(_PREAMBLE + _OBS1.replace(
            "was not completed.",
            "was not completed, as noted in Observation 4. The quality unit did not review it."))
        self.assertEqual([r["number"] for r in rows], ["1"])
        self.assertIn("as noted in Observation 4", rows[0]["detail"])

    def test_lowercase_sub_item_after_orphan_reference_is_rejected(self) -> None:
        # 참조 대상(5)이 문서에 없어도, 조각 뒤가 소문자 하위 항목(`b.`)이면 살리지 않는다.
        rows = _rows(_PREAMBLE + (
            "OBSERVATION 1\n"
            "The quality control unit lacks the responsibility to approve all procedures. "
            "Specifically,\na. A deviation was not investigated as described in\n"
            "OBSERVATION 5: .\nb. A second deviation was not trended either.\n") + _OBS2)
        self.assertEqual([r["number"] for r in rows], ["1", "2"])
        self.assertIn("A second deviation was not trended", rows[0]["detail"])


class NormalDocumentUnchangedTest(unittest.TestCase):

    def test_anchor_selection_of_normal_headings(self) -> None:
        body = _OBS1 + _OBS2 + (
            "OBSERVATION 3\nLaboratory controls do not include scientifically sound test "
            "procedures. Specifically, the HPLC method was not validated.\n")
        matches = list(f._OBS_RE.finditer(body))
        self.assertEqual(f._select_observation_anchors(body, matches), matches)

    def test_split_past_fragment_head_matches_extractor_retitle(self) -> None:
        # 표제는 추출기 소급(PR #1614 `_fda483_title_from_detail`)과 같아야 finding_id 가 같다.
        try:
            import findings_extractors as fx
        except ImportError:  # pragma: no cover — 로컬 의존 부재
            self.skipTest("findings_extractors import 불가")
        for detail in (
            "(REPEAT OBSERVATION) Procedures designed to prevent microbiological contamination "
            "are not established. Specifically, the media fill was inadequate.",
            ". Your outsourcing facility compounds drug products that were withdrawn. Specifically, x.",
            "... Records are not maintained for the distribution of each lot. Specifically, y.",
            "A. Annual Reports for ANDA's that were submitted did not include OOS results. Specifically, z.",
        ):
            head, rest, _ = f._split_past_fragment_head(detail)
            self.assertEqual((head, rest), fx._fda483_title_from_detail(detail), detail)


if __name__ == "__main__":
    unittest.main()
