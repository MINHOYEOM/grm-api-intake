#!/usr/bin/env python3
"""[483 추출 수리 2026-11-12] 주석란 잡문 가드 · 조각 표제 대체 · 관찰 단위 상세 짝.

픽스처는 운영 raw_signals.raw_json.fda_483_observations 실측 조각을 줄인 것이다(문서 id 는 실측
그대로, 상세는 앞 1~2문장만). 근거: _ops/reports/GRM_483추출수리_dry_2026-11-12.md §2·§3·§4-1.
"""

from __future__ import annotations

import hashlib
import unittest

import findings_extractors as extractors
import grm_findings as gf


def _signal(document_id: str, observations: list[dict]) -> dict:
    row = {
        "date": "2026-05-27",
        "document_id": document_id,
        "firm": "Example Firm",
        "headline": "[FDA 483] Example Firm",
        "language": "EN",
        "modality": "Chemical",
        "official_url": "https://www.fda.gov/media/000000/download",
        "source": "FDA 483",
        "type_or_class": "483",
    }
    raw = {"channel": "fda-483", "firm": "Example Firm", "fda_483_observations": observations}
    return gf.raw_signal_from_row(row, raw)


def _obs(number: str, deficiency: str, detail: str = "") -> dict:
    return {"number": number, "deficiency": deficiency, "detail": detail}


def _texts(findings: list[dict]) -> list[str]:
    return [f["finding_text"] for f in findings]


def _expected_id(signal: dict, text: str, ordinal: int) -> str:
    return gf.finding_from_raw_signal(signal, finding_text=text, ordinal=ordinal)["finding_id"]


# fda483-151431 — 관찰 3·8·10 의 표제가 "." 로 잘렸다(줄머리 `(REPEAT OBSERVATION)`·`. `·`... `).
_151431 = [
    _obs("1", "(REPEAT OBSERVATION) There is a failure to thoroughly review any unexplained discrepancy.",
         "Specifically, A) During the review of Adverse Event Investigation ADE2020081."),
    _obs("3", ".",
         "(REPEAT OBSERVATION) Procedures designed to prevent microbiological contamination of drug "
         "products purporting to 'be sterile are not established: Specifically, A) The media fill "
         "performed does not represent the number and complexity of routine and non routine "
         "interventions. B) A procedure is not established to determine the number of open vials."),
    _obs("8", ".",
         ". Your outsourcing facility compounds drug product that have been withdrawn or removed from "
         "the market because such drugs or components of such drugs have been found to be unsafe or "
         "not effective. Specifically, Your firm compounded the following drug products."),
    _obs("9", "l , .",
         ".. You compound drugs that are essentially a copy of one or more approved drugs within the "
         "meaning of sections 503B(a)(5) and S03B(d)(2). Specifically, You compound drug products."),
    _obs("10", ".",
         "... Records are not maintained so that data therein can be reviewed [redacted] to evaluate "
         "tbe quality standards of each drug product to determine tine need for changes in "
         "specifications or manufacturing or control procedures. Specifically. The firm has not "
         "generated [redacted] product reviews."),
    _obs("11", "Your outsourcing facility did not submit a report to FDA identifying the drugs compounded.",
         ". Specifically, the following product was compounded and not identified on your report."),
]

# fda483-70711 — 관찰 3·4 표제 "." , 관찰 3 상세는 열거자 `A.` 로 시작.
_70711 = [
    _obs("2", "Drug product failing to meet established standards, specifications, and quality control criteria are not rejected.",
         "Samples of drug products were routinely resampled."),
    _obs("3", ".",
         "A. Annual Reports for ANDA’s that were submitted to FDA did not include out of "
         "specification (OOS) result. Only passing data points were submitted."),
    _obs("4", ".",
         "An NDA-field alert report was not submitted within three working days of receipt of "
         "information concerning a failure of one or more distributed batches of a drug to meet the "
         "specifications established for it in the application. Field Alerts were not routinely filed."),
]

# fda483-85045 — 실사 관찰 뒤에 주석란 "Promised to correct." 가 번호 1~11 로 다시 잘려 들어왔다.
_85045 = [
    _obs("5", "Written MDR procedures have not been implemented.",
         "Specifically, since your revised Medical Device Reporting procedure became effective."),
    _obs("8", "Procedures for design validation have not been adequately established.",
         "Specifically, your design validation activities are inadequate in that:"),
    _obs("1", "Promised to correct."),
    _obs("2", "Promised to correct."),
    _obs("5", ".Promised to correct."),
    _obs("9", "Promised to correct.", "Observation J0: Promised to correct."),
    _obs("11", "Promised to correct.",
         "*DATES OF INSPECTION: 10/29/2012(Mon), 10 /30/2012(Tue), 10/3 1/2012(Wed)"),
]

