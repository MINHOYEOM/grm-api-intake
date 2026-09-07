#!/usr/bin/env python3
"""링크드인 카드뉴스 빌더 테스트 — 순수 층만(Chrome·네트워크 0).

CI(`unittest discover -s tests`)는 `tests/test_web_linkedin_cards.py` shim 으로 순회. 직접:
  python web/tests/test_linkedin_cards.py

픽스처는 실제 발행본 `brief_web_2026_09_07.json`(불변 발행 데이터) + 용어사전 정본. 단언은 값이
아니라 **성질**로 한다(제목 2줄 이내·본문 한 줄 폭·용어가 실제 등장·결정론·이스케이프) — 데이터가
자라도 낡지 않게. 합성 브리프로는 '없는 것은 안 낸다'(경고서한·실사 결과 슬라이드 생략)를 본다.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
import linkedin_cards as lc  # noqa: E402

BRIEF = WEB_DIR / "data" / "briefs" / "brief_web_2026_09_07.json"
GLOSSARY = WEB_DIR / "data" / "glossary.json"


def _load():
    return (json.loads(BRIEF.read_text(encoding="utf-8")),
            json.loads(GLOSSARY.read_text(encoding="utf-8")))


def _synthetic(cards: list[dict], tldr: list[str] | None = None) -> dict:
    return {"schema_version": "grm-web-card/v1",
            "brief": {"publish_date": "2026-09-14", "window": "2026-09-07 ~ 2026-09-14",
                      "agencies": ["FDA"], "tldr": tldr or []},
            "cards": cards}


def _card(i: int, **over) -> dict:
    base = {"id": f"c{i}", "render_order": i, "group": "글로벌", "agency": "FDA", "category": "Other",
            "signal_tier": 2, "headline_target": f"Firm {i} Inc.", "title_issue": "무균구역 구획 미흡",
            "summary": "요약.", "key_facts": ["사유: 무균공정 구역 구획 미흡", "발행 부서/일자: CDER · 09/01/2026"],
            "implication": "첫 문장이다. 둘째 문장이다.", "checks": ["무균공정 구역 구획 근거"]}
    base.update(over)
    return base


class TextUtilTest(unittest.TestCase):
    def test_split_two_balances_at_space_or_middle_dot(self):
        self.assertEqual(lc.split_two("짧은 제목"), ["짧은 제목"])
        left, right = lc.split_two("주사제 엔도톡신 규격 이탈")
        self.assertEqual((left, right), ("주사제 엔도톡신", "규격 이탈"))
        # 공백과 가운뎃점 중 두 줄 폭 차가 더 작은 쪽(4.0 vs 9.35 < 9.65 vs 4.0)
        self.assertEqual(lc.split_two("무균구역 미생물오염·교차오염"), ["무균구역", "미생물오염·교차오염"])

    def test_split_two_without_break_point_keeps_one_line(self):
        self.assertEqual(lc.split_two("가나다라마바사아자차카타파하"), ["가나다라마바사아자차카타파하"])

    def test_fit_size_shrinks_long_single_line_but_not_below_floor(self):
        self.assertEqual(lc.fit_size(["짧다"], 78), 78)
        self.assertLess(lc.fit_size(["가나다라마바사아자차카타파하가나다라"], 78), 78)
        self.assertGreaterEqual(lc.fit_size(["가" * 80], 78), 48)

    def test_first_sentence_stops_at_first_da_period(self):
        self.assertEqual(lc.first_sentence("첫 문장이다. 둘째 문장이다."), "첫 문장이다.")
        self.assertEqual(lc.first_sentence("마침표 없는 문장"), "마침표 없는 문장")

    def test_parse_fact_label_value(self):
        self.assertEqual(lc.parse_fact("회수 등급: Class I"), ("회수 등급", "Class I"))
        self.assertIsNone(lc.parse_fact("라벨 없는 문장"))
        self.assertIsNone(lc.parse_fact("라벨:   "))

    def test_class1_regex_does_not_match_class_ii(self):
        self.assertTrue(lc.CLASS1.search("회수 등급: Class I"))
        self.assertFalse(lc.CLASS1.search("회수 등급: Class II"))
        self.assertFalse(lc.CLASS1.search("Class III"))

    def test_window_and_week_label(self):
        self.assertEqual(lc.window_label("2026-08-31 ~ 2026-09-07"), "2026. 8. 31 – 9. 7")
        self.assertEqual(lc.title_dateform("2026-09-07"), (2026, 9, 1))
        self.assertEqual(lc.title_dateform("2026-09-14"), (2026, 9, 2))

    def test_firm_short_strips_korean_corp_marker_before_paren_split(self):
        # '(주)코아팜바이오' 를 괄호에서 자르면 빈 문자열이 된다 — 법인 표기를 먼저 뗀다
        self.assertEqual(lc._firm_short("(주)코아팜바이오"), "코아팜바이오")
        self.assertEqual(lc._firm_short("Fresenius Medical Care AG & Co. KGaA", 10.0), "Fresenius Medical")
        self.assertEqual(lc._firm_short("PReye, LLC"), "PReye")


class BuildDeckRealBriefTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.brief, cls.glossary = _load()
        cls.deck = lc.build_deck(cls.brief, cls.glossary)
        cls.html = lc.render_html(cls.deck)

    def test_cover_first_closing_last_and_kinds(self):
        kinds = [s["kind"] for s in self.deck["slides"]]
        self.assertEqual(kinds[0], "cover")
        self.assertEqual(kinds[-1], "closing")
        self.assertEqual(kinds.count("headline"), 3)
        for k in ("themes", "rows", "glossary", "checks"):
            self.assertIn(k, kinds, f"2026-09-07 발행본에는 {k} 슬라이드가 있어야 한다")
        self.assertEqual(len(kinds), 9)

    def test_page_numbers_are_sequential(self):
        self.assertEqual([s["idx"] for s in self.deck["slides"]], list(range(1, 10)))
        self.assertTrue(all(s["total"] == 9 for s in self.deck["slides"]))

    def test_headlines_two_lines_or_fewer_and_short(self):
        for s in self.deck["slides"]:
            self.assertTrue(1 <= len(s["h1"]) <= 2, s["h1"])
            for line in s["h1"]:
                self.assertLessEqual(lc.text_width(line), 15.5, line)

    def test_headline_cards_follow_tldr_order(self):
        firms = [dict(s["rows"]).get("업체", "") for s in self.deck["slides"] if s["kind"] == "headline"]
        self.assertIn("Victory", firms[0])
        self.assertIn("Liebel", firms[1])       # tldr 는 제품명(Optiray)으로 부른다 — 제품 토큰 매칭
        self.assertIn("미래바이오", firms[2])

    def test_headline_kind_chips(self):
        chips = [s["chips"] for s in self.deck["slides"] if s["kind"] == "headline"]
        self.assertEqual(chips[0], ["FDA", "Class I 회수"])
        self.assertEqual(chips[2], ["식약처", "제조업무정지"])

    def test_cover_tiles_counts(self):
        tiles = dict((lab, n) for n, lab in self.deck["slides"][0]["tiles"])
        self.assertEqual(tiles["규제 신호 카드"], "33")
        self.assertEqual(tiles["FDA Class I 회수"], "3")
        self.assertEqual(tiles["FDA 경고서한"], "5")
        self.assertEqual(len(self.deck["slides"][0]["tiles"]), 4)

    def test_warning_letter_theme_slide(self):
        s = next(x for x in self.deck["slides"] if x["kind"] == "themes")
        self.assertEqual(s["h1"][0], "경고서한 5건,")
        self.assertEqual(s["bars"][0][0], "무균공정 · 오염 방지")
        self.assertEqual(len(s["rows"]), 5)
        for firm, chips in s["rows"]:
            self.assertLessEqual(lc.text_width(firm), 10.0, firm)
            self.assertTrue(1 <= len(chips) <= 2)

    def test_mfds_inspection_rows_have_names(self):
        s = next(x for x in self.deck["slides"] if x["kind"] == "rows")
        names = [f for f, _ in s["rows"]]
        self.assertEqual(len(names), 6)
        self.assertTrue(all(names), names)                 # '(주)…' 가 빈 이름으로 떨어지지 않는다
        self.assertIn("코아팜바이오", names)

    def test_glossary_terms_are_specific_and_present_this_week(self):
        s = next(x for x in self.deck["slides"] if x["kind"] == "glossary")
        self.assertEqual(len(s["terms"]), 5)
        kos = [t["ko"] for t in s["terms"]]
        for ko in kos:
            self.assertNotIn(ko, lc.GLOSSARY_STOP, ko)
        self.assertTrue({"내독소", "교차오염"} & set(kos), kos)
        for t in s["terms"]:
            self.assertEqual(len(t["lines"]), 1)           # 정의는 자연 줄바꿈(강제 <br> 0)
            self.assertIn("<svg", t["mini"])

    def test_checks_prefer_headline_cards_and_fit_one_line(self):
        s = next(x for x in self.deck["slides"] if x["kind"] == "checks")
        self.assertEqual(len(s["checks"]), 6)
        self.assertIn("주사용수·원료 내독소 관리 한도 설정 근거", s["checks"][0:2])
        for c in s["checks"]:
            self.assertLessEqual(lc.text_width(c), 34.0, c)
        self.assertEqual(len(set(s["checks"])), 6)

    def test_caption_one_idea_per_line_mobile_width(self):
        cap = self.deck["caption"]
        lines = cap.splitlines()
        self.assertEqual(lines[0], "이번 주 규제 소식, 카드 9장.")
        self.assertIn("https://grm-solutions.com/briefs/2026-09-07/", lines)
        for ln in lines:
            if ln.startswith("http") or ln.startswith("#"):
                continue
            self.assertLessEqual(lc.text_width(ln), 26.0, ln)
        self.assertTrue(lines[-1].startswith("#") and lines[-2].startswith("#"))
        self.assertIn("· Class I 회수 3건", lines)
        self.assertIn("· 경고서한 5건 — 무균공정·일탈·시험기록", lines)

    def test_doc_title_and_url(self):
        self.assertEqual(self.deck["doc_title"], "9월 1주차 규제 소식")
        self.assertEqual(self.deck["url"], "https://grm-solutions.com/briefs/2026-09-07/")

    def test_ai_notice_on_every_slide_and_source_notes(self):
        self.assertEqual(self.html.count(lc.AI_NOTE), 9)
        self.assertIn("출처: FDA 공식 공고", self.html)
        self.assertIn("출처: 식약처 공식 공고", self.html)

    def test_html_has_nine_page_sections_and_print_css(self):
        self.assertEqual(self.html.count('<section class="slide'), 9)
        self.assertIn("@page{size:1080px 1350px;margin:0}", self.html)
        self.assertIn("pretendard", self.html)              # CDN 폰트(CI 러너엔 한글 폰트가 없다)
        self.assertNotIn("<img", self.html)                 # 외부 이미지 0 — 전부 인라인 SVG/데이터 URI

    def test_deterministic(self):
        brief, glossary = _load()
        again = lc.build_deck(brief, glossary)
        self.assertEqual(json.dumps(again, ensure_ascii=False, sort_keys=True),
                         json.dumps(self.deck, ensure_ascii=False, sort_keys=True))
        self.assertEqual(lc.render_html(again), self.html)

    def test_anon_replaces_firm_names_everywhere(self):
        deck = lc.build_deck(self.brief, self.glossary, anon=True)
        html_text = lc.render_html(deck)
        for name in ("Victory Medical", "Liebel-Flarsheim", "미래바이오", "PReye", "코아팜"):
            self.assertNotIn(name, html_text, name)
        self.assertIn("업체 A", html_text)

    def test_source_has_no_clock_or_randomness(self):
        src = (WEB_DIR / "linkedin_cards.py").read_text(encoding="utf-8")
        self.assertNotIn("datetime", src)
        self.assertNotIn("import random", src)
        self.assertNotIn("time.time(", src)


class BuildDeckSyntheticTest(unittest.TestCase):
    def test_without_warning_letters_or_inspections_those_slides_are_skipped(self):
        brief = _synthetic([_card(1, group="Recall", key_facts=["사유: 이물", "회수 등급: Class II"]),
                            _card(2), _card(3)])
        deck = lc.build_deck(brief, [])
        kinds = [s["kind"] for s in deck["slides"]]
        self.assertNotIn("themes", kinds)
        self.assertNotIn("rows", kinds)
        self.assertNotIn("glossary", kinds)               # 사전이 비면 용어 장도 없다
        self.assertEqual(kinds[0], "cover")
        self.assertEqual(kinds[-1], "closing")
        tiles = dict((lab, n) for n, lab in deck["slides"][0]["tiles"])
        self.assertNotIn("FDA Class I 회수", tiles)          # Class II 는 Class I 로 세지 않는다
        self.assertEqual(tiles["회수 신호"], "1")

    def test_headline_fallback_uses_signal_tier_when_tldr_empty(self):
        brief = _synthetic([_card(1, signal_tier=1), _card(2, signal_tier=3), _card(3, signal_tier=2)])
        deck = lc.build_deck(brief, [])
        firms = [dict(s["rows"])["업체"] for s in deck["slides"] if s["kind"] == "headline"]
        self.assertEqual(firms[0], "Firm 2 Inc.")

    def test_user_text_is_html_escaped(self):
        brief = _synthetic([_card(1, title_issue="<script>alert(1)</script> 지적", headline_target="A & B <Co>")])
        html_text = lc.render_html(lc.build_deck(brief, []))
        self.assertNotIn("<script>", html_text)
        self.assertIn("&lt;script&gt;", html_text)
        self.assertIn("A &amp; B &lt;Co&gt;", html_text)

    def test_facts_skip_publication_line_and_cap_three(self):
        brief = _synthetic([_card(1, key_facts=["a: 1", "b: 2", "c: 3", "d: 4", "발행 부서/일자: X"])])
        s = next(x for x in lc.build_deck(brief, [])["slides"] if x["kind"] == "headline")
        self.assertEqual([k for k, _ in s["rows"]], ["a", "b", "c", "업체"])


class CliTest(unittest.TestCase):
    def test_no_pdf_path_writes_txt_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "dist"
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = lc.main(["--data", str(BRIEF.parent), "--brief", str(BRIEF), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf", "--html"])
            self.assertEqual(rc, 0)
            d = out / "briefs" / "2026-09-07"
            self.assertTrue((d / "linkedin.txt").exists())
            self.assertTrue((d / "linkedin.html").exists())
            self.assertFalse((d / "linkedin.pdf").exists())
            self.assertIn("9장", buf.getvalue())

    def test_missing_brief_dir_is_a_clean_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = lc.main(["--data", str(pathlib.Path(tmp) / "nope"), "--out", str(pathlib.Path(tmp) / "d")])
            self.assertEqual(rc, 0)

    def test_latest_brief_path_picks_newest_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("brief_web_2026_08_31.json", "brief_web_2026_09_07.json", "brief_web_2026_06_22.json"):
                (pathlib.Path(tmp) / name).write_text("{}", encoding="utf-8")
            self.assertEqual(lc.latest_brief_path(pathlib.Path(tmp)).name, "brief_web_2026_09_07.json")
            self.assertIsNone(lc.latest_brief_path(pathlib.Path(tmp) / "empty"))


if __name__ == "__main__":
    unittest.main()
