"""MFDS 품목기준코드 → 제품군 근거(ATC) 배선 가드.

2026-09-21. 제품군 분류기를 증거 기반으로 고친 뒤 국내 회수·행정처분 카드 114장 중
70장(61%)이 배지를 잃었다. 원문이 제형만 말하기 때문이다. MFDS 허가정보의 ATC 를
품목기준코드로 받아 그 자리를 **근거 있게** 채운다.

여기 검사들은 네트워크를 타지 않는다 — 조회 결과를 raw_payload 에 넣은 상태를 만들어
**판정 성질**만 잰다.
"""
from __future__ import annotations

import os
import unittest

import collect_intake as ci
import grm_mfds_item_class as mic

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _card(**raw):
    """국내 회수 카드의 compute_modality 호출부와 같은 모양."""
    return ci.compute_modality(raw, "[회수·판매중지] 어떤약", "", "recall-quality", "어떤제약")


class AtcVerdictTest(unittest.TestCase):
    """★ATC 는 제형이 아니라 물질 성격으로 묶인 국제 분류라 축 혼동이 없다."""

    def test_biologic_groups(self) -> None:
        for atc, label in (("L01FD01", "트라스투주맙"), ("J07BK01", "백신"),
                           ("A10AB01", "인슐린"), ("B02BD02", "혈액응고인자"),
                           ("L03AX", "면역자극제"), ("J06BA02", "면역글로불린")):
            self.assertEqual(_card(mfds_atc_code=atc), ci.MODALITY_BIOLOGIC, msg=label)

    def test_chemical_when_atc_is_not_a_biologic_group(self) -> None:
        # ★'생물 묶음이 아니다' 가 여기서는 양성 근거다 — WHO 가 그 물질을 분류해
        #   저분자 계열에 넣었다는 뜻이기 때문이다(제형을 보고 추정하는 것과 다르다).
        for atc, label in (("H02AB08", "트리암시놀론"), ("N06BX06", "시티콜린"),
                           ("C09AA05", "라미프릴")):
            self.assertEqual(_card(mfds_atc_code=atc), ci.MODALITY_CHEMICAL, msg=label)

    def test_radiopharma_and_contrast_are_out_of_scope(self) -> None:
        for atc in ("V09IX04", "V08AB09"):
            self.assertEqual(_card(mfds_atc_code=atc), ci.MODALITY_OTHER, msg=atc)

    def test_longer_prefix_wins(self) -> None:
        """★접두 매칭은 긴 것이 먼저 이겨야 한다.

        L04AB(TNF 억제제, 생물)와 L04A(면역억제제 일반)가 겹친다. 짧은 쪽이 먼저
        이기면 생물의약품이 화학합성으로 떨어진다.
        """
        self.assertEqual(_card(mfds_atc_code="L04AB01"), ci.MODALITY_BIOLOGIC)

    def test_atc_beats_dosage_form_signals(self) -> None:
        """★ATC 가 제형·제품명 신호보다 앞선다.

        'XX정'(정제) 접미사는 경구 고형이라 Chemical 근거지만, 그 품목의 ATC 가
        생물 묶음이면 ATC 가 이겨야 한다(규제기관 분류 > 이름 추정).
        """
        self.assertEqual(
            ci.compute_modality({"mfds_atc_code": "L01FD01", "PRDUCT": "어떤정"},
                                "[회수] 어떤정", "", "recall-quality", "어떤제약"),
            ci.MODALITY_BIOLOGIC)


class NoEvidenceTest(unittest.TestCase):
    """★조회하지 못한 것과 값이 없는 것을 판정 근거로 쓰지 않는다."""

    def test_not_found_is_not_a_verdict(self) -> None:
        """허가정보 API 는 의약품만 담는다. 그렇다고 0건 = 의약외품은 아니다.

        API 누락·신규 등록 지연도 같은 0건을 낸다. 구분할 수 없으면 판정하지 않는다.
        """
        self.assertEqual(_card(mfds_item_lookup=mic.LOOKUP_NOT_FOUND),
                         ci.MODALITY_UNKNOWN)

    def test_lookup_failures_are_not_verdicts(self) -> None:
        for state in (mic.LOOKUP_ERROR, mic.LOOKUP_NO_KEY, mic.LOOKUP_NO_SEQ,
                      mic.LOOKUP_BUDGET_EXHAUSTED):
            self.assertEqual(_card(mfds_item_lookup=state),
                             ci.MODALITY_UNKNOWN, msg=state)

    def test_ingredient_modifiers_do_not_decide(self) -> None:
        """'알파'·'베타'·'페그' 는 저분자에도 붙는다 — 신호로 쓰면 안 된다."""
        self.assertEqual(_card(mfds_main_ingredient="[M9]알파칼시돌"),
                         ci.MODALITY_UNKNOWN)

    def test_ingredient_stem_decides_when_atc_missing(self) -> None:
        self.assertEqual(_card(mfds_main_ingredient="[M1]아달리무맙"),
                         ci.MODALITY_BIOLOGIC)


class LookupContractTest(unittest.TestCase):
    """수집기↔분류기 계약 — 키 이름이 한쪽에서만 바뀌면 조용히 끊긴다."""

    def test_keys_are_shared_between_producer_and_consumer(self) -> None:
        with open(os.path.join(_ROOT, "grm_taxonomy.py"), encoding="utf-8") as f:
            src = f.read()
        for key in (mic.KEY_ATC, mic.KEY_INGREDIENT):
            self.assertIn(f'"{key}"', src,
                          f"분류기가 생산자 키 '{key}' 를 읽지 않는다")

    def test_both_korean_collectors_enrich(self) -> None:
        """★생산자가 빠지면 슬롯만 남는다 — 두 수집기 모두 배선돼 있어야 한다."""
        for fname in ("collect_mfds_recall.py", "collect_mfds_admin_action.py"):
            with open(os.path.join(_ROOT, fname), encoding="utf-8") as f:
                src = f.read()
            self.assertIn("enrich_raw_payload(raw_payload, item_seq)", src, fname)
            self.assertIn("from grm_mfds_item_class import", src, fname)

    def test_no_network_without_key(self) -> None:
        """자격증명이 없으면 호출하지 않는다(검사 환경·로컬에서 조용히 통과)."""
        self.assertEqual(mic.fetch_item_class("200504683", service_key=""),
                         {mic.KEY_LOOKUP: mic.LOOKUP_NO_KEY})

    def test_multi_item_seq_takes_first_and_marks(self) -> None:
        raw: dict = {}
        mic.enrich_raw_payload(raw, "197400553,197400555", service_key="")
        self.assertTrue(raw.get("mfds_item_seq_multi"))
        self.assertEqual(mic._first_item_seq("197400553,197400555"), "197400553")


if __name__ == "__main__":
    unittest.main()