# fda483-193490 — 관찰 1 본문의 "Please refer to OBSERVATION N" 에서 잘린 하위 항목(가짜 조각).
_193490 = [
    _obs("1", "The responsibilities and procedures applicable to the quality control unit are not fully followed.",
         "Specifically, lA. Your Quality Unit failed to: a. Ensure the timely implementation of CAPA."),
    _obs("1", ".", "b. Conduct adequate root-cause analyses for the ongoing Environmental Monitoring (EM) excursions."),
    _obs("3", ".", "c Investigate the root cause of visual inspection failure during initial 100% inspection."),
    _obs("4", ".", "d. Evaluate air flow patterns under dynamic conditions in all ISO 5 classified BSC cabinets."),
    _obs("3", "Production personnel were not practicing good sanitation and health habits.",
         "Specifically, Your firm failed to establish and maintain adequate controls."),
    _obs("4", "There is a failure to thoroughly review any unexplained discrepancy and the failure of a batch.",
         "Specifically, Your firm failed to conduct, or document, an investigation."),
]

# fda483-88118 — 관찰 4·5 가 같은 표제(같은 제목 병합 · 대표 결정 전이라 현행 1행 유지).
_88118_TITLE = ("Written production and process control procedures are not followed in the execution "
                "of production and process control functions.")
_88118 = [
    _obs("3", "The quality control unit lacks authority to review production records to assure that no "
              "errors have occurred and fully investigate errors that have occurred.",
         "Specifically, You failed to implement adequate corrective and preventive actions."),
    _obs("4", _88118_TITLE,
         "Specifically, a. You failed to follow your SOP, \"Proper Documentation\", QLA-MQA-00603."),
    _obs("5", _88118_TITLE,
         "Inventory Control System REDACTED [redacted] is not reliable or accurate."),
]


