# -*- coding: utf-8 -*-
"""영문 슬롯 게이트 — 빠진 호를 발행 PR 전에 잡는지."""
import json
import os
import tempfile
import unittest

import check_en_slots as g

FULL = {"title_issue": "t", "summary": "s", "implication": "i", "key_facts": ["k"], "checks": ["c"]}


def _doc(cards, tldr=("a",)):
    return {"brief": {"en": {"tldr": list(tldr)} if tldr else {}}, "cards": cards}


class GateTest(unittest.TestCase):
    def test_complete_passes(self):
        self.assertEqual(g.find_problems(_doc([{"id": "a", "en": FULL}])), [])

    def test_no_en_listed_with_count(self):
        out = g.find_problems(_doc([{"id": "a"}, {"id": "b", "en": FULL}]))
        self.assertTrue(any("1/2" in p and "a" in p for p in out))

    def test_partial_names_missing_field(self):
        # 한국어 checks 가 채워졌는데 영문만 비었다 = 반쪽 영어.
        out = g.find_problems(_doc([{"id": "a", "checks": ["확인?", "둘?"],
                                     "en": dict(FULL, checks=[])}]))
        self.assertTrue(any("checks" in p for p in out))

    def test_both_empty_optional_passes(self):
        """[2026-11-10] 쓸 원문 정보가 없어 한국어도 시사점·점검을 비운 카드는 완결이다."""
        card = {"id": "a", "implication": "", "checks": [],
                "en": dict(FULL, implication="", checks=[])}
        self.assertEqual(g.find_problems(_doc([card])), [])

    def test_missing_tldr(self):
        out = g.find_problems(_doc([{"id": "a", "en": FULL}], tldr=None))
        self.assertTrue(any("tldr" in p for p in out))

    def test_foreign_code_slot_hangul_flagged(self):
        """[2026-11-23] 해외 카드 공용 코드 칸에 사람이 넣은 국문 표기(11-23호 '개인 사업자'와 같은 부류)는 막는다."""
        card = {"id": "a", "agency": "FDA", "headline_target": "농장주", "en": FULL,
                "facts": [{"label": "제조소/업체", "value": "농장주 · FEI 1"}]}
        out = g.find_problems(_doc([card]))
        self.assertTrue(any("한글" in p and "headline_target" in p and "facts[0]" in p for p in out), out)

    def test_redacted_person_and_code_suffixes_pass(self):
        """뷰가 언어별로 정하는 표기(개인 사업자·외 N품목·(영국))는 영어 트리에서 한글이 남지 않는다."""
        import render
        card = {"id": "a", "agency": "FDA", "headline_target": "개인 사업자", "en": FULL,
                "facts": [{"label": "제조소/업체", "value": "개인 사업자 · FEI 3041017158"},
                          {"label": "제품", "value": "Drug X 외 41품목"},
                          {"label": "발행기관", "value": "MHRA (영국)"}]}
        self.assertEqual(g.find_problems(_doc([card])), [])
        en = render._card_with_english_narrative(card, "en")
        self.assertEqual(en["headline_target"], "Individual business")
        self.assertEqual([f["value"] for f in en["facts"]],
                         ["Individual business · FEI 3041017158", "Drug X and 41 more products", "MHRA (UK)"])
        self.assertEqual(render._card_with_english_narrative(card, "ko"), card)   # 한국어 트리 무변형
        self.assertEqual(render._firm_key_for_card(card), "")                     # 가린 이름은 프로파일로 잇지 않는다
        self.assertEqual(render._firm_key_for_card(en), "")

    def test_mfds_korean_names_allowed(self):
        """식약처 카드의 한글 실명은 원래 한국어라 그대로 둔다(화면이 밝힌다)."""
        card = {"id": "a", "agency": "MFDS", "headline_target": "동아제약(주)", "en": FULL}
        self.assertEqual(g.find_problems(_doc([card])), [])

    def test_cli_exit_codes(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_doc([{"id": "a"}]), f)
        self.assertEqual(g.main([p]), 1)
        self.assertEqual(g.main([p, "--warn-only"]), 0)
        self.assertEqual(g.main([os.path.join(d, "none.json")]), 2)


if __name__ == "__main__":
    unittest.main()
