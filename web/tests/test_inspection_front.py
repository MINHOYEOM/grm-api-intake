"""[실사 결과 카드 2026-11-08] 앞면 블록 렌더 — 한국어·영어 트리, 과거 호 불변.

데이터는 이미 발행된 실제 카드(web/data/briefs)에 inspection_card 를 적용해 만든다.
★과거 호는 `inspection` 키가 없어 종전 모양 그대로여야 한다(소급 없음) — 브리프 골든이 바이트로
  지키고, 여기서는 뷰 값(agency_label·summary)이 그대로인지 직접 본다.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402
import inspection_card as ic  # noqa: E402
from grm_i18n import Translator  # noqa: E402

BRIEFS = WEB_DIR / "data" / "briefs"
EN = Translator("en")


def _cards(date: str) -> list[dict]:
    return json.loads((BRIEFS / f"brief_web_{date}.json").read_text(encoding="utf-8"))["cards"]


def _assembled(date: str) -> list[dict]:
    cards = ic.merge_same_inspection_ncr(copy.deepcopy(_cards(date)))
    ic.attach_inspection_blocks(cards)
    return cards


def _render_card(view: dict, lang: str) -> str:
    tr = Translator(lang)
    env = render._make_env(lang, tr)
    return env.get_template("partials/card.html").render(card=view)


class InspectionFrontViewTest(unittest.TestCase):
    def test_published_card_view_unchanged_without_block(self) -> None:
        card = next(c for c in _cards("2026_10_12") if c["id"] == "188001")
        self.assertNotIn("inspection", card)
        v = render._card_view(card)
        self.assertIsNone(v["inspection"])
        self.assertEqual(v["agency_label"], card["agency"])           # "EMA" 그대로(과거 호)
        self.assertEqual(v["summary"], card["summary"])
        self.assertEqual(v["implication"], card["implication"])

    def test_nki_korean_front(self) -> None:
        card = next(c for c in _assembled("2026_10_12") if c["id"] == "188001")
        v = render._card_view(card)
        ins = v["inspection"]
        self.assertEqual([c["text"] for c in ins["chips"]], ["치명 1", "주요 7", "기타 14"])
        self.assertEqual(ins["statements"], "판정서 4건")
        self.assertEqual(len(ins["items_head"]), 5)
        self.assertEqual(len(ins["items_rest"]), 3)
        self.assertEqual(ins["actions"], ["GMP 인증서 철회", "임상시험 중지"])
        self.assertEqual(v["agency_label"], "Netherlands 규제당국")
        # 목록이 대신하는 서술층은 앞면에서 빠진다(데이터는 그대로).
        self.assertEqual((v["summary"], v["quotes"], v["key_facts"], v["implication"]), ("", [], [], ""))
        self.assertTrue(card["summary"])
        html = _render_card(v, "ko")
        self.assertIn('class="block insp"', html)
        self.assertIn("[EU GMP 비준수 · Netherlands 규제당국]", html)
        self.assertIn("나머지 3건 보기", html)
        self.assertNotIn('class="summary"', html)

    def test_nki_english_front_uses_source_text(self) -> None:
        card = next(c for c in _assembled("2026_10_12") if c["id"] == "188001")
        v = render._card_view(card, EN, "en")
        ins = v["inspection"]
        self.assertEqual(v["agency_label"], "Netherlands competent authority")
        self.assertEqual([c["text"] for c in ins["chips"]], ["Critical 1", "Major 7", "Other 14"])
        self.assertTrue(ins["items_head"][0]["text"].startswith("The execution of the Out-of-Specification"))
        self.assertFalse(any(render._HANGUL_RE.search(i["text"]) for i in ins["items_head"] + ins["items_rest"]))
        html = _render_card(v, "en")
        self.assertIn("Regulatory action", html)
        self.assertIn("Show 3 more", html)

    def test_mfds_items_not_shown_in_english(self) -> None:
        card = next(c for c in _assembled("2026_10_19")
                    if (c.get("deterministic_detail") or {}).get("type") == "gmp_deficiencies")
        ko = render._card_view(card)["inspection"]
        self.assertTrue(ko["items_head"])
        self.assertIn("[별표", ko["items_head"][0]["clause"])
        en = render._card_view(card, EN, "en")
        self.assertEqual(en["inspection"]["items_head"], [])         # 원문이 한국어뿐 — 싣지 않는다
        self.assertTrue(en["inspection"]["chips"])                    # 개수는 남는다
        self.assertTrue(en["summary"])                                # 목록이 없으면 종전 층 유지

    def test_483_front(self) -> None:
        card = next(c for c in _assembled("2026_10_26")
                    if (c.get("deterministic_detail") or {}).get("type") == "fda_483_observations"
                    and c.get("inspection"))
        v = render._card_view(card)
        self.assertTrue(v["inspection"]["chips"][0]["text"].startswith("Observation "))
        self.assertEqual(v["agency_label"], "FDA")
        _render_card(v, "ko")
        _render_card(render._card_view(card, EN, "en"), "en")


if __name__ == "__main__":
    unittest.main()
