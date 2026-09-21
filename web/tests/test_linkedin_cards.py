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
import re
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


HANGUL = re.compile(r"[가-힣]")


class EnglishDeckTest(unittest.TestCase):
    """[영문판 2026-09-15] 같은 주 소식을 영어로도 낸다. 단언은 성질로 —
    ① 영문 덱에는 한글이 한 조각도 없다(화면에 나가는 HTML·본문·문서 제목 전부),
    ② 두 덱은 **같은 항목**을 말한다(선별은 한국어 정본으로 하므로),
    ③ 한국 업체명은 로마자로 지어내지 않는다 — 못 쓰면 그 줄이 없다."""

    @classmethod
    def setUpClass(cls):
        doc, gl = _load()
        cls.doc, cls.gl = doc, gl
        cls.ko = lc.build_deck(doc, gl, lang="ko")
        cls.en = lc.build_deck(doc, gl, lang="en")

    def test_english_deck_has_no_hangul_anywhere_on_screen(self):
        shipped = lc.render_html(self.en) + self.en["caption"] + self.en["doc_title"]
        found = HANGUL.findall(shipped)
        self.assertEqual(found, [], f"영문 덱에 한글 {found[:10]}")

    def test_korean_deck_still_korean(self):
        self.assertTrue(HANGUL.search(lc.render_html(self.ko)))
        self.assertTrue(self.ko["caption"].startswith("이번 주 규제 소식"))

    def test_both_languages_carry_the_same_items(self):
        kinds_ko = [s["kind"] for s in self.ko["slides"]]
        kinds_en = [s["kind"] for s in self.en["slides"]]
        # 식약처 묶음 장(rows)만 영문에서 빠진다 — 업체명 목록이 본체라서.
        self.assertEqual([k for k in kinds_ko if k != "rows"], kinds_en)
        self.assertEqual(kinds_ko.count("headline"), kinds_en.count("headline"))
        gl_ko = next(s for s in self.ko["slides"] if s["kind"] == "glossary")
        gl_en = next(s for s in self.en["slides"] if s["kind"] == "glossary")
        self.assertEqual(len(gl_ko["terms"]), len(gl_en["terms"]))
        ck_ko = next(s for s in self.ko["slides"] if s["kind"] == "checks")
        ck_en = next(s for s in self.en["slides"] if s["kind"] == "checks")
        self.assertEqual(len(ck_ko["checks"]), len(ck_en["checks"]))

    def test_english_headline_rows_are_actually_parsed(self):
        """'Observation 1:' 은 라벨이 13자라 한국어 상한(12)에 걸려 **표가 통째로 비었다**.
        영문 헤드라인 장은 적어도 한 줄은 들고 있어야 한다."""
        for s in (x for x in self.en["slides"] if x["kind"] == "headline"):
            with self.subTest(h1=s["h1"]):
                self.assertTrue(s["rows"], "영문 헤드라인 표가 비었다")
                self.assertTrue(all(v.strip() for _, v in s["rows"]))

    def test_english_implication_is_one_sentence(self):
        for s in (x for x in self.en["slides"] if x["kind"] == "headline" and x.get("impl")):
            with self.subTest(h1=s["h1"]):
                # 마침표 뒤 공백+대문자가 또 있으면 한 문장으로 안 끊긴 것
                self.assertIsNone(re.search(r"[a-z0-9)]\.\s+[A-Z]", s["impl"]), s["impl"])

    def test_korean_company_names_never_romanised_in_english(self):
        card = {"id": "x1", "render_order": 1, "agency": "MFDS", "group": "Recall",
                "category": "Recall", "headline_target": "(주)네오메디칼제약",
                "title_issue": "치약 질산칼륨 함량 부적합", "summary": "실사 결과",
                "key_facts": ["제품: 치약"], "implication": "국내 회수다.", "checks": ["함량시험 기록"],
                "en": {"title_issue": "Toothpaste assay failure", "summary": "MFDS ordered a recall.",
                       "key_facts": ["Product: toothpaste"], "implication": "A domestic recall.",
                       "checks": ["Assay records"]}}
        doc = _synthetic([card], tldr=["(주)네오메디칼제약 회수"])
        ko = lc.build_deck(doc, self.gl, lang="ko")
        en = lc.build_deck(doc, self.gl, lang="en")
        ko_head = next(s for s in ko["slides"] if s["kind"] == "headline")
        en_head = next(s for s in en["slides"] if s["kind"] == "headline")
        self.assertIn("(주)네오메디칼제약", [v for _, v in ko_head["rows"]])
        labels = [lab for lab, _ in en_head["rows"]]
        self.assertNotIn("Company", labels, "이름을 못 쓰는데 업체 줄이 남았다")
        self.assertEqual(HANGUL.findall(lc.render_html(en)), [])

    def test_mini_labels_fill_every_slot_in_every_language(self):
        """라벨 사전이 그림보다 낡으면 KeyError 로 죽는다 — 두 언어 전부를 실제로 채워 본다."""
        for fig_id in lc.MINI_SVG:
            for lang in lc.LANGS:
                with self.subTest(fig=fig_id, lang=lang):
                    svg = lc.mini(fig_id, lang)
                    self.assertNotIn("{", svg, "채우지 않은 슬롯이 남았다")
                    self.assertEqual(svg.count("<svg"), 1)
                    if lang != "ko":
                        self.assertEqual(HANGUL.findall(svg), [])
        self.assertEqual(lc.mini("없는-용어", "en"), lc.mini("_generic", "en"))

    def test_deck_and_site_carry_the_same_figures(self):
        """그림은 덱(MINI_SVG)과 사이트(partials/glossary_fig/*.html) 두 벌로 산다.
        한쪽에만 그리면 다른 쪽은 조용히 문서 아이콘으로 돌아간다 — 2026-09-21 에
        실제로 그랬다(덱에만 있던 deviation). 이름이 아니라 **두 집합의 차이**로 잰다."""
        fig_dir = pathlib.Path(lc.WEB_DIR) / "partials" / "glossary_fig"
        site = {p.stem for p in fig_dir.glob("*.html")}
        deck = set(lc.MINI_SVG) - {"_generic"}      # _generic 은 폴백이라 사이트에 없다
        self.assertEqual(deck - site, set(), "덱에만 있는 그림 — 사이트 partial 이 없다")
        self.assertEqual(site - deck, set(), "사이트에만 있는 그림 — 덱 MINI_SVG 가 없다")

    def test_every_figure_points_at_a_real_glossary_term(self):
        """그림 id 는 정본 용어 id 여야 한다 — 오타 하나면 영영 안 뜨는 그림이 된다."""
        terms = {t["id"] for t in json.loads(
            (pathlib.Path(lc.WEB_DIR) / "data" / "glossary.json").read_text(encoding="utf-8"))}
        orphans = sorted((set(lc.MINI_SVG) - {"_generic"}) - terms)
        self.assertEqual(orphans, [], f"정본에 없는 용어의 그림: {orphans}")

    def test_tie_is_broken_by_headline_evidence_not_glossary_order(self):
        """같은 점수면 **그 주 헤드라인이 실제로 다룬 말**이 이긴다.
        예전 규칙은 동점을 '사전 등재 순서'로 갈랐다 — 편집과 무관한 기준이라, 그림을
        늘리자 헤드라인 용어가 사전 앞쪽 용어에 밀려났다(2026-09-21 무균공정 → 완제품).
        여기서는 **등재 순서를 일부러 거꾸로** 두어, 순서가 아니라 근거가 이기는지 본다."""
        glossary = [
            # 사전 앞쪽 = 예전 규칙이라면 무조건 이기는 자리. 헤드라인 근거는 없다.
            {"id": "early-term", "term_ko": "완제품테스트", "term_en": "Early", "easy_ko": "정의", "easy_en": "def"},
            # 사전 뒤쪽이지만 헤드라인 카드의 구조화 칸(title_issue)에 뜬다.
            {"id": "late-term", "term_ko": "무균공정테스트", "term_en": "Late", "easy_ko": "정의", "easy_en": "def"},
        ]
        # 실측으로 5점 동점을 만든 구성(2026-09-21):
        #   late-term  = 헤드라인 구조화 칸 강신호 1회        → 5, head_strong=1
        #   early-term = 헤드라인 본문 1회(3) + 비헤드라인 강신호 1회(2) → 5, head_strong=0
        # 카드 5장이라 둘 다 40% 감쇠에 걸리지 않는다(df 2/5, 1/5).
        head = {"id": "H", "title_issue": "무균공정테스트 관리 미흡",
                "summary": "완제품테스트 항목도 함께 살폈다"}
        cards = [head,
                 {"id": "A", "title_issue": "완제품테스트 관련", "summary": ""},
                 {"id": "B", "title_issue": "", "summary": ""},
                 {"id": "C", "title_issue": "", "summary": ""},
                 {"id": "D", "title_issue": "", "summary": ""}]

        # 둘 다 점수가 살아 있어야 동점 비교가 성립한다 — 각각 혼자 두면 반드시 뽑힌다.
        for t in glossary:
            with self.subTest(alone=t["id"]):
                self.assertEqual([x["id"] for x in lc.pick_glossary_terms([t], cards, 1,
                                                                          headline_cards=[head])],
                                 [t["id"]], "점수가 0이라 후보도 못 된다 — 픽스처가 낡았다")

        # 핵심: 사전 등재 순서를 뒤집어도 **헤드라인 근거가 있는 쪽**이 이긴다.
        # 예전 규칙(동점 → 사전 순서)이라면 [early, late] 에서 early 가 이겼다.
        for order, why in (([glossary[0], glossary[1]], "사전에 early 가 먼저"),
                           ([glossary[1], glossary[0]], "사전에 late 가 먼저")):
            with self.subTest(order=why):
                self.assertEqual([t["id"] for t in lc.pick_glossary_terms(order, cards, 1,
                                                                          headline_cards=[head])],
                                 ["late-term"],
                                 f"{why} — 동점인데 등재 순서가 이겼다. 헤드라인 근거를 먼저 봐야 한다")

    def test_unknown_language_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            lc.build_deck(self.doc, self.gl, lang="fr")


