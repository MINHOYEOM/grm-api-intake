"""시사점·점검을 비울 수 있다 — 2026-11-10 브리프 재정비(대표 결정 2·9).

쓸 원문 정보가 없는 카드(사유 한 단어 회수·지적 없는 실사·본문 미확보 고시)는 Routine 이
`implication=""`·`checks=[]` 로 낸다. 그런 카드가

  1) 조립 게이트 2("빈 슬롯")에 막히지 않고(경고만),
  2) 영문 갈래도 같은 칸이 비어 있으면 완결로 치며(영어판이 안 꺼진다),
  3) 반대로 한국어는 채웠는데 영문만 비면 여전히 반쪽 영어로 막히는지

를 조립기·렌더 판정·편집팀 병합기 세 곳에서 같은 결과로 확인한다(판정이 갈리면 한 곳은
통과시키고 다른 곳이 조용히 그 호의 영어판을 끈다).
"""
from __future__ import annotations

import copy
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "company" / "editorial"))
sys.path.insert(0, str(ROOT / "tests"))

import assemble_publish_brief as apb  # noqa: E402
import inject_slots as inj  # noqa: E402
import merge_parts as mp  # noqa: E402
import render  # noqa: E402
from test_assemble_publish_brief import DELTA_PATH, TRUTH_PATH, _blank_scaffold_from  # noqa: E402

import json  # noqa: E402

EN_FULL = {"title_issue": "Recall for appearance failure",
           "summary": "One product was recalled.",
           "key_facts": ["Reason: appearance"],
           "implication": "", "checks": []}


def _card(**over):
    c = {"id": "c1", "title_issue": "성상 부적합 회수", "summary": "한 품목이 회수됐다.",
         "key_facts": ["사유: 성상"], "implication": "", "checks": [], "en": dict(EN_FULL)}
    c.update(over)
    return c


class EnglishPairingTest(unittest.TestCase):
    """render.card_english_gaps · merge_parts._en_gaps — 같은 판정."""

    def _both(self, card):
        return render.card_english_gaps(card), mp._en_gaps(card)

    def test_empty_ko_and_en_is_complete(self):
        card = _card()
        self.assertEqual(self._both(card), ([], []))
        self.assertTrue(render.card_has_english(card))

    def test_ko_filled_en_empty_is_gap(self):
        card = _card(implication="회사 회신을 받아들이지 않았다.",
                     checks=["재시험 전에 조사를 여는가?", "품질부서가 승인하는가?"])
        r, m = self._both(card)
        self.assertEqual(r, ["implication", "checks"])
        self.assertEqual(m, r)
        self.assertFalse(render.card_has_english(card))

    def test_required_three_never_optional(self):
        for field in ("title_issue", "summary", "key_facts"):
            card = _card(en={**EN_FULL, field: [] if field == "key_facts" else ""})
            self.assertEqual(self._both(card), ([field], [field]), field)

    def test_optional_set_matches_across_modules(self):
        self.assertEqual(tuple(render.CARD_NARRATIVE_OPTIONAL), tuple(mp.EN_OPTIONAL))
        self.assertEqual(tuple(render.CARD_NARRATIVE_OPTIONAL),
                         tuple(inj._EN_OPTIONAL_IF_KO_EMPTY))

    def test_english_view_tolerates_missing_optional_keys(self):
        en = {k: v for k, v in EN_FULL.items() if k not in ("implication", "checks")}
        card = _card(en=en)
        self.assertTrue(render.card_has_english(card))
        view = render._card_with_english_narrative(card, "en")
        self.assertEqual(view["implication"], "")
        self.assertEqual(view["checks"], [])


class InjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
        cls.delta = json.loads(DELTA_PATH.read_text(encoding="utf-8"))

    def _delta_with_empty(self, en_impl=""):
        d = copy.deepcopy(self.delta)
        cid = next(iter(d["cards"]))
        d["cards"][cid]["implication"] = ""
        d["cards"][cid]["checks"] = []
        d["cards"][cid]["en"] = {**EN_FULL, "implication": en_impl}
        return d, cid

    def test_empty_prose_assembles_with_warning(self):
        s = _blank_scaffold_from(self.truth)
        d, cid = self._delta_with_empty()
        out, report = apb.assemble_publish_brief(s, d, strict=True)
        card = next(c for c in out["cards"] if c["id"] == cid)
        self.assertEqual(card["implication"], "")
        self.assertEqual(card["checks"], [])
        self.assertEqual(card["en"]["implication"], "")      # 영문 갈래가 버려지지 않는다
        self.assertTrue(any("implication 비움" in w for w in report.warnings), report.warnings)

    def test_card_missing_entirely_still_blocks(self):
        s = _blank_scaffold_from(self.truth)
        d = copy.deepcopy(self.delta)
        d["cards"][next(iter(d["cards"]))] = {}
        with self.assertRaises((apb.AssembleError, inj.SlotInjectionError)):
            apb.assemble_publish_brief(s, d, strict=True)

    def test_ko_filled_en_empty_rejected(self):
        d = copy.deepcopy(self.delta)
        cid = next(iter(d["cards"]))
        d["cards"][cid]["implication"] = "회사 회신을 받아들이지 않았다."
        d["cards"][cid]["en"] = dict(EN_FULL)                  # implication 빈 영문
        rep = inj.validate_injection(_blank_scaffold_from(self.truth), d)
        self.assertTrue(any(f"{cid}.en: 부분 제공" in e for e in rep.errors), rep.errors)

    def test_one_check_still_rejected(self):
        d, cid = self._delta_with_empty()
        d["cards"][cid]["checks"] = ["하나만?"]
        rep = inj.validate_injection(_blank_scaffold_from(self.truth), d)
        self.assertTrue(any(f"{cid}.checks" in e for e in rep.errors), rep.errors)


if __name__ == "__main__":
    unittest.main()
