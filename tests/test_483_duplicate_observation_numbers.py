# -*- coding: utf-8 -*-
"""483 관찰 번호 중복 — 국문 병기 합치기와 심층 사전 점검(리허설 1120i).

fda483-196399 의 결정론 관찰 번호가 `1,2,3,5,5,6,7,8`(원문 OCR 이 4 를 5 로 읽음)이었다. 기자는 번역 8건을
모두 넣었지만 `inject_slots._merge_observation_translations` 가 번호 하나에 관찰 하나만 남겨 앞선 #5 에
국문이 붙지 않았고, 조립(교열 코드 검사)·백업 v16 발행(같은 assemble_publish_brief)이 둘 다 막혔다.
같은 꼴의 fda483-196254(`1,1,2,3,4,1,2,2`)·196260(`…,5,5,…`)은 조립 관문(게이트 4)이 '관찰을 새로 만든
카드'만 봐서 사전 점검을 0건으로 지나갔고, 배포 게이트(`render.validate_483_observations`)에서 막힐 뻔했다.
"""
import copy
import importlib.util
import unittest
from pathlib import Path
from unittest import mock

import assemble_publish_brief as apb
import inject_slots
import verify_deep_analysis as vda

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("company_precheck_dup", ROOT / "company" / "editorial" / "precheck.py")
precheck = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(precheck)


def _obs(nums):
    return [{"number": n, "deficiency": f"Deficiency {i}", "detail": f"Detail {i}"}
            for i, n in enumerate(nums)]


def _ko(nums):
    return [{"number": n, "deficiency_ko": f"국문 {i}", "detail_ko": f"상세 {i}"}
            for i, n in enumerate(nums)]


def _card(nums):
    return {"id": "fda483-1", "card_type": "FDA 483",
            "deterministic_detail": {"type": "fda_483_observations", "observations": _obs(nums)}}


def _merge(card, obs_ko):
    inject_slots._merge_observation_translations(card, obs_ko, inject_slots.InjectionReport(), card["id"])
    return card["deterministic_detail"]["observations"]


class MergeDuplicateNumbersTest(unittest.TestCase):
    def test_duplicate_numbers_pair_in_order(self):
        for nums in (["1", "2", "3", "5", "5", "6", "7", "8"],      # fda483-196399
                     ["1", "1", "2", "3", "4", "1", "2", "2"]):     # fda483-196254
            with self.subTest(nums=nums):
                obs = _merge(_card(nums), _ko(nums))
                # 관찰 i 에 번역 i — 같은 번호라도 제 자리에 붙는다.
                self.assertEqual([o.get("deficiency_ko") for o in obs], [f"국문 {i}" for i in range(len(nums))])
                self.assertEqual([o.get("detail_ko") for o in obs], [f"상세 {i}" for i in range(len(nums))])
                self.assertEqual(apb._lint_483_observation_ko([{"id": "x", "deterministic_detail":
                                                               {"type": "fda_483_observations",
                                                                "observations": obs}}]), [])

    def test_unique_numbers_unchanged(self):
        # 번호가 유일한 정상 카드 — 번역 순서가 뒤섞여도 번호로 붙는다(종전 동작).
        nums = ["1", "2", "3"]
        obs = _merge(_card(nums), list(reversed(_ko(nums))))
        self.assertEqual([o["deficiency_ko"] for o in obs], ["국문 0", "국문 1", "국문 2"])

    def test_count_mismatch_falls_back_to_last(self):
        # 같은 번호의 관찰 2건에 번역 1건 — 순서를 믿을 수 없어 종전대로 마지막 관찰에만 붙인다.
        obs = _merge(_card(["1", "5", "5"]), [{"number": "5", "deficiency_ko": "하나뿐"}])
        self.assertNotIn("deficiency_ko", obs[1])
        self.assertEqual(obs[2]["deficiency_ko"], "하나뿐")


class PrecheckDeepDuplicateNumbersTest(unittest.TestCase):
    NUMS = ["1", "1", "2"]

    def setUp(self):
        p = mock.patch.object(vda, "run_deep_analysis_gate",
                              return_value=vda.GateResult(ok=True, findings=[], report=""))
        p.start()
        self.addCleanup(p.stop)

    def _scaffold(self):
        return {"brief": {"run_date_kst": "2026-11-20"}, "cards": [copy.deepcopy(_card(self.NUMS))]}

    def _shard(self, obs_ko):
        return {"items": {"fda483-1": {"deep_analysis": {"key_violations": "x"}, "observations_ko": obs_ko}}}

    def test_filled_duplicate_numbers_pass(self):
        self.assertEqual(precheck.deep_assembly_lines(self._shard(_ko(self.NUMS)), {"rows": []},
                                                      self._scaffold()), [])

    def test_missing_on_non_refreshed_card_is_caught(self):
        # 원문이 없어 조립이 관찰을 다시 만들지 않는 카드(refreshed_483_ids 밖)도 배포 게이트처럼 본다.
        lines = precheck.deep_assembly_lines(self._shard(_ko(["1", "2"])), {"rows": []}, self._scaffold())
        self.assertTrue(lines)
        self.assertTrue(all(x.startswith("심층 조립 검사 카드 'fda483-1' 관찰 #1") for x in lines), lines)


if __name__ == "__main__":
    unittest.main()