class Fda483LostTitleTest(unittest.TestCase):
    def test_151431_dot_titles_become_three_distinct_rows(self) -> None:
        signal = _signal("fda483-151431", _151431)
        findings = extractors.findings_from_raw_signal(signal)
        texts = _texts(findings)

        self.assertNotIn(".", texts)
        self.assertIn(
            "Procedures designed to prevent microbiological contamination of drug products purporting "
            "to 'be sterile are not established: Specifically, A) The media fill performed does not "
            "represent the number and complexity of routine and non routine interventions.", texts)
        self.assertIn(
            "Your outsourcing facility compounds drug product that have been withdrawn or removed from "
            "the market because such drugs or components of such drugs have been found to be unsafe or "
            "not effective.", texts)
        self.assertIn(
            "Records are not maintained so that data therein can be reviewed [redacted] to evaluate tbe "
            "quality standards of each drug product to determine tine need for changes in specifications "
            "or manufacturing or control procedures.", texts)
        # 한 번만 나오는 조각 표제(관찰 9 'l , .')는 단독 조각 대체(2026-11-13)로 상세 첫 문장이 표제다
        # — 11-13 손 SQL 로 넣은 운영 행(finding-8044e15c…)과 같은 표제.
        self.assertNotIn("l , .", texts)
        self.assertIn(
            "You compound drugs that are essentially a copy of one or more approved drugs within the "
            "meaning of sections 503B(a)(5) and S03B(d)(2).", texts)
        self.assertEqual(len(findings), 6)

    def test_retitled_rows_keep_finding_id_rule_and_observation_number(self) -> None:
        signal = _signal("fda483-151431", _151431)
        by_text = {f["finding_text"]: f for f in extractors.findings_from_raw_signal(signal)}
        pairs = {p["finding_id"]: p for p in extractors.fda_483_observation_details(signal)}
        expected_ordinals = {"Procedures designed": 3, "Your outsourcing facility compounds": 8,
                             "Records are not maintained": 10}
        for head, ordinal in expected_ordinals.items():
            text = next(t for t in by_text if t.startswith(head))
            self.assertEqual(by_text[text]["finding_id"], _expected_id(signal, text, ordinal))
            self.assertEqual(pairs[by_text[text]["finding_id"]]["ordinal"], ordinal)
        # 정상 표제 행의 id 는 종전과 같다(규칙 불변).
        normal = _151431[0]["deficiency"]
        self.assertEqual(by_text[normal]["finding_id"], _expected_id(signal, normal, 1))

    def test_retitled_rows_fit_the_storage_unique_index(self) -> None:
        # findings 유일 인덱스 = (raw_signal_id, md5(finding_text)). 되살린 행끼리·기존 행과 겹치지 않아야
        # append 경로에서 409(중복)로 조용히 버려지지 않는다.
        for name, observations in (("fda483-151431", _151431), ("fda483-70711", _70711)):
            findings = extractors.findings_from_raw_signal(_signal(name, observations))
            keys = {(f["raw_signal_id"], hashlib.md5(f["finding_text"].encode("utf-8")).hexdigest())
                    for f in findings}
            self.assertEqual(len(keys), len(findings), name)

    def test_70711_enumerator_is_stripped_before_first_sentence(self) -> None:
        signal = _signal("fda483-70711", _70711)
        texts = _texts(extractors.findings_from_raw_signal(signal))
        self.assertIn("Annual Reports for ANDA’s that were submitted to FDA did not include out of "
                      "specification (OOS) result.", texts)
        self.assertTrue(any(t.startswith("An NDA-field alert report was not submitted") for t in texts))
        self.assertNotIn("A.", texts)
        self.assertNotIn(".", texts)
        self.assertEqual(len(texts), 3)

    def test_retitled_detail_is_the_rest_of_the_original_detail(self) -> None:
        signal = _signal("fda483-70711", _70711)
        pairs = {p["ordinal"]: p for p in extractors.fda_483_observation_details(signal)}
        self.assertEqual(pairs[3]["detail_text"], "Only passing data points were submitted.")
        self.assertEqual(pairs[4]["detail_text"], "Field Alerts were not routinely filed.")
        self.assertEqual(pairs[2]["detail_text"], "Samples of drug products were routinely resampled.")

    def test_fake_fragments_with_titled_number_are_skipped(self) -> None:
        findings = extractors.findings_from_raw_signal(_signal("fda483-193490", _193490))
        texts = _texts(findings)
        self.assertNotIn(".", texts)
        self.assertEqual(len(findings), 3)

    def test_orphan_cross_reference_fragment_is_not_retitled(self) -> None:
        # 182460 관찰 3 "for details." 는 고아 번호지만 관찰 1 의 하위 항목 "c) CAPA…" 다 — 되살리지 않는다.
        observations = [
            _obs("1", "The responsibilities and procedures applicable to the quality control unit are not fully followed.",
                 "Specifically, a) Your firm is not documenting all reports of product complaints."),
            _obs("2", "for details.", "b) Your firm is not always identifying all root causes of deviations."),
            _obs("3", "for details.", "c) CAPA 22-0017 was created after the last inspection to address a 483 observation."),
            _obs("2", "Procedures describing the handling of all written and oral complaints are not followed.",
                 "Specifically, a) Your firm is not documenting and investigating all complaints received."),
        ]
        texts = _texts(extractors.findings_from_raw_signal(_signal("fda483-182460", observations)))
        self.assertEqual(texts.count("for details."), 1)
        self.assertFalse(any(t.startswith("CAPA 22-0017") for t in texts))
        self.assertEqual(len(texts), 3)


