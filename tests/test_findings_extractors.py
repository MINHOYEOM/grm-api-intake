#!/usr/bin/env python3
"""FIND-1 M1d raw_signal -> findings extractor tests."""

from __future__ import annotations

import json
import os
import unittest

import findings_extractors as extractors
import grm_findings as gf


GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def _load_input(name: str) -> dict:
    with open(os.path.join(GOLDEN, f"{name}.input.json"), encoding="utf-8") as f:
        return json.load(f)


def _raw_signal(name: str) -> dict:
    fx = _load_input(name)
    return gf.raw_signal_from_row(fx["row"], fx["raw"])


class FindingsExtractorsTest(unittest.TestCase):
    def assertValidFindings(self, findings: list[dict]) -> None:
        self.assertTrue(findings)
        for finding in findings:
            self.assertEqual(gf.validate_finding(finding), [])

    def test_fda_483_observations_become_accepted_findings(self) -> None:
        raw_signal = _raw_signal("fda_483_observations")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "FDA")
        self.assertEqual(findings[0]["document_type"], "483")
        self.assertEqual(findings[0]["finding_text"], "There is a failure to thoroughly review any unexplained discrepancy.")
        self.assertEqual(findings[0]["category_code"], "deviation_capa")
        self.assertEqual(findings[0]["evidence_level"], "A")
        self.assertEqual(findings[0]["finding_language"], "EN")
        self.assertEqual(findings[0]["review_status"], "accepted")
        self.assertEqual(findings[0]["confidence"], 0.95)
        self.assertEqual(findings[0]["evidence_url"], "https://www.fda.gov/media/192439/download")

        again = extractors.findings_from_raw_signal(raw_signal)
        self.assertEqual(
            [f["finding_id"] for f in findings],
            [f["finding_id"] for f in again],
        )

    def test_mfds_gmp_deficiency_table_becomes_accepted_findings(self) -> None:
        raw_signal = _raw_signal("gmp_inspection_periodic")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 3)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "MFDS")
        self.assertEqual(findings[0]["document_type"], "gmp-inspection")
        self.assertEqual(findings[0]["finding_text"], "시설장비 기타 [별표1] 2.1호 제품 교차오염 방지 제조시설 운영할 것")
        self.assertEqual(findings[0]["category_code"], "contamination_control")
        self.assertEqual(findings[0]["mfds_refs"], ["[별표1] 2.1호"])
        self.assertEqual(findings[0]["evidence_level"], "A")
        self.assertEqual(findings[0]["review_status"], "accepted")
        self.assertEqual(findings[0]["confidence"], 0.90)
        self.assertEqual(findings[0]["evidence_url"], "https://nedrug.mfds.go.kr/cmn/edms/down/1PyJBfLEtwC")

    def test_mfds_gmp_excerpt_fallback_enters_needs_review_queue(self) -> None:
        raw_signal = _raw_signal("gmp_inspection_biologic")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["category_code"], "aseptic_sterility_assurance")
        self.assertEqual(findings[0]["evidence_level"], "B")
        self.assertEqual(findings[0]["finding_language"], "KO")
        self.assertEqual(findings[0]["review_status"], "needs_review")
        self.assertEqual(findings[0]["confidence"], 0.72)

    def test_warning_letter_excerpt_enters_needs_review_queue(self) -> None:
        # [FIND-1 M11] 이 픽스처(Acme, 단일 문단·번호/헤딩 앵커 없음)는 새 WL 분해 로직의
        # degrade 경로(앵커 전무 -> 통짜 1건, 기존과 동일)도 함께 검증한다.
        raw_signal = _raw_signal("warning_letter_excerpt")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "FDA")
        # v3 taxonomy: "written procedures for production and process controls" now
        # classifies as process_validation, not documentation_records -- the audit-
        # identified "written procedure" catch-all keyword was removed in v3 (see
        # archive/findings_classification_audit_2026-07-12.md case 3df6f81c, the same
        # 211.100(a) CFR phrasing this fixture models).
        self.assertEqual(findings[0]["category_code"], "process_validation")
        self.assertEqual(findings[0]["evidence_level"], "B")
        self.assertEqual(findings[0]["finding_language"], "EN")
        self.assertEqual(findings[0]["review_status"], "needs_review")
        self.assertEqual(findings[0]["confidence"], 0.72)
        self.assertEqual(
            findings[0]["evidence_url"],
            "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/acme-660124",
        )

    def _wl_findings(self, raw_overrides: dict, row_overrides: dict | None = None) -> list[dict]:
        fx = _load_input("warning_letter_excerpt")
        raw = dict(fx["raw"])
        raw.update(raw_overrides)
        row = dict(fx["row"])
        if row_overrides:
            row.update(row_overrides)
        raw_signal = gf.raw_signal_from_row(row, raw)
        return extractors.findings_from_raw_signal(raw_signal)

    def test_warning_letter_numbered_list_decomposes_into_one_finding_per_violation(self) -> None:
        # [FIND-1 M11] "1. ... 2. ..." 번호 리스트는 위반 1건당 finding 1건으로 분해되고,
        # 각 블록의 법조항만 그 블록의 cfr_refs 에 들어간다(교차오염 없음).
        body = (
            "During our inspection, our investigators observed specific violations "
            "including, but not limited to, the following. 1. Your firm failed to "
            "establish written procedures for cleaning and maintenance of equipment "
            "(21 CFR 211.67). Investigators observed residue on shared equipment "
            "surfaces after cleaning was documented as complete. 2. Your firm failed "
            "to thoroughly investigate a customer complaint related to product "
            "contamination (21 CFR 211.198). The complaint file did not include a "
            "documented root cause or corrective action."
        )

        findings = self._wl_findings({"wl_body_excerpt": body, "wl_body_full": ""})

        self.assertEqual(len(findings), 2)
        self.assertValidFindings(findings)
        self.assertNotIn("the following", findings[0]["finding_text"])  # 서두(preamble) 절단
        self.assertTrue(findings[0]["finding_text"].startswith("Your firm failed to establish"))
        self.assertEqual(findings[0]["cfr_refs"], ["21 CFR 211.67"])
        self.assertTrue(findings[1]["finding_text"].startswith("Your firm failed to thoroughly investigate"))
        self.assertEqual(findings[1]["cfr_refs"], ["21 CFR 211.198"])
        self.assertEqual([f["evidence_level"] for f in findings], ["B", "B"])
        self.assertEqual([f["review_status"] for f in findings], ["needs_review", "needs_review"])

    def test_warning_letter_section_headings_decompose_and_drop_footer(self) -> None:
        # [FIND-1 M11] LyfeUnit 실측 스타일(섹션 헤딩형) 축약 픽스처. 헤딩("... Violations")
        # 으로 분해되고, 서두("FDA Review Violations were identified..." -- 뒤에 소문자
        # "were"가 와서 진짜 헤딩이 아님)와 푸터(Conclusion/Sincerely/...)는 반드시 빠진다.
        body = (
            "FDA Review Violations were identified during a review of your website. "
            "Unapproved New Drug Violations Certain products offered for sale are "
            "unapproved new drugs under section 201(g) of the FD&C Act. You failed to "
            "obtain approved applications for these products. "
            "Misbranded Drug Violations Your product labeling fails to bear adequate "
            "directions for use under section 502(f)(1) of the FD&C Act. This causes "
            "the product to be misbranded. "
            "Conclusion Send your written response to FDA within 15 business days. "
            "Sincerely, /S/ Jane Doe Director Content current as of: 07/07/2026 "
            "Regulated Product(s) Drugs"
        )

        findings = self._wl_findings({"wl_body_full": body, "wl_body_excerpt": ""})

        self.assertEqual(len(findings), 2)
        self.assertValidFindings(findings)
        for finding in findings:
            text = finding["finding_text"]
            self.assertNotIn("Conclusion", text)
            self.assertNotIn("Sincerely", text)
            self.assertNotIn("Regulated Product", text)
            self.assertNotIn("FDA Review Violations", text)
        self.assertTrue(findings[0]["finding_text"].startswith("Certain products offered for sale"))
        self.assertEqual(findings[0]["cfr_refs"], ["section 201(g)"])
        self.assertTrue(findings[1]["finding_text"].startswith("Your product labeling fails"))
        self.assertEqual(findings[1]["cfr_refs"], ["section 502(f)(1)"])

    def test_warning_letter_single_narrative_degrades_to_one_finding(self) -> None:
        # [FIND-1 M11] YHC 실측 스타일: 번호 리스트도 헤딩도 없는 단일 위반 서술. 본문 중
        # "Refusal to Provide Records"(Title Case 이지만 "Violations" 로 끝나지 않음 + 뒤에
        # 오는 단어가 대문자로 시작하는 새 문장 첫 단어라 해도)가 헤딩으로 오탐되지 않는지가
        # 핵심 -- degrade 로 통짜 1건, 본문 그대로 유지돼야 한다.
        body = (
            "This Warning Letter advises you of significant violations of the FD&C "
            "Act for XYZ Pharma, FEI 1234567, located at Some Address. Refusal to "
            "Provide Records According to FDA records, your firm initially registered "
            "as a drug manufacturer in 2016."
        )

        findings = self._wl_findings({"wl_body_excerpt": body, "wl_body_full": ""})

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["finding_text"], body)

    def test_warning_letter_short_truncated_final_block_is_dropped(self) -> None:
        # [FIND-1 M11] excerpt 절단으로 마지막 번호 항목이 문장 중간에서 짧게 끊기면
        # (문장부호 없음 + <40자) 미완결 조각으로 버려진다 -- 빈 결과가 되진 않는다(item 1은
        # 유효하므로 그대로 남는다).
        body = (
            "During inspection we found issues. 1. Your firm failed to maintain "
            "equipment logs as required by 21 CFR 211.68. The logs were incomplete "
            "for several batches inspected in detail. 2. Your firm failed to invest"
        )

        findings = self._wl_findings({"wl_body_excerpt": body, "wl_body_full": ""})

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertTrue(findings[0]["finding_text"].startswith("Your firm failed to maintain"))
        self.assertEqual(findings[0]["cfr_refs"], ["21 CFR 211.68"])
        self.assertNotIn("invest", findings[0]["finding_text"])

    def test_warning_letter_lowercase_conclusion_prose_is_not_cut_as_footer(self) -> None:
        # [FIND-1 M11 회귀] 위반 본문 산문의 소문자 "at the conclusion of the inspection"
        # (Genzyme 실측)을 편지 푸터 "Conclusion" 으로 오인해 뒷부분+조항을 잘라내던 버그 방어.
        # 푸터 마커는 대소문자 구분(Title-Case 제목형만) 이므로 소문자 산문은 보존돼야 하고,
        # 그 뒤의 위반 서술과 21 CFR 211.22 조항이 살아 있어야 한다.
        body = (
            "During the inspection, FDA documented significant CGMP violations. At the "
            "conclusion of the inspection, FDA investigators issued a Form FDA-483. "
            "Your firm's quality control unit failed to exercise its responsibility as "
            "required by 21 CFR 211.22 for the drug products manufactured at your site."
        )

        findings = self._wl_findings({"wl_body_excerpt": body, "wl_body_full": ""})

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["finding_text"], body)  # 산문 소문자 conclusion 보존
        self.assertIn("21 CFR 211.22", findings[0]["cfr_refs"])  # 잘리지 않아 조항 살아있음

    def test_extract_us_legal_refs_covers_cfr_usc_and_fdc_sections(self) -> None:
        # [FIND-1 M11] 21 CFR(범위 전개 포함) / 21 U.S.C.(§ 유무) / FD&C section(콤마+and 목록
        # 전개) 3계열을 모두 잡고 정규화·dedup 한다.
        self.assertEqual(
            extractors._extract_us_legal_refs("21 CFR 211.194(a)"),
            ["21 CFR 211.194(a)"],
        )
        self.assertEqual(
            extractors._extract_us_legal_refs("21 CFR parts 210 and 211"),
            ["21 CFR 210", "21 CFR 211"],
        )
        self.assertEqual(
            extractors._extract_us_legal_refs("21 U.S.C. § 351(a)(2)(B)"),
            ["21 U.S.C. § 351(a)(2)(B)"],
        )
        self.assertEqual(
            extractors._extract_us_legal_refs("sections 301(a), 301(d)"),
            ["section 301(a)", "section 301(d)"],
        )

    def test_who_whopir_excerpt_enters_needs_review_queue(self) -> None:
        raw_signal = _raw_signal("who_inspection_excerpt")

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        self.assertEqual(findings[0]["agency"], "WHO")
        self.assertEqual(findings[0]["category_code"], "deviation_capa")
        self.assertEqual(findings[0]["evidence_level"], "B")
        self.assertEqual(findings[0]["review_status"], "needs_review")
        self.assertEqual(findings[0]["confidence"], 0.72)

    def test_invalid_raw_signal_or_empty_payload_returns_no_findings(self) -> None:
        self.assertEqual(extractors.findings_from_raw_signal({}), [])

        fx = _load_input("warning_letter_excerpt")
        raw_signal = gf.raw_signal_from_row(fx["row"], {})
        self.assertEqual(extractors.findings_from_raw_signal(raw_signal), [])

    def test_duplicate_finding_texts_are_deduped(self) -> None:
        fx = _load_input("fda_483_observations")
        fx["raw"]["fda_483_observations"].append(dict(fx["raw"]["fda_483_observations"][0]))
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertEqual(len({f["finding_text"] for f in findings}), 2)

    def test_with_report_clean_extraction_has_zero_drops(self) -> None:
        raw_signal = _raw_signal("fda_483_observations")

        findings, report = extractors.findings_from_raw_signal_with_report(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            report,
            {
                "extracted": 2,
                "kept": 2,
                "dropped_invalid": 0,
                "dropped_duplicate_text": 0,
                "invalid_errors": [],
            },
        )

    def test_with_report_counts_invalid_drop_when_evidence_url_is_missing(self) -> None:
        fx = _load_input("warning_letter_excerpt")
        fx["row"] = dict(fx["row"])
        fx["row"].pop("official_url", None)
        fx["row"].pop("source_url", None)
        fx["row"].pop("api_query", None)
        fx["raw"] = dict(fx["raw"])
        fx["raw"].pop("url", None)
        fx["raw"].pop("source_url", None)
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
        self.assertEqual(gf.validate_raw_signal(raw_signal), [])

        findings, report = extractors.findings_from_raw_signal_with_report(raw_signal)

        self.assertEqual(findings, [])
        self.assertEqual(report["extracted"], 1)
        self.assertEqual(report["kept"], 0)
        self.assertEqual(report["dropped_invalid"], 1)
        self.assertEqual(report["dropped_duplicate_text"], 0)
        self.assertEqual(report["invalid_errors"], ["findings.evidence_url required"])

        # Unchanged public function still returns an empty list for this case.
        self.assertEqual(extractors.findings_from_raw_signal(raw_signal), [])

    def test_fda_483_page_header_contamination_is_scrubbed_on_reextraction(self) -> None:
        # FIND-1 M10a — 저장된 raw 가 이미 오염된 채로 있어도(백필 경로) 페이지 넘김 헤더
        # 라벨-값 인터리브가 여기서 제거되고, finding_id 는 깨끗한 텍스트 기준으로 계산된다.
        fx = _load_input("fda_483_observations")
        raw = dict(fx["raw"])
        raw["establishment_type"] = "Producer of Sterile Drug Products"
        raw["fei_number"] = "2071629"
        raw["firm"] = "VA San Diego Healthcare Systems"
        raw["fda_483_observations"] = [
            {
                "number": "1",
                "deficiency": (
                    "STREET ADDRESS 4/27/26-5/1/26, 5/4/26-5/6/26, 5/8/26 FEI NUMBER 2071629 "
                    "3350 La Jolla Village Dr TYPE OF ESTABLISHMENT INSPECTED Producer of "
                    "Sterile Drug Products Personnel engaged in aseptic processing were "
                    "observed wearing non-sterile gloves."
                ),
                "detail": "",
            }
        ]
        row = dict(fx["row"])
        row["firm"] = ""  # firm_name 힌트가 raw.firm 폴백 경로를 타도록 row 쪽은 비움
        raw_signal = gf.raw_signal_from_row(row, raw)
        # raw_signal_from_row 는 raw.firm 도 firm_name 후보로 보므로 여기선 그대로 채워짐 —
        # extractor 는 raw_signal.get("firm_name") 우선, 없으면 raw.get("firm") 폴백을 쓴다.

        findings = extractors.findings_from_raw_signal(raw_signal)

        self.assertEqual(len(findings), 1)
        self.assertValidFindings(findings)
        clean_text = "Personnel engaged in aseptic processing were observed wearing non-sterile gloves."
        self.assertEqual(findings[0]["finding_text"], clean_text)

        expected = gf.finding_from_raw_signal(
            raw_signal,
            finding_text=clean_text,
            ordinal=1,
            evidence_level="A",
            evidence_url=findings[0]["evidence_url"],
            finding_language="EN",
            confidence=0.95,
            review_status="accepted",
        )
        self.assertEqual(findings[0]["finding_id"], expected["finding_id"])

    def test_with_report_counts_duplicate_text_drop(self) -> None:
        fx = _load_input("fda_483_observations")
        fx["raw"]["fda_483_observations"].append(dict(fx["raw"]["fda_483_observations"][0]))
        raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])

        findings, report = extractors.findings_from_raw_signal_with_report(raw_signal)

        self.assertEqual(len(findings), 2)
        self.assertEqual(report["extracted"], 3)
        self.assertEqual(report["kept"], 2)
        self.assertEqual(report["dropped_invalid"], 0)
        self.assertEqual(report["dropped_duplicate_text"], 1)
        self.assertEqual(report["invalid_errors"], [])


if __name__ == "__main__":
    unittest.main()
