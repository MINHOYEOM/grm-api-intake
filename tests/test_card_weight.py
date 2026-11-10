"""카드 무게 — 깊이 읽는 카드 / 표준 / 표 한 줄 (2026-11-10 대표 결정 A·B·C).

1) 판정(`card_weight.py`): 문턱 상수가 11-09호 실측(깊이 9·표준 23·표 19)을 그대로 재현한다.
2) 조립: 11-16호부터만 `card_weight` 를 붙인다(소급 없음).
3) 렌더: 무게가 있는 호만 묶음 안 순서·표 묶음·꼬리표·라벨이 바뀌고, 없는 호는 그대로다.
"""
from __future__ import annotations

import collections
import copy
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import card_weight as cw  # noqa: E402
import render  # noqa: E402

BRIEF_1109 = ROOT / "web" / "data" / "briefs" / "brief_web_2026_11_09.json"


def _cards_1109() -> list[dict]:
    return json.loads(BRIEF_1109.read_text(encoding="utf-8"))["cards"]


class CardWeightClassifyTest(unittest.TestCase):
    def test_1109_counts_match_measured(self):
        # 재정비안 §2-3: 51장 = 깊이 9 · 표준 23 · 표 19. 문턱을 바꾸면 이 숫자가 먼저 깨진다.
        got = collections.Counter(cw.classify_card_weight(c) for c in _cards_1109())
        self.assertEqual(dict(got), {"deep": 9, "standard": 23, "row": 19})

    def test_1109_breakdown(self):
        by = collections.defaultdict(collections.Counter)
        for c in _cards_1109():
            by[cw.classify_card_weight(c)][c.get("card_type")] += 1
        # 깊이: 경고서한 5 · Class I 2 · Type I 1 · 지적 9건 483 1
        self.assertEqual(by["deep"]["Warning Letter"], 5)
        self.assertEqual(by["deep"]["Recall"], 2)
        self.assertEqual(by["deep"]["Recall(HC)"], 1)
        self.assertEqual(by["deep"]["FDA 483 실사 관찰"], 1)
        # 표 한 줄: 한약재 회수 7 · FDA Class II 6 · MHRA 2 · 지적 없는 실사 1 · 고시 3
        self.assertEqual(by["row"]["회수·판매중지"], 7)
        self.assertEqual(by["row"]["Recall"], 6)
        self.assertEqual(by["row"]["Recall(UK)"], 2)
        self.assertEqual(by["row"]["GMP실사"], 1)
        self.assertEqual(by["row"]["고시·개정법령"], 3)

    def test_thresholds_are_boundaries(self):
        base = {"card_type": "Recall", "agency": "FDA",
                "facts": [{"label": "Class", "value": "Class II"}]}
        at = dict(base, quotes=[{"original": "x" * cw.ROW_FDA_CLASS2_REASON_LT}])
        below = dict(base, quotes=[{"original": "x" * (cw.ROW_FDA_CLASS2_REASON_LT - 1)}])
        self.assertEqual(cw.classify_card_weight(at), "standard")
        self.assertEqual(cw.classify_card_weight(below), "row")
        mf = {"card_type": "회수·판매중지", "agency": "MFDS"}
        self.assertEqual(cw.classify_card_weight(
            dict(mf, quotes=[{"original": "가" * cw.ROW_MFDS_RECALL_REASON_MAX}])), "row")
        self.assertEqual(cw.classify_card_weight(
            dict(mf, quotes=[{"original": "가" * (cw.ROW_MFDS_RECALL_REASON_MAX + 1)}])), "standard")
        obs = lambda n: {"card_type": "FDA 483 실사 관찰", "type_tag": "483", "agency": "FDA",  # noqa: E731
                         "deterministic_detail": {"type": "fda_483_observations",
                                                  "observations": [{"number": str(i)} for i in range(n)]}}
        self.assertEqual(cw.classify_card_weight(obs(cw.DEEP_483_MIN_OBSERVATIONS)), "deep")
        self.assertEqual(cw.classify_card_weight(obs(cw.DEEP_483_MIN_OBSERVATIONS - 1)), "standard")

    def test_notice_with_body_is_standard(self):
        c = {"card_type": "지침·안내서", "agency": "MFDS", "source_body_captured": True}
        self.assertEqual(cw.classify_card_weight(c), "standard")
        self.assertEqual(cw.classify_card_weight(dict(c, source_body_captured=None)), "row")

    def test_merged_no_finding_card_is_row(self):
        c = {"card_type": "GMP실사", "key_facts": ["건수: 3건 (평가 결과 지적(보완)사항 없음)"]}
        self.assertEqual(cw.classify_card_weight(c), "row")

    def test_applies_only_from_1116(self):
        f = cw.card_weight_applies
        self.assertFalse(f({"publish_date": "2026-11-09"}))
        self.assertTrue(f({"publish_date": "2026-11-16"}))
        self.assertFalse(f({"run_date_kst": "2026-11-02"}))
        self.assertTrue(f({}))

    def test_assign_does_not_change_other_fields(self):
        cards = _cards_1109()
        before = copy.deepcopy(cards)
        cw.assign_card_weights(cards)
        for a, b in zip(cards, before):
            a = dict(a)
            self.assertIn(a.pop("card_weight"), cw.WEIGHTS)
            self.assertEqual(a, b)


