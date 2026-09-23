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


def _month_brief(pub: str, cards: list[dict]) -> dict:
    """[마케팅 2026-09-23 L-06] `_synthetic` 의 발행일 가변판 — 월간 덱은 발행일이 다른
    여러 호를 한 번에 넘겨야 하는데 `_synthetic` 은 발행일이 고정이다."""
    return {"schema_version": "grm-web-card/v1",
            "brief": {"publish_date": pub, "window": f"{pub} ~ {pub}", "agencies": ["FDA"], "tldr": []},
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
        # [마케팅 2026-09-23] 기본 캡션은 이제 caption_style="one" — 종전 요약형은
        # summary 로 명시해야 나온다. URL 줄은 UTM 이 붙은 caption_url 로 바뀌었다.
        deck = lc.build_deck(self.brief, self.glossary, caption_style="summary")
        cap = deck["caption"]
        lines = cap.splitlines()
        self.assertEqual(lines[0], "이번 주 규제 소식, 카드 9장.")
        self.assertEqual(deck["caption_url"],
                         "https://grm-solutions.com/briefs/2026-09-07/"
                         "?utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly")
        self.assertIn(deck["caption_url"], lines)
        self.assertEqual(deck["url"], "https://grm-solutions.com/briefs/2026-09-07/")   # 슬라이드 URL 은 깨끗하게
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


class EnglishPluralTest(unittest.TestCase):
    """영문 문구표에 복수형 's' 를 박아 두면 1건일 때 "1 inspection results" 가 나간다.
    2026-09-21 링크드인 영문 캡션에 실제로 그렇게 나갔다(식약처 실사 1곳).

    판정은 **고친 문구 목록이 아니라 성질**로 한다 — 캡션·제목 어디에도 "1 <복수명사>" 가
    없어야 한다. 새 문구가 늘어도 이 검사는 낡지 않는다."""

    # 복수 명사는 "1" 바로 뒤가 아니라 **명사구 끝**에 온다("1 inspection results").
    # 그래서 1 다음에 낱말을 최대 두 개까지 건너뛰고, 그 다음 낱말이 s 로 끝나는지 본다.
    # 마지막 낱말만 **소문자**로 제한한다 — 안 그러면 "1 Class I recall" 의 `Class` 가
    # s 로 끝나 거짓 경보가 난다(실제로 났다). 우리 복수 명사는 전부 소문자다.
    # 앞의 `(?<![0-9])` 가 없으면 "21 items" 속의 "1 items" 를 잡는다.
    # 쉼표·마침표에서 멈추므로 "All 1 item, with links…" 의 links 는 걸리지 않는다.
    ONE_PLURAL = re.compile(r"(?<![0-9])1(?: [A-Za-z]+){0,2} [a-z]+s\b")

    def _one_of_everything(self):
        """모든 갈래를 정확히 1건씩 만든다 — 복수형이 틀릴 수 있는 자리를 전부 켠다."""
        return _synthetic([
            _card(1, group="Recall", key_facts=["사유: 이물", "회수 등급: Class I"]),
            _card(2, category="Warning Letter"),
            _card(3, agency="MFDS", summary="실사 결과 공개", key_facts=["사유: 기준서 미준수"]),
            _card(4, agency="MFDS", summary="제조업무정지 1개월", key_facts=["사유: 제조업무정지"]),
        ])

    def test_regex_catches_a_real_one_plural_but_not_lookalikes(self):
        """가드 자신을 먼저 시험한다 — 안 그러면 아무것도 못 잡는 정규식이 조용히 초록이 된다
        (2026-09-22 실제로 첫 판이 그랬다: 복수형이 두 번째 낱말이라 하나도 안 걸렸다)."""
        for bad in ("· 1 inspection results", "· 1 administrative actions",
                    "1 warning letters,", "· 1 Class I recalls", "1 site inspecteds"):
            with self.subTest(bad=bad):
                self.assertTrue(self.ONE_PLURAL.search(bad), bad)
        for ok in ("· 1 inspection result", "· 1 administrative action",
                   "1 warning letter,", "· 1 Class I recall", "All 21 items",
                   "All 1 item, with links to the originals", "2 inspection results"):
            with self.subTest(ok=ok):
                self.assertIsNone(self.ONE_PLURAL.search(ok), ok)

    def test_english_caption_has_no_one_plural(self):
        brief = self._one_of_everything()
        gl = json.loads(GLOSSARY.read_text(encoding="utf-8"))
        cap = lc.build_deck(brief, gl, lang="en")["caption"]
        bad = self.ONE_PLURAL.findall(cap)
        self.assertEqual(bad, [], "1건인데 복수형이 나갔다: %r\n---\n%s" % (bad, cap))

    def test_english_slide_headlines_have_no_one_plural(self):
        brief = self._one_of_everything()
        deck = lc.build_deck(brief, json.loads(GLOSSARY.read_text(encoding="utf-8")), lang="en")
        for s in deck["slides"]:
            for line in s.get("h1", []):
                with self.subTest(kind=s["kind"], line=line):
                    self.assertEqual(self.ONE_PLURAL.findall(line), [])

    def test_plural_marker_still_pluralises_above_one(self):
        """1 만 고치고 2 를 망가뜨리면 안 된다 — 2건이면 's' 가 붙어야 한다."""
        self.assertEqual(lc.nfmt("{n} inspection result{s}", 1), "1 inspection result")
        self.assertEqual(lc.nfmt("{n} inspection result{s}", 2), "2 inspection results")
        self.assertEqual(lc.nfmt("실사 결과 {n}곳", 1), "실사 결과 1곳")   # 한국어는 영향 없음


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
        # [마케팅 2026-09-23] 기본 캡션(one)은 고정 문구로 시작하지 않는다 — 성질(한글 포함)로 본다.
        self.assertTrue(HANGUL.search(lc.render_html(self.ko)))
        self.assertTrue(HANGUL.search(self.ko["caption"]))

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

    # ── [영문 정의 잘림 2026-09-22] 카드는 "정의를 그대로 가져왔다"고 적어 둔다. 그래 놓고
    # CSS 클램프가 문장 한가운데를 "…" 로 끊으면 그 문구가 거짓이 된다.
    #
    # 판정은 **줄 수가 아니라 폭**으로 한다 — 줄바꿈은 브라우저가 하고 단어 경계 때문에 들쭉
    # 날쭉해서(실측 줄당 20.5~26.4em) 파이썬이 줄 수를 맞힐 수 없다. 칸 기하에서 예산을 낸다:
    #   .slide 1080px - padding(88×2) = 904px
    #   .gl .g grid = 200px + 16 + 1fr + 16 + 150px  →  본문 칸 1fr = 522px
    #   .gl .g p font-size 21px                      →  한 줄 약 522/21 = 24.86 em
    #
    # ★이 예산은 **증명이 아니라 어림**이다. `text_width` 의 라틴 0.56em 가정이 이 글꼴의
    # 실제 평균보다 커서 **폭을 높게 잡는다** — 클램프 4줄 기준으로 돌려 보면 5건을 잡는데
    # 브라우저 실측으로 실제 넘친 것은 2건뿐이었다(returned-product 는 예산을 7% 넘고도
    # 4줄에 들어갔다). 그러니:
    #   · 통과 = "들어간다"는 보장이 아니다.
    #   · 실패 = "거의 확실히 잘린다" — 브라우저로 확인하고 정의를 줄이거나 클램프를 올려라.
    # 엄격한 쪽으로 틀리므로 조용한 잘림을 놓치는 일은 드물다. 그게 이 검사의 목적이다.
    EN_COLUMN_EM = 522 / 21

    def _en_clamp_lines(self) -> int:
        m = re.search(r"\.lang-en \.gl \.g p\{-webkit-line-clamp:(\d+)\}", lc.CSS)
        self.assertIsNotNone(m, "영문 클램프 규칙을 못 찾았다 — CSS 가 바뀌었다")
        return int(m.group(1))

    def test_no_english_definition_can_exceed_the_card_clamp(self):
        lines = self._en_clamp_lines()
        budget = lines * self.EN_COLUMN_EM
        terms = json.loads((pathlib.Path(lc.WEB_DIR) / "data" / "glossary.json")
                           .read_text(encoding="utf-8"))
        too_wide = []
        for t in terms:
            en = " ".join(str(t.get("easy_en") or "").split())
            if en and lc.text_width(en) > budget:
                too_wide.append((t["id"], round(lc.text_width(en), 1)))
        self.assertEqual(too_wide, [],
                         f"영문 정의가 {lines}줄 예산({budget:.0f}em)을 넘는다 — 카드에서 "
                         f"문장 한가운데가 잘릴 값이다. 브라우저로 확인하고 정의를 줄이거나 "
                         f"클램프를 올려라: {too_wide}")

    def test_clamp_budget_actually_rejects_an_over_long_definition(self):
        """상한이 무엇도 거르지 못하는 값이면 위 검사는 영원히 초록이다 — 실제로 거르는지 본다."""
        budget = self._en_clamp_lines() * self.EN_COLUMN_EM
        fits = "A short definition."
        never = "word " * 200
        self.assertLess(lc.text_width(fits), budget)
        self.assertGreater(lc.text_width(never), budget)

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


class CaptionStyleTest(unittest.TestCase):
    """[마케팅 2026-09-23 계획 L-02] 게시 본문 두 형식 — `one`(기본, '이번 주 한 건')과
    `summary`(종전 요약형). 기대값은 픽스처를 직접 읽어 **파생**한다(하드코딩 추측 금지) —
    `pick_headline_cards`·`_card_text`·`_card_list` 로 프로덕션과 같은 규칙을 재현한다."""

    @classmethod
    def setUpClass(cls):
        cls.brief, cls.glossary = _load()
        cls.cards = sorted(cls.brief["cards"], key=lambda c: int(c.get("render_order") or 0))

    def _expected_one_card(self, lang: str = "ko"):
        """`_caption_one` 의 선택 규칙(시사점+점검 모두 있는 첫 카드, 없으면 시사점만 있는
        첫 카드)을 공개 헬퍼로 재현 — 내부 함수를 그대로 부르면 자기 자신을 시험하는
        동어반복이 된다."""
        heads = lc.pick_headline_cards(self.brief["brief"], self.cards, 3)
        if lang != "ko":
            heads = [c for c in heads if lc.has_en(c)]
        with_checks = [c for c in heads if lc._card_text(c, "implication", lang).strip()
                       and lc._card_list(c, "checks", lang)]
        if with_checks:
            return with_checks[0]
        with_impl = [c for c in heads if lc._card_text(c, "implication", lang).strip()]
        return with_impl[0] if with_impl else None

    def test_one_is_the_default_caption_style(self):
        default = lc.build_deck(self.brief, self.glossary)
        explicit = lc.build_deck(self.brief, self.glossary, caption_style="one")
        self.assertEqual(default["caption"], explicit["caption"])

    def test_one_caption_first_lines_are_headline_card_title_wrapped(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        card = self._expected_one_card("ko")
        self.assertIsNotNone(card, "픽스처에 시사점 있는 헤드라인 카드가 없다 — 가정이 낡았다")
        expected = lc.wrap_width(lc._card_text(card, "title_issue", "ko"))
        self.assertEqual(deck["caption"].splitlines()[: len(expected)], expected)

    def test_one_caption_checks_section_matches_card_checks(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        card = self._expected_one_card("ko")
        cks = lc._card_list(card, "checks", "ko")
        lines = deck["caption"].splitlines()
        self.assertTrue(cks, "픽스처 카드에 점검이 없다 — '점검 있음' 갈래를 시험하지 못한다")
        self.assertIn("현장 점검 포인트", lines)
        bullets = [ln for ln in lines if ln.startswith("• ")]
        self.assertEqual(len(bullets), min(2, len(cks)))

    def test_one_caption_has_utm_url_on_its_own_line(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        lines = deck["caption"].splitlines()
        self.assertIn(deck["caption_url"], lines)
        self.assertIn("utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly",
                      deck["caption_url"])
        self.assertNotIn("?", deck["url"])            # 슬라이드 URL 은 깨끗하게

    def test_one_caption_lines_fit_mobile_width(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        lines = deck["caption"].splitlines()
        for ln in lines:
            if ln.startswith("http") or ln.startswith("#"):
                continue
            self.assertLessEqual(lc.text_width(ln), 26.0, ln)

    def test_one_caption_last_line_is_hashtags(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        non_blank = [ln for ln in deck["caption"].splitlines() if ln]
        self.assertTrue(non_blank[-1].startswith("#"), non_blank[-1])

    def test_one_caption_is_deterministic(self):
        a = lc.build_deck(self.brief, self.glossary, caption_style="one")["caption"]
        b = lc.build_deck(self.brief, self.glossary, caption_style="one")["caption"]
        self.assertEqual(a, b)

    def test_english_one_caption_has_no_hangul_and_uses_english_headings(self):
        deck = lc.build_deck(self.brief, self.glossary, lang="en", caption_style="one")
        self.assertEqual(HANGUL.findall(deck["caption"]), [])
        self.assertIn("What to check on site", deck["caption"])
        self.assertIn("Official sources and the full weekly brief", deck["caption"])
        for ln in deck["caption"].splitlines():
            if ln.startswith("http") or ln.startswith("#"):
                continue
            self.assertLessEqual(lc.text_width(ln), 26.0, ln)

    def test_closing_slide_url_stays_clean_and_deck_url_unchanged(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        closing = next(s for s in deck["slides"] if s["kind"] == "closing")
        self.assertNotIn("?", closing["url"])
        self.assertEqual(deck["url"], "https://grm-solutions.com/briefs/2026-09-07/")

    def test_summary_caption_url_line_carries_utm(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="summary")
        lines = deck["caption"].splitlines()
        self.assertIn(deck["caption_url"], lines)
        self.assertTrue(deck["caption_url"].startswith(
            "https://grm-solutions.com/briefs/2026-09-07/"
            "?utm_source=linkedin&utm_medium=social&utm_campaign=2026-09-07_weekly"))

    def test_unknown_caption_style_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            lc.build_deck(self.brief, self.glossary, caption_style="fancy")

    def test_fallback_to_summary_when_no_headline_card_has_implication(self):
        """헤드라인 카드 전부 시사점이 비면 '한 건' 캡션을 못 만든다 — 조용히 빈 본문을
        내는 대신 종전 요약형으로 물러선다."""
        brief = _synthetic([_card(1, implication="", checks=[]),
                            _card(2, implication="", checks=[]),
                            _card(3, implication="", checks=[])])
        one = lc.build_deck(brief, [], caption_style="one")
        summary = lc.build_deck(brief, [], caption_style="summary")
        self.assertEqual(one["caption"], summary["caption"])

    def test_cli_default_writes_one_and_flag_writes_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_default = pathlib.Path(tmp) / "default"
            out_summary = pathlib.Path(tmp) / "summary"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                lc.main(["--brief", str(BRIEF), "--glossary", str(GLOSSARY),
                        "--out", str(out_default), "--no-pdf"])
                lc.main(["--brief", str(BRIEF), "--glossary", str(GLOSSARY),
                        "--out", str(out_summary), "--no-pdf", "--caption", "summary"])
            txt_default = (out_default / "briefs" / "2026-09-07" / "linkedin.txt").read_text(encoding="utf-8")
            txt_summary = (out_summary / "briefs" / "2026-09-07" / "linkedin.txt").read_text(encoding="utf-8")
            self.assertNotEqual(txt_default, txt_summary)
            self.assertTrue(txt_summary.startswith("이번 주 규제 소식"))
            self.assertFalse(txt_default.startswith("이번 주 규제 소식"))

    # ── [09-23 반려 피드백 재수정] '지적 1/지적 2' 다줄 사실 → 한 줄 헤더 + 선택적 한 줄 ──────

    def test_one_caption_has_compact_fact_header_instead_of_multiline_citations(self):
        """경고서한 카드는 실제로 '지적 1/지적 2' 처럼 폭을 훌쩍 넘는 key_facts 를 가진다 —
        그 카드로 직접 캡션을 만들어 그 문구가 **전혀** 안 나오는지 본다(감싸서 죽이는 게
        아니라 애초에 안 고른다)."""
        wl = next(c for c in self.cards if c.get("category") == "Warning Letter")
        self.assertTrue(any(f.startswith(("지적", "관찰사항")) for f in wl.get("key_facts") or []),
                        "픽스처 가정이 낡았다 — 경고서한 카드에 '지적 N:' 사실이 없다")
        cap = lc._caption_one([wl], "https://x/?utm", "ko", lambda c: c.get("headline_target", ""))
        self.assertIsNotNone(cap)
        for ln in cap.splitlines():
            self.assertFalse(re.match(r"^(지적|관찰사항|Citation|Observation)\s*\d*\s*[:：]", ln), ln)

    def test_one_caption_fact_header_line_present_and_fits_width(self):
        deck = lc.build_deck(self.brief, self.glossary, caption_style="one")
        card = self._expected_one_card("ko")
        agency, kind = lc._agency(card), lc._kind_chip(card, "ko")
        lines = deck["caption"].splitlines()
        header = next((ln for ln in lines if ln.startswith(agency + " · ")), None)
        self.assertIsNotNone(header, lines)
        self.assertIn(kind, header)
        self.assertLessEqual(lc.text_width(header), 26.0, header)

    def test_fact_header_drops_firm_before_date_when_too_wide(self):
        long_firm = "A Very Extremely Long Pharmaceutical Manufacturing Corporation Name Ltd"
        card = {"agency": "FDA", "category": "Warning Letter", "headline_target": long_firm,
                "key_facts": ["발행 부서/일자: Center for Drug Evaluation and Research (CDER) · 08/18/2026"]}
        header = lc._fact_header(card, "ko", lambda c: c.get("headline_target", ""))
        self.assertLessEqual(lc.text_width(header), 26.0, header)
        self.assertNotIn(long_firm, header)             # 업체명이 먼저 빠진다
        self.assertIn("08/18/2026", header)              # 날짜는 남는다

    def test_fact_header_omits_missing_date_without_inventing_one(self):
        card = {"agency": "FDA", "group": "Recall", "category": "Recall", "headline_target": "Acme",
                "key_facts": ["회수 등급: Class II"]}   # Class II — Class I 회수 칩으로 안 갈린다
        header = lc._fact_header(card, "ko", lambda c: c.get("headline_target", ""))
        self.assertEqual(header, "FDA · 회수 · Acme")

    def test_one_extra_fact_skips_facts_that_do_not_fit_unwrapped(self):
        card = {"key_facts": ["지적 1: " + "무균공정 구역 오염 방지 절차 미수립 " * 5,
                              "회수 등급: Class I"]}
        extra = lc._one_extra_fact(card, "ko")
        self.assertEqual(extra, "회수 등급: Class I")   # 긴 지적문은 건너뛰고 짧은 사실을 쓴다

    def test_one_extra_fact_empty_when_nothing_fits(self):
        card = {"key_facts": ["지적 1: " + "무균공정 구역 오염 방지 절차 미수립 " * 5]}
        self.assertEqual(lc._one_extra_fact(card, "ko"), "")


class WrapWidthBalanceTest(unittest.TestCase):
    """[09-23 반려 피드백 재수정] wrap_width 균형 배분 — 그리디(앞줄을 상한까지 욱여넣고
    마지막 줄에 짧은 나머지를 남김)가 "문장을 잘못 끊는다"는 반려의 실제 원인이었다. 여기서는
    균형 배분 자체를(실제 카드 문구·합성 문구 양쪽으로) 단위 시험한다 — `_caption_one` 통합
    시험과 달리 픽스처가 바뀌어도 낡지 않는다."""

    SAMPLES = [
        "무균공정 구역에서 오염·혼동을 막기 위한 충분한 크기의 구획과 관리체계 미비",
        "조제 주사제의 내독소 규격 이탈은 무균성뿐 아니라 용수·원료의 내독소 부하 관리 "
        "실패를 함께 가리키는 지표다.",
        "failure to perform operations within specifically defined areas of adequate "
        "size and to have controls necessary to prevent contamination or mix-ups in "
        "aseptic processing areas",
        "An endotoxin excursion in a compounded injection points to failed control of "
        "endotoxin load in water and materials, not only to sterility.",
        "one two three four five six seven eight nine ten eleven twelve thirteen",
    ]

    def test_single_line_when_it_already_fits(self):
        self.assertEqual(lc.wrap_width("짧은 문장"), ["짧은 문장"])
        self.assertEqual(lc.wrap_width(""), [])
        self.assertEqual(lc.wrap_width("   "), [])

    def test_lines_never_exceed_the_cap(self):
        for t in self.SAMPLES:
            for line in lc.wrap_width(t, 26.0):
                with self.subTest(t=t, line=line):
                    self.assertLessEqual(lc.text_width(line), 26.0, line)

    def test_never_drops_or_invents_characters(self):
        for t in self.SAMPLES:
            lines = lc.wrap_width(t, 26.0)
            with self.subTest(t=t):
                self.assertEqual(re.sub(r"\s+", "", "".join(lines)), re.sub(r"\s+", "", t))

    def test_balances_lines_within_40_percent_of_the_longest(self):
        """N≥2 줄일 때 가장 짧은 줄이 가장 긴 줄의 40% 아래로 떨어지지 않는다."""
        checked_multiline = 0
        for t in self.SAMPLES:
            lines = lc.wrap_width(t, 26.0)
            if len(lines) < 2:
                continue
            checked_multiline += 1
            widths = [lc.text_width(ln) for ln in lines]
            with self.subTest(t=t, widths=widths):
                self.assertGreaterEqual(min(widths), 0.4 * max(widths))
        self.assertGreater(checked_multiline, 0, "샘플 전부 한 줄이라 균형 배분을 시험 못 했다")

    def test_balanced_beats_naive_greedy_on_a_real_lopsided_case(self):
        """2026-09-21 실사례 — 그리디는 마지막 줄에 '절차'(2.0 폭)만 남겼다(최댓값의 8%)."""
        t = "ISO 5 환경·인원 모니터링 이탈 시 균 동정 및 추세 조사 절차"
        cuts = lc._wrap_cuts(t, 26.0)
        greedy_offsets = lc._greedy_wrap(t, cuts, 26.0)
        prev, greedy_lines = 0, []
        for cut in greedy_offsets:
            greedy_lines.append(t[prev:cut].strip())
            prev = cut
        greedy_widths = [lc.text_width(ln) for ln in greedy_lines]
        self.assertLess(min(greedy_widths), 0.4 * max(greedy_widths),
                        "그리디 자체가 더는 치우치지 않는다 — 대조 표본이 낡았다")

        balanced = lc.wrap_width(t, 26.0)
        balanced_widths = [lc.text_width(ln) for ln in balanced]
        self.assertGreaterEqual(len(balanced), 2)
        self.assertGreaterEqual(min(balanced_widths), 0.4 * max(balanced_widths),
                                (balanced, balanced_widths))

    def test_cannot_break_a_single_overlong_token(self):
        """끊을 자리가 아예 없는 한 덩어리는 상한을 넘긴 채 한 줄로 낸다 — 없는 공백을
        만들 수는 없다(문장을 지어내거나 글자를 버리지 않는다)."""
        t = "a" * 80
        self.assertEqual(lc.wrap_width(t, 26.0), [t])


class MonthItemSelectionTest(unittest.TestCase):
    """[마케팅 2026-09-23 L-06] `pick_month_items` — 합성 데이터로 규칙 자체를 시험한다
    (호마다 첫 헤드라인 → 모자라면 최신 호부터 다음 헤드라인으로 라운드로빈 → 중복은
    id 나 제목 어느 한쪽만 같아도 스킵). 실데이터 대조는 아래 BuildMonthDeckRealDataTest."""

    def test_one_per_brief_then_round_robin_fill_from_newest(self):
        # 호 셋, 호마다 카드 둘(신호도差로 어느 쪽이 '첫 헤드라인'인지 결정론 고정).
        briefs = [
            _month_brief("2026-05-04", [_card(1, title_issue="A1", signal_tier=3, render_order=1),
                                        _card(2, title_issue="A2", signal_tier=1, render_order=2)]),
            _month_brief("2026-05-11", [_card(3, title_issue="B1", signal_tier=3, render_order=1),
                                        _card(4, title_issue="B2", signal_tier=1, render_order=2)]),
            _month_brief("2026-05-18", [_card(5, title_issue="C1", signal_tier=3, render_order=1),
                                        _card(6, title_issue="C2", signal_tier=1, render_order=2)]),
        ]
        items = lc.pick_month_items(briefs, "ko", 5)
        got = [(c["title_issue"], pub, rank) for c, pub, rank in items]
        # 1차: 호마다 첫 헤드라인(A1·B1·C1). 2차: 5장 채우려 **최신 호부터** 두 번째 헤드라인을
        # 한 바퀴(C2 다음 B2) — A2 는 이미 5장을 채워 차례가 오지 않는다.
        self.assertEqual(got, [
            ("A1", "2026-05-04", 0),
            ("B1", "2026-05-11", 0), ("B2", "2026-05-11", 1),
            ("C1", "2026-05-18", 0), ("C2", "2026-05-18", 1),
        ])
        self.assertNotIn("A2", [t for t, _, _ in got], "5장이 다 찼는데 더 채웠다")

    def test_no_duplicates_when_fewer_briefs_than_the_limit(self):
        briefs = [_month_brief("2026-05-04", [_card(1, signal_tier=3, render_order=1)])]
        items = lc.pick_month_items(briefs, "ko", 5)
        self.assertEqual(len(items), 1, "카드가 하나뿐인데 지어내 채우면 안 된다")

    def test_duplicate_skipped_by_id_or_by_title_either_one(self):
        briefs = [
            _month_brief("2026-05-04", [_card(1, id="dup-id", title_issue="같은 제목", signal_tier=3, render_order=1),
                                        _card(2, id="a2", title_issue="원본B", signal_tier=1, render_order=2)]),
            _month_brief("2026-05-11", [_card(3, id="dup-id", title_issue="다른 제목", signal_tier=3, render_order=1),
                                        _card(4, id="b4", title_issue="같은 제목", signal_tier=1, render_order=2)]),
        ]
        items = lc.pick_month_items(briefs, "ko", 5)
        ids = [c["id"] for c, _, _ in items]
        titles = [c["title_issue"] for c, _, _ in items]
        self.assertEqual(ids.count("dup-id"), 1, "같은 id 가 두 번 실렸다(2호의 dup-id 는 id 로 걸러야 한다)")
        self.assertEqual(titles.count("같은 제목"), 1, "같은 제목이 두 번 실렸다(2호의 b4 는 제목으로 걸러야 한다)")
        self.assertNotIn("b4", ids, "제목이 겹치는데도 실렸다")

    def test_english_drops_cards_without_en_block(self):
        en_card = _card(1, title_issue="영문 있음", signal_tier=3, render_order=1,
                        en={"title_issue": "EN title", "summary": "EN summary",
                            "key_facts": ["Fact: x"], "implication": "EN impl.", "checks": ["EN check"]})
        ko_only = _card(2, title_issue="영문 없음", signal_tier=1, render_order=2)   # en 블록 없음
        briefs = [_month_brief("2026-05-04", [en_card, ko_only])]
        items_en = lc.pick_month_items(briefs, "en", 5)
        self.assertEqual(len(items_en), 1)
        self.assertEqual(items_en[0][0]["id"], "c1")
        items_ko = lc.pick_month_items(briefs, "ko", 5)
        self.assertEqual(len(items_ko), 2, "국문은 en 블록 유무와 무관하게 둘 다 실려야 한다")


class BuildMonthDeckRealDataTest(unittest.TestCase):
    """[마케팅 2026-09-23 L-06] 실제 발행본 전체로 월간 덱을 짓는다 — 값이 아니라 성질로
    단언한다(브리프가 늘어도 안 낡게). 픽스처는 `web/data/briefs/*.json` 전체."""

    @classmethod
    def setUpClass(cls):
        cls.all_briefs = [json.loads(p.read_text(encoding="utf-8"))
                          for p in sorted((WEB_DIR / "data" / "briefs").glob("brief_web_*.json"))]
        cls.glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for b in cls.all_briefs:
            pub = str(b.get("brief", {}).get("publish_date") or "")
            if pub:
                counts[pub[:7]] = counts.get(pub[:7], 0) + 1
        # 발행본이 2건 이상인 달을 고른다 — 라운드로빈 채움·필터링을 실측으로 보려면
        # 한 호짜리 달로는 아무것도 증명 못 한다.
        cls.month = max(counts, key=lambda mo: counts[mo])
        cls.issues_in_month = counts[cls.month]
        cls.deck = lc.build_month_deck(cls.all_briefs, cls.glossary, cls.month)

    def _month_briefs(self) -> list[dict]:
        return [b for b in self.all_briefs
                if str(b.get("brief", {}).get("publish_date") or "").startswith(self.month + "-")]

    def test_month_filtering_by_publish_date_prefix(self):
        expected_cards = sum(len(b.get("cards") or []) for b in self._month_briefs())
        other_month_cards = sum(len(b.get("cards") or []) for b in self.all_briefs) - expected_cards
        self.assertGreater(other_month_cards, 0, "픽스처에 다른 달 데이터가 없어 필터링을 실측 못 한다")
        closing = next(s for s in self.deck["slides"] if s["kind"] == "closing")
        self.assertIn(str(expected_cards), closing["h1"][0],
                     "마무리 장의 총 건수가 그 달 카드 수와 다르다 — 달 밖 카드가 샜다")

    def test_slide_count_equals_items_plus_three(self):
        n_items = sum(1 for s in self.deck["slides"] if s["kind"] == "headline")
        self.assertGreater(n_items, 0)
        self.assertLessEqual(n_items, 5)
        self.assertEqual(len(self.deck["slides"]), n_items + 3)
        self.assertEqual(self.deck["slides"][0]["kind"], "cover")
        self.assertEqual(self.deck["slides"][-2]["kind"], "numbers")
        self.assertEqual(self.deck["slides"][-1]["kind"], "closing")

    def test_cover_and_number_slide_texts(self):
        cover = self.deck["slides"][0]
        n_items = sum(1 for s in self.deck["slides"] if s["kind"] == "headline")
        m = int(self.month.split("-")[1])
        self.assertEqual(cover["eyebrow"], "월간 규제 결산")
        self.assertIn(f"{m}월", "".join(cover["h1"]))
        self.assertIn(str(n_items), "".join(cover["h1"]))
        self.assertFalse(cover["tiles"], "월간 표지는 통계 타일을 두지 않는다 — 숫자 장과 중복")
        self.assertIn(f"{self.issues_in_month}호", cover["sub"])
        numbers = next(s for s in self.deck["slides"] if s["kind"] == "numbers")
        self.assertGreater(len(numbers["bars"]), 0)
        self.assertLessEqual(len(numbers["bars"]), 4, "상위 4개까지만")
        counts = [c for _, c in numbers["bars"]]
        self.assertEqual(counts, sorted(counts, reverse=True), "많은 순이 아니다")
        self.assertIn(f"{m}월", numbers["cap"])

    def test_caption_lines_fit_mobile_width_and_utm(self):
        lines = self.deck["caption"].splitlines()
        for ln in lines:
            if ln.startswith("http") or ln.startswith("#"):
                continue
            self.assertLessEqual(lc.text_width(ln), 26.0, ln)
        self.assertIn(self.deck["caption_url"], lines)
        self.assertIn(f"utm_campaign=monthly_{self.month}", self.deck["caption_url"])
        self.assertIn("utm_source=linkedin&utm_medium=social", self.deck["caption_url"])
        self.assertTrue([ln for ln in lines if ln][-1].startswith("#"))

    def test_deterministic(self):
        again = lc.build_month_deck(self.all_briefs, self.glossary, self.month)
        self.assertEqual(json.dumps(again, ensure_ascii=False, sort_keys=True),
                         json.dumps(self.deck, ensure_ascii=False, sort_keys=True))
        self.assertEqual(lc.render_html(again), lc.render_html(self.deck))

    def test_unknown_month_format_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            lc.build_month_deck(self.all_briefs, self.glossary, "2026-9")

    def test_empty_month_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            lc.build_month_deck(self.all_briefs, self.glossary, "1999-01")

    def test_unknown_language_is_refused_loudly(self):
        with self.assertRaises(ValueError):
            lc.build_month_deck(self.all_briefs, self.glossary, self.month, lang="fr")


class MonthlyDeckEnglishTest(unittest.TestCase):
    """[마케팅 2026-09-23 L-06] 영문 월간 덱 — 화면에 나가는 글자에 한글이 한 조각도
    없어야 한다(카드 유형 집계처럼 원문이 한글뿐인 값은 그 항목만 빠진다)."""

    HANGUL = re.compile(r"[가-힣]")

    @classmethod
    def setUpClass(cls):
        cls.all_briefs = [json.loads(p.read_text(encoding="utf-8"))
                          for p in sorted((WEB_DIR / "data" / "briefs").glob("brief_web_*.json"))]
        cls.glossary = json.loads(GLOSSARY.read_text(encoding="utf-8"))
        counts: dict[str, int] = {}
        for b in cls.all_briefs:
            pub = str(b.get("brief", {}).get("publish_date") or "")
            if pub:
                counts[pub[:7]] = counts.get(pub[:7], 0) + 1
        cls.month = max(counts, key=lambda mo: counts[mo])
        cls.deck = lc.build_month_deck(cls.all_briefs, cls.glossary, cls.month, lang="en")

    def test_no_hangul_anywhere_on_screen(self):
        shipped = lc.render_html(self.deck) + self.deck["caption"] + self.deck["doc_title"]
        found = self.HANGUL.findall(shipped)
        self.assertEqual(found, [], f"영문 월간 덱에 한글 {found[:10]}")

    def test_card_type_bars_never_carry_hangul(self):
        numbers = next(s for s in self.deck["slides"] if s["kind"] == "numbers")
        for label, _ in numbers["bars"]:
            self.assertEqual(self.HANGUL.findall(label), [], label)

    def test_url_uses_english_archive_when_it_exists(self):
        # 이 저장소의 실측 데이터는 영문으로 낼 수 있는 호가 있으므로 `/en/archive/` 가 있어야 한다.
        self.assertTrue(any(lc.render.brief_has_english(b) for b in self.all_briefs),
                        "픽스처에 영문 브리프가 없다 — 이 시험의 전제가 낡았다")
        self.assertEqual(self.deck["url"], "https://grm-solutions.com/en/archive/")
        self.assertIn("https://grm-solutions.com/en/archive/", self.deck["caption_url"])


class MonthlyCliTest(unittest.TestCase):
    """[마케팅 2026-09-23 L-06] CLI `--month`/`--months auto`."""

    def test_months_auto_produces_latest_and_previous_month(self):
        all_briefs = [json.loads(p.read_text(encoding="utf-8"))
                     for p in sorted((WEB_DIR / "data" / "briefs").glob("brief_web_*.json"))]
        pubs = sorted(str(b.get("brief", {}).get("publish_date") or "") for b in all_briefs)
        pubs = [p for p in pubs if p]
        latest_y, latest_m = int(pubs[-1][:4]), int(pubs[-1][5:7])
        cur = f"{latest_y:04d}-{latest_m:02d}"
        py, pm = (latest_y, latest_m - 1) if latest_m > 1 else (latest_y - 1, 12)
        prev = f"{py:04d}-{pm:02d}"
        self.assertEqual(lc.auto_months(all_briefs), [prev, cur])
        # 실데이터 전제(2026-09-23 기준: 9월·8월 둘 다 발행본이 있다) — 낡으면 이 assert 가 먼저 죽는다.
        self.assertIn("2026-09", [prev, cur])
        self.assertIn("2026-08", [prev, cur])

    def test_cli_months_auto_writes_two_months_two_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "dist"
            buf, err = io.StringIO(), io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err):
                rc = lc.main(["--data", str(BRIEF.parent), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf", "--months", "auto"])
            self.assertEqual(rc, 0)
            months = [p.name for p in (out / "monthly").iterdir()]
            self.assertEqual(len(months), 2, months)
            for month in months:
                self.assertTrue((out / "monthly" / month / "linkedin.txt").exists())
                self.assertTrue((out / "monthly" / month / "linkedin_en.txt").exists())
            self.assertFalse((out / "briefs").exists(), "주간 브리프 경로가 생기면 안 된다(월간 전용 실행)")

    def test_cli_explicit_month_writes_only_that_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "dist"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = lc.main(["--data", str(BRIEF.parent), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf", "--month", "2026-09", "--lang", "ko"])
            self.assertEqual(rc, 0)
            self.assertTrue((out / "monthly" / "2026-09" / "linkedin.txt").exists())
            self.assertFalse((out / "monthly" / "2026-09" / "linkedin_en.txt").exists())

    def test_cli_month_with_no_data_is_a_clean_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "dist"
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                rc = lc.main(["--data", str(BRIEF.parent), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf", "--month", "1999-01"])
            self.assertEqual(rc, 0)
            self.assertIn("::warning::1999-01 건너뜀", err.getvalue())
            self.assertFalse(out.exists())

    def test_weekly_behaviour_unchanged_when_neither_flag_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "dist"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = lc.main(["--data", str(BRIEF.parent), "--brief", str(BRIEF), "--glossary", str(GLOSSARY),
                              "--out", str(out), "--no-pdf"])
            self.assertEqual(rc, 0)
            self.assertTrue((out / "briefs" / "2026-09-07" / "linkedin.txt").exists())
            self.assertFalse((out / "monthly").exists())


if __name__ == "__main__":
    unittest.main()
