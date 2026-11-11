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


def _joined(seg: str, est: str = "") -> str:
    return c._clean_observation_chunk(c._join_footer_pages(c._join_483_pages(seg, est), est))


class FooterBlockJoinTest(unittest.TestCase):
    """[11-11] 다시 읽기 뒤 잘림 띠 잔존 63건 진단 — 쪽 블록 순서가 '서명란 → 머리말 → 본문' 이 아닌 꼴.
    고정 자료는 실제 문서의 쪽 경계 원문을 줄인 것이다(서명자·수신인 이름은 가상 이름으로 바꿨다)."""

    def test_header_then_footer_then_body(self):
        # fda483-104451 obs2: 머리말 → 서명란 → 다음 쪽 본문(`e)no environmental…`).
        seg = ("d)cleaning procedures for the sterile compounding room include dry sweeping the floor which may\n"
               "cause an increase in airborne particulates immediately preceding production operations.\n"
               "DEPARTMENT OF HEALTH AND HUMAN SERVICES\nFOOD AND DRUG ADMINISTRATION\n"
               "DISTRICT ADDRESS AND PHONE NUMBER\nUS Customhouse Rm900 2nd & Chestnut St\nPhiladelphia, PA 19106\n"
               "CITY. STATE. ZIP CODE. COUNTRY\nPittsburgh, PA 15213- 3337\nTYPE ESTABLISHMENT INSPECTED\n"
               "Pro duc er of Sterile Drug Pro duc ts\nSEE REVERSE\nOF THIS PAGE\nEMPLOYEE(S) SIGNATURE\n"
               "Jane Q Doe, Investigato r\n(CTNH)\nl/l7/l0l7\nJohn R Roe, I n vestigato r\nX Jahn R Rae\n"
               "OATE lSSUEO\n2/17/2017\nFORM FDA 483 (09108)\nPREVIOUS EDmON OBSOlEJE\nINSPECTIONAL OBSERVATIONS\n"
               "PAGE 3 OF 5PAGES\n"
               "e)no environmental monitoring is performed during production in <Redacted B4> glovebox isolator or in the steri\n"
               "le Compounding Room.\n")
        out = _joined(seg, "Producer of Sterile Drug Products")
        self.assertTrue(out.startswith("d)cleaning procedures"), out)
        self.assertTrue(out.endswith("glovebox isolator or in the steri le Compounding Room."), out)
        for junk in ("Doe", "Roe", "Customhouse", "Pittsburgh", "SEE REVERSE", "PAGE 3"):
            self.assertNotIn(junk, out)

    def test_footer_then_unlabeled_header_values_then_body(self):
        # fda483-96388 obs2: 서명란 → 라벨 없는 머리말 값(FEI·주소·시설 유형) → 끊긴 문장의 나머지.
        seg = ("Specifically, there is no documentation of the actual pressure differential measurement during "
               "operations. The documentation of the <Redacted B4> check of the\n"
               "EMPLOYEE(&) SIGNATLJRE\nDATE ISSUED\n;\nJane Q. Doe, CSO fYl~-&~1\\1., ~\nSEE REVERSE\n"
               "John P. Roe, CSO ,f~f.~ . t ,~\n03/02/2016\nOF THIS PAGE\nI\nFORM TDA 483 (09/08)\n"
               "PIISVIOUS EDmON OBSOLI!'Il!\nINSPECT~ON.~\nL OBSERVATIONS\nPAGE20P8PAGBS\n- 03/02/2016\n"
               "3012038236\nDr i ve\nSte 203\nAustin, TX\n78745-525 5\nProducer of Sterile\nProducts\n"
               "pressure differential only documents \"pass\" or \"fail\" for the reading.\n")
        out = _joined(seg, "Producer of Sterile Drug Products")
        self.assertTrue(out.endswith('check of the pressure differential only documents "pass" or "fail" '
                                     'for the reading.'), out)
        for junk in ("Doe", "Roe", "Austin", "3012038236", "OBSERVATIONS"):
            self.assertNotIn(junk, out)

    def test_header_values_then_footer_then_list_item(self):
        # fda483-82819 obs2: 라벨 없는 머리말 값 → 서명란(`SEE REVERSE`·`OF THIS PAGe`) → 다음 하위 항목 `c)`.
        seg = ("b) Case ID# C200911161408olm, an adverse reaction was reported on 11/14/09. The final quality "
               "review did not occur until 01/05/10 (approximately 6 weeks later).\n\n"
               "2010*\n3002345935\nRed Cross, The\nBlvd\nAZ\nB57l0-1404\nDonor Center\n"
               "SEE REVERSE\nJane L. Doe, Investigator\nOF THIS PAGe\n"
               "c) Case ID# C2009122912550r, an adverse reaction was reported involving LOC less than 1 minute.\n")
        out = _joined(seg, "Donor Center")
        self.assertTrue(out.endswith("c) Case ID# C2009122912550r, an adverse reaction was reported involving "
                                     "LOC less than 1 minute."), out)
        self.assertNotIn("Doe", out)
        self.assertNotIn("SEE REVERSE", out)

    def test_garbled_header_anchor_words(self):
        # fda483-162642 obs4: 머리말 낱말이 `ADMINlSTRATION`·`ESTABUSHMEITT` 로 깨졌고, 시설 유형 값이
        # 힌트(`Producer of Sterile and Non Sterile Drug Products`)와 표기만 다르다.
        seg = ("The light panel is located above the bench top where production of sterile injectable drug "
               "product is occurring. For example:\n"
               "EMPLOYEE(S) NAME AND TITLE (Print or Ty~)\nDATE ISSUED\nSEE REVERSE\nJane N. Doe, Investigator\n"
               "03/14/2022\nOF THIS PAGE\nFORM FDA 483 (09/08)\nPREVIOUS EDmON 08$01.lrrE\n"
               "INSPECTION AL OBSERVATlONS\nPAGE 6 OF 16PAGES\n\nDEPARTMENT OF IIEALTH AND HUMAN SERVICES\n"
               "FOOD AND DRUG ADMINlSTRATION\nDISTRICT AOORESS AND PHONE NUMBER\n550 Main Street\n"
               "Cincinnati, OH 45202\nNAME ANO TITLE OF INOIVIOUAL TO WHOM REPORT ISSUED\n"
               "John E. Roe, Pharmacist-in-Charge\nFIRMNANE\nSTREET ADORESS\nTYPE ESTABUSHMEITT INSPECTED\n"
               "Nicholasville, KY 40356-8512\nProducer of Sterile and Non-Sterile Drugs\nFor example:\n•\n"
               "On 02/14/2022 your technician placed an opened bag of stoppers on the bench top under the light panel.\n")
        out = _joined(seg, "Producer of Sterile and Non Sterile Drug Products")
        self.assertTrue(out.endswith("placed an opened bag of stoppers on the bench top under the light panel."), out)
        for junk in ("Doe", "Roe", "Cincinnati", "Producer of", "Nicholasville"):
            self.assertNotIn(junk, out)

    def test_form_notice_after_footer_is_not_joined(self):
        # fda483-195550 obs5: 마지막 쪽 서명란 뒤는 양식 뒷면 고지 — 관찰은 거기서 끝났다.
        seg = ("did not include an effectiveness check for evaluation of this change.\n"
               "EMPLOYEE(S) SIGNATURE\nSEE REVERSE Jane T Doe, Investigator\nOF THIS PAGE\n"
               "FORM FDA 483 (09/08) PREVIOUS EDITION OBSOLETE\nINSPECTIONAL OBSERVATIONS\nPAGE 4 of 4 PAGES\n"
               "The observations of objectionable conditions and practices listed on the front of this form\n"
               "are reported:\n")
        self.assertEqual(c._join_footer_pages(seg), seg)
        self.assertEqual(_joined(seg), "did not include an effectiveness check for evaluation of this change.")

    def test_capitalized_sentence_after_footer_is_not_joined(self):
        # fda483-90268: 서명란 뒤 대문자 새 문장은 앵커를 잃은 다음 관찰 표제일 수 있다 — 붙이지 않는다.
        seg = ("Specifically, the complaint was not investigated\n"
               "EMPLOYEE(S) SIGNATURE\nSEE REVERSE\nJane S Doe, Investigator\nOF THIS PAGE\nDATE ISSUED\n"
               "Compliant records are deficient in that they do not include the findings of the investigation.\n")
        self.assertEqual(c._join_footer_pages(seg), seg)

    def test_single_marker_in_prose_is_not_a_page_block(self):
        seg = ("Specifically, the sample was reviewed by Jane Doe, Investigator\n"
               "and the results were not documented in the laboratory notebook as required.\n")
        self.assertEqual(c._join_footer_pages(seg), seg)

    def test_full_extraction_extends_only_the_detail(self):
        text = ("DURING AN INSPECTION OF YOUR FIRM I OBSERVED:\nOBSERVATION 1\n"
                "Procedures designed to prevent microbiological contamination are not established.\n"
                "Specifically, the documentation of the <Redacted B4> check of the\n"
                "EMPLOYEE(S) SIGNATURE\nDATE ISSUED\nJane Q. Doe, CSO\nSEE REVERSE\n03/02/2016\nOF THIS PAGE\n"
                "FORM FDA 483 (09/08)\nINSPECTIONAL OBSERVATIONS\nPAGE 2 OF 8 PAGES\n3012038236\nAustin, TX\n"
                "pressure differential only documents \"pass\" or \"fail\" for the reading.\n"
                "OBSERVATION 2\nClothing of personnel is not appropriate for the duties they perform.\n"
                "Specifically, the gowns worn in the ISO 7 cleanroom were not sterile.\n")
        obs = c._extract_483_observations_from_text(text, {"establishment_type": "Producer of Sterile Drug Products"})
        self.assertEqual([o["number"] for o in obs], ["1", "2"])
        self.assertTrue(obs[0]["detail"].endswith('check of the pressure differential only documents "pass" or '
                                                  '"fail" for the reading.'), obs[0]["detail"])
        self.assertEqual(obs[1]["detail"], "Specifically, the gowns worn in the ISO 7 cleanroom were not sterile.")


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