class Fda483AnnotationGuardTest(unittest.TestCase):
    def test_85045_promised_to_correct_rows_are_not_created(self) -> None:
        findings = extractors.findings_from_raw_signal(_signal("fda483-85045", _85045))
        self.assertEqual(_texts(findings), [
            "Written MDR procedures have not been implemented.",
            "Procedures for design validation have not been adequately established.",
        ])

    def test_other_annotation_phrases_are_not_created(self) -> None:
        observations = [
            _obs("1", "Written MDR procedures have not been implemented.", "Specifically, events were not reported."),
            _obs("7", "Not annotated *DATES OF INSPECTION 5/15/2017 (Mon), 5/16/2017 (Tue)",
                 "Pursuant to Section 704(b) of the Federal Food, Drug and Cosmetic Act."),
            _obs("8", "Not annotated SEE REVE.RSE OF THIS PAGE"),
            _obs("4", "on the FDA 483 dated 03 /07/2014 and"),
            _obs("4", "Reported corrected, not verified."),
            _obs("3", "response: Advanced Care Infusion will review and further proceduralize the processes.",
                 "Details will be included related to methods and frequency of cleaning."),
            _obs("6", ".", "*DATES OF INSPECTION: 10/14/2011(Fri), 10/17/2011(Mon)"),
            _obs("9", ".", ",. \\ *DATES OF INSPECTION: 06/ll/2014(Wed), 06/16/2014(Mon)"),
        ]
        texts = _texts(extractors.findings_from_raw_signal(_signal("fda483-000001", observations)))
        self.assertEqual(texts, ["Written MDR procedures have not been implemented."])

    def test_real_titles_with_footer_noise_are_kept(self) -> None:
        # 정상 표제 끝에 "AMENDMENT 1" 쪽 꼬리말이 붙거나, 상세가 실사일 꼬리말뿐인 진짜 관찰은 지우지 않는다.
        observations = [
            _obs("1", "Procedures designed to prevent microbiological contamination of drug products "
                      "purporting to be sterile are not established. AMENDMENT 1 EMPLOYEE(S) SIGNATURE"),
            _obs("2", "Backup data is not assured as exact, complete and secure from alteration, erasure or loss.",
                 "*DATES OF INSPECTION: 10/14/2011(Fri), 10/17/2011(Mon)"),
        ]
        findings = extractors.findings_from_raw_signal(_signal("fda483-000002", observations))
        self.assertEqual(len(findings), 2)


class Fda483SameTitleUnchangedTest(unittest.TestCase):
    def test_88118_same_title_observations_stay_one_row_with_existing_id(self) -> None:
        signal = _signal("fda483-88118", _88118)
        # 운영 DB 의 기존 행과 같은 raw_signal_id·finding_id 가 나와야 한다(규칙 불변 고정값).
        self.assertEqual(signal["raw_signal_id"], "rawsig-c3a90118bd29999b496d3c41")
        findings, report = extractors.findings_from_raw_signal_with_report(signal)
        same = [f for f in findings if f["finding_text"] == _88118_TITLE]
        self.assertEqual(len(same), 1)
        self.assertEqual(same[0]["finding_id"], "finding-a7a83a8957c7cca21b37a1c0")
        self.assertEqual(report["dropped_duplicate_text"], 1)
        self.assertEqual(len(findings), 2)

    def test_88118_kept_row_pairs_with_its_own_observation_detail(self) -> None:
        pairs = extractors.fda_483_observation_details(_signal("fda483-88118", _88118))
        pair = next(p for p in pairs if p["finding_id"] == "finding-a7a83a8957c7cca21b37a1c0")
        self.assertEqual(pair["ordinal"], 4)
        self.assertTrue(pair["detail_text"].startswith("Specifically, a. You failed to follow your SOP"))
        self.assertEqual(len(pairs), 2)

    def test_pairs_cover_exactly_the_kept_findings(self) -> None:
        for name, observations in (("fda483-151431", _151431), ("fda483-85045", _85045),
                                   ("fda483-193490", _193490), ("fda483-88118", _88118)):
            signal = _signal(name, observations)
            kept = [f["finding_id"] for f in extractors.findings_from_raw_signal(signal)]
            pairs = [p["finding_id"] for p in extractors.fda_483_observation_details(signal)]
            self.assertEqual(pairs, kept, name)

    def test_pairs_for_non_483_source_are_empty(self) -> None:
        row = {"date": "2026-05-27", "document_id": "wl-1", "firm": "X", "source": "FDA Warning Letter",
               "type_or_class": "WL", "official_url": "https://www.fda.gov/x"}
        signal = gf.raw_signal_from_row(row, {"letter_excerpt": "x"})
        self.assertEqual(extractors.fda_483_observation_details(signal), [])

    def test_non_483_dedup_is_unchanged(self) -> None:
        # 다른 소스는 종전 표제 키 dedup 그대로(이 PR 은 dedup 함수를 건드리지 않는다).
        base = gf.finding_from_raw_signal(
            _signal("fda483-000003", []), finding_text="Same text.", ordinal=1,
            evidence_url="https://www.fda.gov/x")
        other = dict(base, finding_id="finding-other", source="FDA Warning Letter")
        kept, report = extractors._dedupe_valid_findings_with_report([base, other])
        self.assertEqual(len(kept), 1)
        self.assertEqual(report["dropped_duplicate_text"], 1)


if __name__ == "__main__":
    unittest.main()
