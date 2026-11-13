#!/usr/bin/env python3
"""483 단독 조각 표제 대체 (2026-11-13) — 손 SQL 로 넣은 행을 추출기가 그대로 재현하는가.

11-13 비서실이 대표 승인으로 FDA 483 조각 표제('!' · 'A.' · 'Specifically, 1.' · 'There is a.')
관찰을 손 SQL 로 고쳤다(B 43행 + 복원 2행). 근거:
  _ops/reports/GRM_483단독조각_적용_sql_2026-11-13.sql · GRM_483단독조각_적용안_2026-11-13.md
  _ops/reports/GRM_483복원2행_단독조각_sql_2026-11-13.sql · GRM_483복원2행_단독조각_dry_2026-11-13.md
같은 규칙이 추출기에 있어야 다음 수집·재추출이 같은 finding_id 를 내고(중복 행 0) 손 입력이 필요 없다.

픽스처 tests/fixtures/fda483_solo_fragment_retitle_20261113.json 은 운영 raw_signals 의 해당 관찰 하나와
운영 findings 행의 (finding_id, finding_text, md5(detail_text))다. raw_signal_id 는 운영 값 그대로라
finding_id 를 바로 맞댈 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import findings_extractors as extractors  # noqa: E402
import grm_findings as gf  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "fda483_solo_fragment_retitle_20261113.json")


def _load() -> dict:
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _signal(case: dict, observations: list[dict] | None = None) -> dict:
    row = {
        "date": "2026-05-27",
        "document_id": case["document_id"],
        "firm": case["firm_name"] or "Example Firm",
        "headline": "[FDA 483] Example Firm",
        "language": "EN",
        "modality": "Chemical",
        "official_url": "https://www.fda.gov/media/000000/download",
        "source": "FDA 483",
        "type_or_class": "483",
    }
    raw = {
        "channel": "fda-483",
        "firm": case["raw_firm"],
        "establishment_type": case["establishment_type"],
        "fei_number": case["fei_number"],
        "fda_483_observations": observations if observations is not None else [case["observation"]],
    }
    signal = gf.raw_signal_from_row(row, raw)
    signal["raw_signal_id"] = case["raw_signal_id"]  # 운영 id — finding_id 해시 입력
    signal["firm_name"] = case["firm_name"]
    return signal


def _md5(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


class ManualRowsReproducedTest(unittest.TestCase):
    """B 43행 + 복원 2행(793062c2 · 8c091af9) — 같은 finding_id · 표제 · detail_text."""

    def test_all_45_rows(self) -> None:
        cases = _load()["reproduce"]
        self.assertEqual(len(cases), 45)
        ids = {c["expected"]["finding_id"] for c in cases}
        self.assertIn("finding-793062c2ac43eeb56e3edce5", ids)
        self.assertIn("finding-8c091af986f5274282ea8483", ids)
        for case in cases:
            exp = case["expected"]
            with self.subTest(doc=case["document_id"], number=case["observation"]["number"]):
                details = extractors.fda_483_observation_details(_signal(case))
                self.assertEqual(len(details), 1)
                got = details[0]
                self.assertEqual(got["finding_text"], exp["finding_text"])
                self.assertEqual(got["finding_id"], exp["finding_id"])
                self.assertEqual(_md5(got["detail_text"]), exp["detail_md5"])
                self.assertEqual(len(got["detail_text"] or ""), exp["detail_len"])
                findings = extractors.findings_from_raw_signal(_signal(case))
                self.assertEqual([f["finding_id"] for f in findings], [exp["finding_id"]])

    def test_title_plus_detail_is_whole_stripped_detail(self) -> None:
        # 저장 단계에서 자르지 않는다 — 표제 + 나머지 상세가 스트립한 원 상세 전부다.
        for case in _load()["reproduce"]:
            obs = case["observation"]
            with self.subTest(doc=case["document_id"], number=obs["number"]):
                got = extractors.fda_483_observation_details(_signal(case))[0]
                whole = extractors._fda483_solo_strip(obs["detail"]).replace(" ", "")
                joined = (got["finding_text"] + got["detail_text"]).replace(" ", "")
                # ① 표제+상세 == 스트립한 상세 전부. ④ 는 그 앞에 끊긴 원 표제 머리가 붙는다.
                self.assertTrue(joined.endswith(whole))
                self.assertLess(len(joined) - len(whole), len(obs["deficiency"]))


class NotRetitledTest(unittest.TestCase):
    """대체하면 안 되는 단독 짧은 표제 — 종전 출력 그대로(표제 = 원 표제)."""

    def test_guarded_observations_keep_original_title(self) -> None:
        cases = _load()["unchanged"]
        self.assertEqual(len(cases), 8)
        for case in cases:
            obs = case["observation"]
            with self.subTest(doc=case["document_id"], number=obs["number"], deficiency=obs["deficiency"]):
                details = extractors.fda_483_observation_details(_signal(case))
                self.assertEqual(len(details), 1)
                self.assertEqual(details[0]["finding_text"], extractors._compact(obs["deficiency"]))

    def test_number_with_real_title_is_left_alone(self) -> None:
        # 같은 번호에 정상 표제가 있으면 상호참조 하위 항목 — 대체하지 않는다(행도 지우지 않는다).
        case = next(c for c in _load()["reproduce"] if c["observation"]["deficiency"] == "!")
        number = case["observation"]["number"]
        real = {"number": number,
                "deficiency": "Laboratory controls do not include scientifically sound test procedures.",
                "detail": "Specifically, the HPLC method was not validated."}
        details = extractors.fda_483_observation_details(_signal(case, [real, case["observation"]]))
        self.assertEqual([d["finding_text"] for d in details],
                         [real["deficiency"], extractors._compact(case["observation"]["deficiency"])])


class RuleUnitTest(unittest.TestCase):
    def test_short_complete_title_is_not_joined(self) -> None:
        # 11-13 ② — "Risk analysis is inadequate." 는 표제 유지가 결정이었다.
        self.assertIsNone(extractors._fda483_solo_retitle(
            "Risk analysis is inadequate.", "your firm's risk analysis did not include use errors. Specifically, x."))

    def test_cut_title_joined_and_no_abbrev_kept(self) -> None:
        self.assertEqual(
            extractors._fda483_solo_retitle("ISO 5 areas were.", "not certified under dynamic conditions. Specifically, y."),
            ("ISO 5 areas were not certified under dynamic conditions.", "Specifically, y."))
        title, _ = extractors._fda483_solo_retitle(
            "Your analytical method no.", "632 is inadequate in that it does not detect impurities. Next.")
        self.assertTrue(title.startswith("Your analytical method no. 632 is inadequate"))

    def test_glued_sentence_boundary(self) -> None:
        # 마침표 뒤 공백 없이 대문자로 붙은 반복 문단(83772 · 83807 꼴).
        title, rest = extractors._fda483_solo_retitle(
            ".", ".. You did not perform environmental testing for SE after molting.You did not perform it again.")
        self.assertEqual(title, "You did not perform environmental testing for SE after molting.")
        self.assertEqual(rest, "You did not perform it again.")

    def test_single_letter_lead_stripped(self) -> None:
        # 90268 #4 꼴 — `I ' Laboratory …` 의 한 글자 머리.
        title, _ = extractors._fda483_solo_retitle(
            "!", "I ' Laboratory controls do not include scientifically sound procedures. Specifically, z.")
        self.assertEqual(title, "Laboratory controls do not include scientifically sound procedures.")

    def test_two_identical_fragments_still_use_1614_path(self) -> None:
        # 같은 조각 표제가 2번 이상이면 #1614 경로(머리 스트립 4단) 그대로 — 이미 나간 id 를 지킨다.
        case = _load()["reproduce"][0]
        obs = {"number": "1", "deficiency": ".", "detail": "I ' Laboratory controls do not include sound procedures. Next."}
        obs2 = {"number": "2", "deficiency": ".", "detail": "I ' Written procedures for cleaning are not followed. Next."}
        texts = [d["finding_text"] for d in extractors.fda_483_observation_details(_signal(case, [obs, obs2]))]
        self.assertEqual(texts, ["I ' Laboratory controls do not include sound procedures.",
                                 "I ' Written procedures for cleaning are not followed."])


if __name__ == "__main__":
    unittest.main()
