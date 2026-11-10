"""483 관찰 상세 수집 품질 (2026-11-10 지식 그래프 방향 전환 0단계).

저장된 상세 10,585건의 결함 셋을 고친다:
  ① 여러 쪽에 걸친 관찰이 첫 쪽 서명란에서 잘림(다음 쪽 본문 유실)
  ② 서명란 이름줄(`<이름>, DDC Investigator`)이 상세 꼬리에 남음
  ③ 가림 표시 `(b)(4)` 가 스캔 판독으로 `m@`·`O©` 같은 깨진 글자로 남음
그리고 상한(종전 1200자)에서 문장 중간 절단을 만들지 않는다.

고정 자료는 실제 문서(fda483-195241·195434·195639)의 저장 원문에서 쪽 경계 부분만 줄인 것이다.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_fda_483 as c  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _obs(doc: str, est: str) -> list[dict[str, str]]:
    with open(os.path.join(FIX, f"fda483_{doc}_pagebreak.txt"), encoding="utf-8") as fh:
        text = fh.read()
    return c._extract_483_observations_from_text(text, {"establishment_type": est})


class PageJoinTest(unittest.TestCase):
    def test_continuation_on_next_page_is_kept(self):
        obs = _obs("195434", "Plasmapheresis Center")
        self.assertEqual(len(obs), 1)
        d = obs[0]["detail"]
        self.assertTrue(d.endswith("were not shipped to the testing laboratory."), d[-120:])
        for junk in ("Carmelita", "EMPLOYEE", "Page ", "Coronado", "objectionable"):
            self.assertNotIn(junk, d)

    def test_signer_name_line_before_marker_is_removed(self):
        obs = _obs("195241", "Finished Drug Product Manufacturer")
        d = obs[0]["detail"]
        self.assertIn("audit trail history log", d)           # 2쪽에서 이어진 본문
        for junk in ("Upadhyay", "Ranjan", "Investigator", "Maharashtra", "Parklawn"):
            self.assertNotIn(junk, d)
        self.assertEqual(obs[0]["deficiency"],
                         "The responsibilities and procedures applicable to the quality "
                         "control unit are not fully followed.")

    def test_detail_starting_on_next_page_is_recovered(self):
        obs = _obs("195639", "Tissue Processor")
        self.assertEqual([o["number"] for o in obs], ["1", "2", "3"])
        self.assertTrue(obs[1]["detail"].startswith(
            "Specifically, Your firm does not conduct environmental monitoring"), obs[1]["detail"])
        self.assertNotIn("Mcgann", " ".join(o["detail"] for o in obs))
        self.assertNotIn("objectionable", obs[2]["detail"])

    def test_page_word_in_prose_is_not_a_page_break(self):
        seg = ("Specifically, step 4 on page 2 of 5 of the batch record was blank.\n"
               "The deviation was not investigated.")
        self.assertEqual(c._join_483_pages(seg), seg)

    def test_unresolvable_boundary_cuts_like_before(self):
        seg = ("Specifically, the line was not cleaned.\nEMPLOYEE(S) SIGNATURE\n"
               "Page 1 of 3\nrandom tail without header")
        self.assertEqual(c._clean_observation_chunk(c._join_483_pages(seg)),
                         "Specifically, the line was not cleaned.")

    def test_garbled_page_number_still_joins(self):
        # 11-10 실측 fda483-195384: 쪽 번호가 `Page lof5` 로 판독됐다.
        seg = ("Specifically, you did not perform PQ studies for the following packaging machines\n"
               "DATE ISSUED\nOF THIS PAGE\nYvins Dezan, Investigator\nFORM FDA 483 (09/08)\n"
               "Page lof5\n\nFOOD AND DRUG ADMINISTRATION\nDISTRICT ADDRESS AND PHONE NUMBER\n"
               "CITY, STATE, ZIP CODE, COUNTRY\nTYPE ESTABLISHMENT INSPECTED\n"
               "Parsippany, NJ 07054-4212\nContract Packager\n"
               "used to package the commercial drug products (b) (4) and others.\n")
        out = c._clean_observation_chunk(c._join_483_pages(seg, "Contract Packager"))
        self.assertTrue(out.endswith("used to package the commercial drug products (b) (4) and others."), out)
        self.assertNotIn("Dezan", out)

    def test_header_without_establishment_label_ends_at_est_value(self):
        # 11-10 표본 대조 실측 fda483-195665: 머리말에 `TYPE ESTABLISHMENT INSPECTED` 가 판독되지 않았다.
        seg = ("Specifically, the operator's arms were observed reaching over the line, blocking laminar airflow. The\n"
               "EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE\nLisa Flores, OGPS Investigator\nREVERSE\n"
               "FORM FDA 483 (09/08)\nPREVIOUS EDITION OBSOLETE\nPage 2 OF 14\n\n"
               "DEPARTMENT OF HEALTH AND HUMAN SERVICES\nFOOD AND DRUG ADMINISTRATION\n"
               "DISTRICT ADDRESS AND PHONE NUMBER\n12420 Parklawn Drive, Room 2032 Rockville, MD 20857\n"
               "11/03/2025-1 1/14/2025\n3009883410\nNAME AND TITLE OF INDIVIDUAL TO WHOM REPORT ISSUED\n"
               "Mr. Atul Shastri, President-Operations\nFIRM NAME\nSTREET ADDRESS\n"
               "Unit - 2, A - 1128 & B - 1127 Bhiwadi\nRiico Industrial Area, Rajasthan, 301019, India\n"
               "Sterile Drug Product Manufacturer\n"
               "operator's gowning is sampled for environmental monitoring at the end of setup.\n")
        out = c._clean_observation_chunk(c._join_483_pages(seg, "Sterile Drug Product Manufacturer"))
        self.assertTrue(out.endswith("blocking laminar airflow. The operator's gowning is sampled for "
                                     "environmental monitoring at the end of setup."), out)
        for junk in ("Flores", "Bhiwadi", "Shastri", "Manufacturer"):
            self.assertNotIn(junk, out)

    def test_header_without_establishment_label_ends_at_email(self):
        # 11-10 실측 fda483-195637: 머리말이 주소·이메일까지만 판독됐다.
        seg = ("Additionally, your firm has not established a Design Risk Analysis for your firm's BGRs, AHG, and\n"
               "EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nSEE REVERSE | Creighton T Tuzon,\nInvestigator\n"
               "4/19/2024\nOF THIS PAGE| Richard T Riggie,\nInvestigator\nx\n\n"
               "FOOD AND DRUG ADMINISTRATION\nDISTRICT ADDRESS AND PHONE NUMBER\n"
               "10903 New Hampshire Ave,\nSilver Spring,\nMD 20993-0002\n(240) 402-9160\n"
               "orabioinspectionalcorrespondence@fda.hhs.gov\n"
               "Reagent Red Blood Cell reagents.\nFor example, your firm received a complaint.\n")
        out = c._clean_observation_chunk(c._join_483_pages(seg, "Biologics IVD Manufacturer"))
        self.assertIn("BGRs, AHG, and Reagent Red Blood Cell reagents.", out)
        self.assertTrue(out.endswith("your firm received a complaint."), out)
        self.assertNotIn("fda.hhs.gov", out)
        self.assertNotIn("Tuzon", out)

    def test_extra_signer_titles_and_tail_junk(self):
        seg = "Specifically, the media fill failed. Susan S. Butler, Staff Fellow"
        self.assertEqual(c._signer_cut(seg), seg.index("Susan"))
        self.assertIsNotNone(c._DETAIL_TAIL_JUNK_RE.search("pipe coupling. aeGNATURE"))

    def test_trim_tail(self):
        t = c.trim_detail_tail
        self.assertEqual(t("limits are exceeded. OF THIS PAGE Burnell M Henry, Investigator Invest ogo"),
                         "limits are exceeded.")
        self.assertEqual(t("operations. 'NATURE,"), "operations.")
        # 본문 중간의 "…, Investigator Smith noted" 는 산문이라 남긴다.
        mid = "Specifically, Investigator Smith noted the gap, Investigator Jones agreed. " + "x" * 120 + "."
        self.assertEqual(t(mid), mid)


class RedactionTest(unittest.TestCase):
    def test_garbled_and_canonical_marks_become_one_token(self):
        n = c.normalize_483_redactions
        self.assertEqual(n("for O© Tablets @@ mg, Batch: w@ ."),
                         "for [redacted] Tablets [redacted] mg, Batch: [redacted].")
        self.assertEqual(n("relating to m@\nstages"), "relating to [redacted]\nstages")
        self.assertEqual(n("Tabletsa@ mg"), "Tablets [redacted] mg")
        self.assertEqual(n("(©)@) plates"), "[redacted] plates")
        self.assertEqual(n("the (b) (4) and (b)(4), (b)(6) records"), "the [redacted] and [redacted] records")
        for v in ("(1) (4)", "(D)(4)", "(9) (4)", "(Bb) (4)", "(1D) (4)", "(6) (4~", "(®)(4)"):
            self.assertEqual(n(f"label {v} units"), "label [redacted] units", v)

    def test_non_redaction_at_signs_are_kept(self):
        n = c.normalize_483_redactions
        self.assertEqual(n("incubated @ 30-35C"), "incubated @ 30-35C")
        self.assertEqual(n("write to Response@fda.hhs.gov today"), "write to Response@fda.hhs.gov today")

    def test_symbol_only_tokens_are_dropped(self):
        self.assertEqual(c.normalize_483_redactions("collected on) * ! were not"), "collected on) were not")


class NoStorageCapTest(unittest.TestCase):
    """저장 단계에서는 자르지 않는다(대표 원칙 2026-11-10) — 상세 길이에 상한이 없다."""

    def test_long_detail_is_stored_whole(self):
        text = "Specifically, the record was incomplete. " * 600 + "Unfinished tail that goes on"
        self.assertEqual(c._finalize_detail(text), text.strip())

    def test_no_cap_constant(self):
        self.assertFalse(hasattr(c, "FDA483_OBSERVATION_DETAIL_MAX_CHARS"))
        self.assertFalse(hasattr(c, "_cap_detail"))


if __name__ == "__main__":
    unittest.main()
