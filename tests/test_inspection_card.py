"""[실사 결과 카드 2026-11-08] inspection_card · findings_ko 검증 · 제품군 거짓 친구.

근거 데이터 = 이미 발행된 호의 실제 카드(web/data/briefs). 발행본은 고치지 않는 기록물이라
고정 픽스처로 쓸 수 있다. 대표 지적 3장: Glukem(10-19)·NKI(10-12, 판정서 4건)·Xi'an(09-28).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import grm_taxonomy as tx  # noqa: E402
import inject_slots as inj  # noqa: E402
import inspection_card as ic  # noqa: E402

BRIEFS = os.path.join(ROOT, "web", "data", "briefs")


def _cards(date: str) -> list[dict]:
    with open(os.path.join(BRIEFS, f"brief_web_{date}.json"), encoding="utf-8") as f:
        return json.load(f)["cards"]


def _card(date: str, cid: str) -> dict:
    return copy.deepcopy(next(c for c in _cards(date) if c.get("id") == cid))


GLUKEM = ("2026_10_19", "188112")
XIAN = ("2026_09_28", "187670")
OCB = ("2026_10_05", "187807")
NKI_IDS = ("188001", "187962", "187963", "187964")

GLUKEM_FINDINGS = [
    {"sev": "critical", "ko": "유동층 코팅기 흡기 플레넘에 세척 후 액체 고임·잔류물",
     "span": "critical deficiency related to contamination risk in the Fluid Bed Coating (FBC) equipment"},
    {"sev": "major", "ko": "마스터 배치 기록 지시·생산 문서 부적절",
     "span": "Inadequate Master Batch Record instructions and production documentation"},
    {"sev": "major", "ko": "공정 밸리데이션 미흡", "span": "Deficient process validation"},
    {"sev": "major", "ko": "세척 밸리데이션 불완전", "span": "Incomplete cleaning validation"},
    {"sev": "major", "ko": "차압 일탈", "span": "Differential pressure excursions"},
    {"sev": "major", "ko": "창고·검체채취·칭량 관리 미흡",
     "span": "Deficient warehouse, sampling and dispensing controls"},
    {"sev": "major", "ko": "공급업체 적격성 평가 부적절", "span": "Inadequate vendor qualification"},
    {"sev": "major", "ko": "입고 출발물질 확인시험 부적절",
     "span": "Inadequate identification testing of incoming starting materials"},
    {"sev": "major", "ko": "첨가제 위험성 평가", "span": "Excipient risk assessments"},
    {"sev": "major", "ko": "OOS 처리", "span": "Out-of-Specification handling"},
    {"sev": "major", "ko": "설비 적격성 평가", "span": "Equipment qualification"},
    {"sev": "major", "ko": "방충·방서 관리", "span": "Pest control"},
]


class NcrCountsActionsTest(unittest.TestCase):
    def test_counts_from_real_statements(self) -> None:
        cases = {GLUKEM: {"critical": 1, "major": 11}, XIAN: {"major": 11},
                 OCB: {"critical": 1, "major": 8}}
        for (date, cid), want in cases.items():
            dd = _card(date, cid)["deterministic_detail"]
            self.assertEqual(ic.ncr_counts(dd["nature"]), want, cid)
        nki = _card("2026_10_12", "188001")["deterministic_detail"]
        self.assertEqual(ic.ncr_counts(nki["nature"]), {"critical": 1, "major": 7, "other": 14})

    def test_counts_never_read_dates_or_unrelated_words(self) -> None:
        self.assertEqual(ic.ncr_counts("Inspection on 10-12 February 2026. Major changes were made."), {})
        self.assertEqual(ic.ncr_counts(""), {})

    def test_actions_are_form_headings_only(self) -> None:
        self.assertEqual(ic.ncr_actions(_card(*GLUKEM)["deterministic_detail"]["action"]),
                         ["prohibition_of_supply", "ma_revocation"])
        self.assertEqual(ic.ncr_actions(_card(*XIAN)["deterministic_detail"]["action"]),
                         ["cep_suspension"])
        nki = _card("2026_10_12", "188001")["deterministic_detail"]["action"]
        self.assertEqual(ic.ncr_actions(nki), ["certificate_withdrawal", "clinical_trial_suspension"])
        self.assertEqual(ic.ncr_actions("Others: none"), [])


class NcrItemsTest(unittest.TestCase):
    def test_numbered_statement_splits_with_korean_pair(self) -> None:
        blk = ic.build_inspection_block(_card("2026_10_12", "188001"))
        self.assertEqual([i["no"] for i in blk["items"]], ["C1"] + [f"M{k}" for k in range(1, 8)])
        self.assertTrue(all(i.get("text") for i in blk["items"]))
        self.assertTrue(blk["items"][0]["text"].startswith("규격 외(OOS) 절차의 실행이 부적절하다"))
        self.assertTrue(blk["items"][0]["orig"].startswith("The execution of the Out-of-Specification"))
        self.assertEqual(blk["authority"], {"country": "Netherlands"})

    def test_dash_list_statement(self) -> None:
        blk = ic.build_inspection_block(_card(*XIAN))
        self.assertEqual(len(blk["items"]), 11)
        self.assertEqual(blk["items"][0]["text"], "품질보증 감독 및 품질경영시스템의 미흡")
        self.assertEqual(blk["actions"], ["cep_suspension"])

    def test_prose_statement_has_no_list_without_routine_lines(self) -> None:
        blk = ic.build_inspection_block(_card(*GLUKEM))
        self.assertEqual(blk["items"], [])
        self.assertEqual(blk["counts"], [{"sev": "critical", "n": 1}, {"sev": "major", "n": 11}])
        self.assertEqual(blk["authority"], {"country": "Malta"})

    def test_routine_lines_verified_against_source(self) -> None:
        card = _card(*GLUKEM)
        rep = inj.InjectionReport()
        inj._merge_ncr_findings(card["deterministic_detail"], GLUKEM_FINDINGS, rep, "188112")
        blk = ic.build_inspection_block(card)
        self.assertEqual([i["no"] for i in blk["items"]][:2], ["C1", "M1"])
        self.assertEqual(len(blk["items"]), 12)

    def test_invented_span_drops_whole_list(self) -> None:
        card = _card(*GLUKEM)
        bad = copy.deepcopy(GLUKEM_FINDINGS)
        bad[3]["span"] = "Inadequate sterility assurance of the aseptic line"   # 원문에 없음
        rep = inj.InjectionReport()
        inj._merge_ncr_findings(card["deterministic_detail"], bad, rep, "188112")
        self.assertNotIn("findings_ko", card["deterministic_detail"])
        self.assertTrue(any("span 이 원문에 없음" in w for w in rep.warnings))

    def test_count_mismatch_drops_list(self) -> None:
        card = _card(*GLUKEM)
        rep = inj.InjectionReport()
        inj._merge_ncr_findings(card["deterministic_detail"], GLUKEM_FINDINGS[:-1], rep, "188112")
        self.assertEqual(len(card["deterministic_detail"]["findings_ko"]), 11)
        self.assertEqual(ic.build_inspection_block(card)["items"], [])   # 원문 major 11 ≠ 10


class MergeSameInspectionTest(unittest.TestCase):
    def test_nki_four_statements_become_one_card(self) -> None:
        cards = [c for c in _cards("2026_10_12")]
        merged = ic.merge_same_inspection_ncr(cards)
        self.assertEqual(len(merged), len(cards) - 3)
        ic.attach_inspection_blocks(merged)
        rep = next(c for c in merged if c.get("id") in NKI_IDS)
        self.assertEqual(sorted(s["id"] for s in rep["inspection"]["statements"]), sorted(NKI_IDS))
        self.assertNotIn("_ncr_statements", rep)

    def test_regression_guard_counts_folded_statements_as_present(self) -> None:
        import assemble_regression_guard as rg
        published = {"cards": _cards("2026_10_12")}
        merged = ic.merge_same_inspection_ncr(copy.deepcopy(published["cards"]))
        ic.attach_inspection_blocks(merged)
        res = rg.compare(published, {"cards": merged})
        self.assertFalse(set(NKI_IDS) & set(res.get("missing_cards") or res.get("missing") or []))
        # 대조군: 묶음 표시가 없으면 같은 3장은 소실로 잡혀야 한다(가드가 살아 있다).
        for c in merged:
            c.pop("inspection", None)
        res2 = rg.compare(published, {"cards": merged})
        self.assertTrue(set(NKI_IDS) & set(res2.get("missing_cards") or res2.get("missing") or []))

    def test_different_statements_are_not_merged(self) -> None:
        a, b = _card(*GLUKEM), _card(*XIAN)
        self.assertEqual(len(ic.merge_same_inspection_ncr([a, b])), 2)


class OtherSourcesTest(unittest.TestCase):
    def test_483_wl_mfds_blocks(self) -> None:
        seen = set()
        for c in _cards("2026_10_19") + _cards("2026_10_26"):
            dd = c.get("deterministic_detail") or {}
            blk = ic.build_inspection_block(c)
            t = dd.get("type")
            if t == "fda_483_observations" and blk:
                self.assertEqual(blk["counts"], [{"sev": "observation", "n": len(blk["items"])}])
                seen.add(t)
            elif t == "wl_violations" and blk:
                self.assertTrue(all(i.get("clause") for i in blk["items"]))
                seen.add(t)
            elif t == "gmp_deficiencies" and blk:
                self.assertEqual(sum(x["n"] for x in blk["counts"]), len(blk["items"]))
                self.assertTrue(blk["items"][0]["clause"].startswith("[별표"))
                seen.add(t)
        self.assertEqual(seen, {"fda_483_observations", "wl_violations", "gmp_deficiencies"})

    def test_cards_without_detail_get_no_block(self) -> None:
        self.assertIsNone(ic.build_inspection_block({"id": "x"}))
        self.assertIsNone(ic.build_inspection_block({"deterministic_detail": {"type": "hc_recall_detail"}}))


class ModalityMicrobiologicalTest(unittest.TestCase):
    def test_microbiological_qc_line_is_not_biologic(self) -> None:
        dd = _card(*GLUKEM)["deterministic_detail"]
        body = "\n".join([dd["operations"], dd["nature"], dd["action"]])
        raw = {"product_type": "Human Medicinal Products", "product_description": dd["operations"]}
        self.assertNotEqual(tx.compute_modality(raw, "Glukem Biocare Private Limited", body),
                            tx.MODALITY_BIOLOGIC)

    def test_cell_therapy_still_biologic(self) -> None:
        self.assertEqual(tx.compute_modality(
            {}, "1.6.2 Microbiological: non-sterility 1.1.1.6 Other: cell therapy products"),
            tx.MODALITY_BIOLOGIC)


if __name__ == "__main__":
    unittest.main()