class EnglishCliTest(unittest.TestCase):
    def test_cli_writes_both_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "dist"
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = lc.main(["--brief", str(BRIEF), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf"])
            self.assertEqual(rc, 0)
            d = out / "briefs" / "2026-09-07"
            for name in ("linkedin.txt", "linkedin_en.txt"):
                self.assertTrue((d / name).exists(), name)
            self.assertEqual(HANGUL.findall((d / "linkedin_en.txt").read_text(encoding="utf-8")), [])

    def test_brief_without_english_skips_english_with_a_warning(self):
        """옛 브리프에는 `en` 블록이 없다 — 껍데기 덱을 조용히 내보내지 않는다."""
        card = {"id": "k1", "render_order": 1, "agency": "FDA", "category": "Warning Letter",
                "headline_target": "Acme Inc", "title_issue": "무균공정 미흡", "summary": "경고서한",
                "key_facts": ["제품: 주사제"], "implication": "무균공정이 문제다.", "checks": ["무균 기록"]}
        with tempfile.TemporaryDirectory() as tmp:
            brief = pathlib.Path(tmp) / "brief_web_2026_09_14.json"
            brief.write_text(json.dumps(_synthetic([card, dict(card, id="k2", render_order=2)]),
                                        ensure_ascii=False), encoding="utf-8")
            out = pathlib.Path(tmp) / "dist"
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = lc.main(["--brief", str(brief), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf"])
            self.assertEqual(rc, 0)
            d = out / "briefs" / "2026-09-14"
            self.assertTrue((d / "linkedin.txt").exists())
            self.assertFalse((d / "linkedin_en.txt").exists())
            self.assertIn("::warning::linkedin_en 건너뜀", err.getvalue())


if __name__ == "__main__":
    unittest.main()
