"""브리프 첫 화면 '이번 주 꼭 볼 5건' + 목차 기관별 축약 가드 (2026-11-26 대표 결정).

고르는 규칙은 render.select_must_see 하나에만 있다. 이 시험이 그 규칙을 고정한다.
  1. 묶음 순서: 처분(경고서한·허가 취소) → 식약처(GMP 실사·그 밖의 처분) → 무균·데이터 483(관찰 많은 순) → 지침(공동 해석 먼저)
  2. 묶음마다 한 건씩 먼저, 남은 자리는 같은 순서로 다시. 한 기관 최대 2건, 최대 5건.
  3. 한 줄 표(row) 카드·회수는 고르지 않는다.
  4. 국·영 같은 카드(판정은 번역 전 값) — 발행 브리프 전부로 잰다.
  5. 화면: 칸은 같은 쪽 카드로 이어지고, 목차의 카드 목록은 '전체 보기' 안에 접혀 있다.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"
sys.path.insert(0, str(WEB))
import render  # noqa: E402

BRIEFS = sorted((WEB / "data" / "briefs").glob("brief_web_*.json"))
GOLDEN = WEB / "tests" / "golden"


def _card(i, agency, card_type, *, type_tag="", title="", n=0, weight="standard"):
    return {"id": f"c{i}", "render_order": i, "agency": agency, "card_type": card_type,
            "type_tag": type_tag, "title_issue": title, "headline_target": f"firm{i}",
            "card_weight": weight, "deterministic_detail": {"count": n} if n else None}


def _sorted_cards(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    cs = [c for c in d.get("cards") or [] if render._is_renderable(c)]
    return sorted(cs, key=lambda c: (c.get("render_order") is None, c.get("render_order")))


class MustSeeRuleTest(unittest.TestCase):
    def test_bucket_order_and_round_robin(self):
        cards = [
            _card(0, "FDA", "FDA 483 실사 관찰", type_tag="483", title="무균 조작 미흡", n=3),
            _card(1, "FDA", "FDA 483 실사 관찰", type_tag="483", title="데이터 통제 미흡", n=9),
            _card(2, "MHRA", "규제 소식", title="Annex 1 통일 해석"),
            _card(3, "FDA", "Warning Letter", title="기준일탈 조사"),
            _card(4, "MFDS", "GMP실사", title="안정성시험 미실시"),
            _card(5, "MFDS", "행정처분", title="업무정지 중 출하, 허가 취소"),
            _card(6, "FDA", "Recall", type_tag="Recall"),
        ]
        picked = [cards[i]["id"] for i in render.select_must_see(cards)]
        # 경고서한·허가 취소(묶음 0) → GMP 실사(1) → 관찰 9건 483(2) → 공동 지침(3)
        self.assertEqual(picked, ["c3", "c5", "c4", "c1", "c2"])

    def test_agency_cap_and_max_five(self):
        cards = [_card(i, "FDA", "Warning Letter") for i in range(4)]
        cards += [_card(10 + i, "FDA", "FDA 483 실사 관찰", type_tag="483", title="무균", n=5) for i in range(3)]
        picked = render.select_must_see(cards)
        self.assertEqual(len(picked), 2, "한 기관은 최대 2건")
        many = [_card(i, ag, "Warning Letter") for i, ag in enumerate(["FDA", "EMA", "MHRA", "WHO", "PMDA", "HC"])]
        self.assertEqual(len(render.select_must_see(many)), render.MUST_SEE_N)

    def test_rows_recalls_and_plain_483_are_not_picked(self):
        cards = [
            _card(0, "MFDS", "GMP실사", weight="row"),
            _card(1, "FDA", "Recall", type_tag="Recall"),
            _card(2, "FDA", "FDA 483 실사 관찰", type_tag="483", title="라벨 관리 미흡", n=12),
        ]
        self.assertEqual(render.select_must_see(cards), [])

    def test_joint_guidance_before_single_agency(self):
        cards = [_card(0, "MFDS", "지침·안내서", title="사용적합성 가이드라인"),
                 _card(1, "PIC/S", "규제 소식", title="PIC/S GMP Guide revision")]
        self.assertEqual([cards[i]["id"] for i in render.select_must_see(cards)], ["c1", "c0"])

    def test_1123_issue_is_pinned(self):
        """11-23 호 실제 데이터 — 규칙을 바꾸면 이 줄도 같이 바꾼다(의도한 변경인지 확인)."""
        path = WEB / "data" / "briefs" / "brief_web_2026_11_23.json"
        if not path.exists():
            self.skipTest("11-23 호 없음")
        cs = _sorted_cards(path)
        self.assertEqual([render._card_anchor(cs[i]) for i in render.select_must_see(cs)],
                         ["0f3a6eeb815d", "admin-2026008255", "gmpinspect-1Q7txc0kHQ0",
                          "fda483-196433", "ef50339bb834"])


class MustSeePublishedBriefsTest(unittest.TestCase):
    def test_every_published_brief_ko_and_en_pick_the_same_cards(self):
        self.assertTrue(BRIEFS)
        ko, en = render.Translator("ko"), render.Translator("en")
        for path in BRIEFS:
            with self.subTest(brief=path.name):
                cs = _sorted_cards(path)
                idx = render.select_must_see(cs)
                self.assertLessEqual(len(idx), render.MUST_SEE_N)
                per = {}
                for i in idx:
                    per[cs[i].get("agency")] = per.get(cs[i].get("agency"), 0) + 1
                    self.assertNotEqual(cs[i].get("card_weight"), "row")
                self.assertLessEqual(max(per.values() or [0]), render.MUST_SEE_PER_AGENCY)
                m_ko = render.build_must_see([render._card_view(c, ko, "ko") for c in cs], cs, ko)
                m_en = render.build_must_see([render._card_view(c, en, "en") for c in cs], cs, en)
                self.assertEqual([m["anchor"] for m in m_ko], [m["anchor"] for m in m_en])
                for m in m_en:
                    self.assertIsNone(re.search(r"[가-힣]", m["label"]), m["label"])
                counts = render.brief_agency_counts(cs, ko)
                self.assertEqual(sum(a["n"] for a in counts), sum(1 for c in cs if c.get("agency")))


class MustSeeTemplateTest(unittest.TestCase):
    def test_built_brief_links_to_cards_and_folds_card_list(self):
        goldens = sorted(GOLDEN.glob("brief_2026-*.expected.html"))
        self.assertTrue(goldens)
        for g in goldens:
            html = g.read_text(encoding="utf-8")
            with self.subTest(golden=g.name):
                self.assertEqual(html.count('<details class="toc-all">'), 1)
                toc = html[html.index('id="toc"'):html.index("</aside>", html.index('id="toc"'))]
                fold = toc[toc.index('<details class="toc-all">'):]
                self.assertEqual(toc.count('<details class="toc-grp"'), fold.count('<details class="toc-grp"'),
                                 "카드 목록은 모두 '전체 보기' 안에")
                self.assertIn('class="toc-ag"', toc[:toc.index('<details class="toc-all">')])
                for a in re.findall(r'class="must-c[^"]*" href="#([^"]+)"', html):
                    self.assertIn(f'id="{a}"', html, f"꼭 볼 칸 #{a} 이 이 쪽 카드로 이어지지 않는다")

    def test_template_is_not_a_dark_band(self):
        src = (WEB / "templates" / "brief.html").read_text(encoding="utf-8")
        rule = re.search(r"\.must-c\{([^}]*)\}", src).group(1)
        self.assertIn("background:#fff", rule)
        self.assertIn("select_must_see", src, "규칙 위치를 템플릿 주석에 남긴다")


if __name__ == "__main__":
    unittest.main()