class CardWeightRenderTest(unittest.TestCase):
    def _views(self, weighted: bool, lang: str = "ko"):
        cards = sorted(_cards_1109(), key=lambda c: c["render_order"])
        if weighted:
            cw.assign_card_weights(cards)
        tr = render.Translator(lang) if lang != "ko" else render._KO
        return [render._card_view(c, tr, lang) for c in cards], tr

    def test_unweighted_issue_keeps_order_and_label(self):
        views, tr = self._views(False)
        sections = render._build_sections(views, tr)
        flat = [cv["anchor"] for s in sections for cv in s["cards"]]
        self.assertEqual(flat, [cv["anchor"] for cv in views])
        for cv in views:
            self.assertEqual((cv["weight"], cv["list_tag"], cv["row"]), ("", "", None))
            self.assertEqual((cv["imp_label"], cv["imp_sub"]), ("시사점", "편집 해석"))
        self.assertFalse(any(cv["row_open"] for s in sections for cv in s["cards"]))

    def test_weighted_order_deep_standard_row_within_each_bundle(self):
        views, tr = self._views(True)
        sections = render._build_sections(views, tr)
        self.assertEqual([s["slug"] for s in sections], ["글로벌", "국내", "Recall"])
        rank = {"deep": 0, "standard": 1, "row": 2}
        for s in sections:
            runs = collections.defaultdict(list)
            for cv in s["cards"]:
                runs[cv.get("group_label")].append(rank[cv["weight"]])
            for seq in runs.values():
                self.assertEqual(seq, sorted(seq), s["slug"])
        self.assertEqual(sum(s["count"] for s in sections), 51)

    def test_row_bundles(self):
        views, tr = self._views(True)
        sections = render._build_sections(views, tr)
        heads = [cv["row_head"]["n"] for s in sections for cv in s["cards"] if cv["row_open"]]
        closes = sum(cv["row_close"] for s in sections for cv in s["cards"])
        self.assertEqual(heads, [1, 3, 15])      # 지적 없는 실사 · 고시 · 회수
        self.assertEqual(closes, 3)

    def test_row_cards_have_no_interpretation(self):
        views, _ = self._views(True)
        rows = [cv for cv in views if cv["weight"] == "row"]
        self.assertEqual(len(rows), 19)
        for cv in rows:
            self.assertEqual((cv["implication"], cv["checks"]), ("", []))
            self.assertTrue(cv["row"]["date"])
        hansol = next(cv for cv in rows if cv["headline_target"] == "한솔제약(주)")
        self.assertEqual(hansol["row"]["reason"], "관능(성상)")
        self.assertEqual(hansol["row"]["product"], "한솔두충염자")

    def test_list_tag_is_one_fact(self):
        views, _ = self._views(True)
        by = {cv["headline_target"]: cv["list_tag"] for cv in views}
        self.assertEqual(by["LeeSar Inc"], "Observation 9건")
        self.assertEqual(by["대성가스텍(주)"], "지적 없음")
        self.assertEqual(by["한솔제약(주)"], "회수명령 (강제) · 관능(성상)")

    def test_warning_letter_front_shows_response(self):
        views, _ = self._views(True)
        wl = next(cv for cv in views if cv["headline_target"].startswith("Pritam International"))
        self.assertEqual(wl["weight"], "deep")
        self.assertTrue(wl["implication"])
        self.assertEqual(wl["imp_label"], "회신과 FDA 판단")

    def test_deep_front_facts_pick_agency_action(self):
        card = next(c for c in _cards_1109() if c["headline_target"].startswith("Pritam International"))
        card = dict(card, card_weight="deep",
                    key_facts=["위반 1: x", "회신과 FDA 판단: y", "당국 조치: 수입경보 66-40 등재"])
        cv = render._card_view(card)
        # 시사점 칸이 회신·판단을 싣고 있으니 앞면 줄에는 당국 조치만.
        self.assertEqual(cv["front_facts"], ["당국 조치: 수입경보 66-40 등재"])

    def test_english_tree_same_shape(self):
        views, tr = self._views(True, "en")
        sections = render._build_sections(views, tr)
        heads = [cv["row_head"]["n"] for s in sections for cv in s["cards"] if cv["row_open"]]
        self.assertEqual(heads, [1, 3, 15])
        by = {cv["headline_target"]: cv["list_tag"] for cv in views}
        self.assertEqual(by["LeeSar Inc"], "9 observations")
        self.assertFalse(any(render._HANGUL_RE.search(cv["imp_label"]) for cv in views))


if __name__ == "__main__":
    unittest.main()
