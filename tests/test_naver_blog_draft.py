"""네이버 블로그 원고 '이번 주 483 한 건 해설' 가드 (2026-11-26 대표 결정).

매 빌드가 briefs/{pub}/naver-blog.txt 를 낸다. 지키는 것
  1. 고르는 483 은 '이번 주 꼭 볼 5건'의 483 칸과 같다(규칙 한 곳 — render.select_must_see).
  2. [위성 원고 11-26] 사이트 카드의 긴 문단(요약·시사점·핵심 사실)은 다시 붙이지 않는다 — 사실은 카드 사실 표·
     483 관찰 제목·용어사전 풀이에서만(지어낸 사실 0). 사이트 원문 링크·브리프 링크·구독 링크가 있다.
  2b. 규칙(render.naver_blog_checks): hard 는 전 발행호·픽스처 게이트, soft(본문 길이·제목 40자)는 쪽의 점검 목록.
  3. HTML 태그가 섞이지 않는 평문이고, 회사 규모를 낮춰 부르는 말이 없다.
  4. 해설할 483 이 없는 주에도 파일은 생긴다(경로 예측 가능).
  5. 붙여넣기 쪽 naver-blog.html(11-26): 평문과 같은 원고 한 벌에서 나온다(문장·링크 같음), 태그는 p·b·a·br 만,
     noindex 두 겹(meta·_headers), sitemap 비등록, 한국어판만, 복사 단추 둘.
  6. /admin/ '네이버 블로그' 탭이 최신 발행일로 그 쪽을 연다 — 매주 사람이 주소를 챙기지 않는다.
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WEB = REPO / "web"
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / "tests"))
import render  # noqa: E402

BRIEFS = sorted((WEB / "data" / "briefs").glob("brief_web_*.json"))
URL = "https://grm-solutions.com/briefs/x/"


def _sorted_cards(path):
    d = json.loads(path.read_text(encoding="utf-8"))
    cs = [c for c in d.get("cards") or [] if render._is_renderable(c)]
    return sorted(cs, key=lambda c: (c.get("render_order") is None, c.get("render_order")))


class BlogPickTest(unittest.TestCase):
    def test_pick_is_the_must_see_483(self):
        for path in BRIEFS:
            with self.subTest(brief=path.name):
                cs = _sorted_cards(path)
                i = render.pick_blog_483(cs)
                must_483 = [j for j in render.select_must_see(cs) if cs[j].get("type_tag") == "483"]
                if must_483:
                    self.assertEqual(i, must_483[0])
                if i is not None:
                    self.assertEqual(cs[i].get("type_tag"), "483")
                    self.assertNotEqual(cs[i].get("card_weight"), "row")

    def test_no_483_week_still_writes_a_note(self):
        txt = render.build_naver_blog_483([], [], URL, "2026년 1월 1주차")
        self.assertIn("해설할 FDA 483 이 없습니다", txt)
        self.assertIn(URL, txt)


class BlogTextTest(unittest.TestCase):
    def test_text_comes_from_the_card_and_links_back(self):
        path = WEB / "data" / "briefs" / "brief_web_2026_11_23.json"
        if not path.exists():
            self.skipTest("11-23 호 없음")
        cs = _sorted_cards(path)
        views = [render._card_view(c, render._KO, "ko") for c in cs]
        i = render.pick_blog_483(cs)
        views[i] = dict(views[i], doc_href="findings/doc/sample/")
        txt = render.build_naver_blog_483(cs, views, URL, "2026년 11월 4주차", "2026-11-23",
                                          render.load_glossary())
        c = cs[i]
        title = txt.splitlines()[0].removeprefix("제목: ")
        self.assertTrue(title.startswith("FDA 483 해설 · "))
        self.assertIn(c["title_issue"], title)
        self.assertLessEqual(len(title), render.NAVER_BLOG_TITLE_MAX)
        self.assertIn(c["headline_target"], txt, "업체는 제목에서 빠져도 본문에 남는다")
        # [위성 원고 11-26] 사이트 카드의 긴 문단(요약·시사점·핵심 사실)은 다시 붙이지 않는다.
        self.assertNotIn(c["summary"], txt)
        self.assertNotIn(c["implication"], txt)
        for k in c["key_facts"]:
            self.assertNotIn(k, txt)
        for k in c["checks"]:
            self.assertIn(k, txt)
        # 관찰 제목은 483 관찰 데이터(deterministic_detail) 그대로, 번호와 함께.
        for o in c["deterministic_detail"]["observations"]:
            self.assertIn(f"관찰 {o['number']}: {o['deficiency_ko']}", txt)
        self.assertIn("글에 나온 용어", txt)
        self.assertIn("https://grm-solutions.com/findings/doc/sample/", txt)
        self.assertIn(URL, txt)
        self.assertRegex(txt, r"https://grm-solutions\.com/\?utm_source=naver_blog[^\s#]*#subscribe")
        # 블로그에서 나가는 우리 사이트 링크는 전부 블로그 채널 표식(utm.CHANNELS["naver_blog"])을 단다.
        for u in re.findall(r"https://grm-solutions\.com\S*", txt):
            self.assertIn("utm_source=naver_blog&utm_medium=blog&utm_campaign=2026-11-23_483", u)
        self.assertIn("생성형 AI", txt)
        self.assertIsNone(re.search(r"<[a-z/][^>]*>", txt), "HTML 태그가 섞였다")
        for bad in ("중소", "작은 회사", "영세", "!"):
            self.assertNotIn(bad, txt)


FIXTURES = sorted((WEB / "tests" / "fixtures").glob("*/brief_web_*.json"))


def _docs():
    """전 발행호 + 테스트 픽스처 — 빌드와 같은 인자(용어사전·브리프 카드 용어 빈도)로 원고를 짓는다."""
    glossary = render.load_glossary()
    briefs = [json.loads(p.read_text(encoding="utf-8")) for p in BRIEFS]
    freq = render.build_brief_term_card_freq(render.build_doc_term_link_index(glossary), briefs)
    for path in BRIEFS + FIXTURES:
        cs = _sorted_cards(path)
        views = [render._card_view(c, render._KO, "ko") for c in cs]
        views = [dict(v, doc_href="findings/doc/x/") for v in views]
        doc = render.naver_blog_483_doc(cs, views, URL, "d", "2026-11-23", glossary, freq)
        i = render.pick_blog_483(cs)
        yield path.parent.name + "/" + path.name, doc, (cs[i] if i is not None else None), glossary


class BlogRulesGateTest(unittest.TestCase):
    """[네이버 블로그 규칙 11-26] 생성기가 늘 지킬 수 있는 규칙(hard)은 모든 발행호·픽스처에서 지켜져야 한다.
    카드 풍부도에 달린 규칙(본문 길이·제목 40자)은 발행을 막지 않고 붙여넣기 쪽 점검 목록에 △ 로만 보인다."""

    def test_hard_rules_hold_for_every_brief(self):
        n = 0
        for name, doc, card, _g in _docs():
            with self.subTest(brief=name):
                for r in render.naver_blog_checks(doc):
                    if r["hard"]:
                        self.assertTrue(r["ok"], f"{r['name']} {r['value']} (기준 {r['target']})")
                n += card is not None
        self.assertGreaterEqual(n, 15, "483 이 있는 호가 충분히 시험된다")

    def test_soft_rules_are_exactly_length_rules(self):
        doc = {"title": "x", "blocks": [("p", "y")]}
        self.assertEqual({r["name"] for r in render.naver_blog_checks(doc, "") if not r["hard"]},
                         {"제목 길이", "본문 길이"})

    def test_no_invented_facts(self):
        """원고의 사실 항목은 카드 사실 표·483 관찰 데이터·용어사전 풀이에서만 온다."""
        for name, doc, card, glossary in _docs():
            if card is None:
                continue
            easy = {render._nb_clean(t.get("easy_ko")) for t in glossary}
            facts = {render._nb_clean(f.get("value")).removesuffix(" · 483")
                     for f in card.get("facts") or [] if f.get("value")}
            obs = {render._nb_clean(o.get("deficiency_ko"))
                   for o in (card.get("deterministic_detail") or {}).get("observations") or []}
            with self.subTest(brief=name):
                for b in doc["blocks"]:
                    if b[0] != "list":
                        continue
                    for item in b[2]:
                        if b[1] == "문서 기본 정보":
                            v = item.split(": ", 1)[1]
                            self.assertTrue(v in facts or v == render._nb_clean(card["headline_target"])
                                            or v.removesuffix("건").isdigit(), item)
                        elif b[1].startswith("FDA가 적은 관찰사항"):
                            self.assertIn(item.split(": ", 1)[-1], obs, item)
                        elif b[1] == "글에 나온 용어":
                            self.assertIn(item.split(": ", 1)[1], easy, item)
                        elif b[1] == "우리 현장에서 확인할 것":
                            self.assertIn(item, [render._nb_clean(k) for k in card["checks"]])

    def test_checks_catch_violations(self):
        """규칙 함수가 실제로 잡는지 — 위반을 하나씩 심어 본다(뮤테이션)."""
        base = next(d for _n, d, c, _g in _docs() if c is not None and c.get("summary"))
        by = lambda d: {r["name"]: r["ok"] for r in render.naver_blog_checks(d)}  # noqa: E731
        hard = {r["name"] for r in render.naver_blog_checks(base) if r["hard"]}
        self.assertTrue(all(v for k, v in by(base).items() if k in hard), by(base))
        bad = [
            ("마크다운 흔적", dict(base, blocks=base["blocks"] + [("p", "**굵게**")])),
            ("마크다운 흔적", dict(base, blocks=base["blocks"] + [("p", "## 제목")])),
            ("마크다운 흔적", dict(base, blocks=base["blocks"] + [("p", "a | b")])),
            ("보이지 않는 문자", dict(base, blocks=base["blocks"] + [("p", "a​b")])),
            ("사이트 문장 겹침", dict(base, blocks=base["blocks"] + [("p", ln) for ln in base["site_text"].splitlines()])),
            ("제목 앞 15자 안 키워드", dict(base, title="이번 주 실사 소식 정리 · FDA 483")),
            ("첫 문단 키워드", dict(base, blocks=[("p", "안녕하세요.")] + base["blocks"])),
            ("해시태그", dict(base, blocks=[b for b in base["blocks"] if not b[1].startswith("#")])),
            ("사이트 링크", dict(base, blocks=[b for b in base["blocks"] if b[0] != "links"])),
            ("소제목", dict(base, blocks=[b for b in base["blocks"] if b[0] == "p"])),
        ]
        for rule, d in bad:
            with self.subTest(rule=rule):
                self.assertFalse(by(d)[rule])

    def test_clean_strips_invisible_and_markdown(self):
        self.assertEqual(render._nb_clean("## a​ **b** | c\U000e0041"), "a b · c")


class BlogPageParityTest(unittest.TestCase):
    """평문과 붙여넣기 HTML 은 naver_blog_483_doc() 한 벌 — 한쪽에만 있는 문장·링크가 없어야 한다."""

    def _pairs(self):
        for path in BRIEFS:
            cs = _sorted_cards(path)
            views = [render._card_view(c, render._KO, "ko") for c in cs]
            yield path.name, render.naver_blog_483_doc(cs, views, URL, "2026년 1월 1주차"), \
                render.build_naver_blog_483(cs, views, URL, "2026년 1월 1주차")
        yield "empty", render.naver_blog_483_doc([], [], URL, "d"), render.build_naver_blog_483([], [], URL, "d")

    def test_same_sentences_and_links(self):
        for name, doc, txt in self._pairs():
            body = render.build_naver_blog_html(doc)
            with self.subTest(brief=name):
                self.assertEqual(set(re.findall(r"</?([a-z]+)", body)) - {"p", "b", "a", "br"}, set(),
                                 "스마트에디터가 받는 태그만")
                plain = html.unescape(re.sub(r"<[^>]+>", "\n", body))
                for line in txt.splitlines():
                    line = line.removeprefix("제목: ").removeprefix("- ")
                    if not line or line == doc["title"]:
                        continue
                    label = line.split(": http")[0]
                    self.assertIn(label, plain, line)
                for u in re.findall(r"https://\S+", txt):
                    self.assertIn(f'href="{html.escape(u)}"', body)


class BlogBuiltSiteTest(unittest.TestCase):
    def test_every_ko_brief_page_gets_the_file(self):
        import test_render
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_blog_"))
        try:
            test_render._build_single(tmp)
            briefs = sorted((tmp / "briefs").glob("*/index.html"))
            self.assertTrue(briefs)
            for b in briefs:
                f = b.parent / render.NAVER_BLOG_FILE
                self.assertTrue(f.exists(), f)
                self.assertTrue(f.read_text(encoding="utf-8").strip())
            self.assertFalse(list((tmp / "en").glob("briefs/*/" + render.NAVER_BLOG_FILE)),
                             "블로그 원고는 한국어판만")
            for b in briefs:
                pg = b.parent / render.NAVER_BLOG_PAGE
                self.assertTrue(pg.exists(), pg)
                h = pg.read_text(encoding="utf-8")
                self.assertIn('<meta name="robots" content="noindex, nofollow">', h)
                self.assertNotIn('rel="canonical"', h)
                self.assertIn('id="nb-copy-body"', h)
                self.assertIn("text/html", h, "본문은 text/html 로 복사")
                txt = (b.parent / render.NAVER_BLOG_FILE).read_text(encoding="utf-8")
                if txt.startswith("제목: "):
                    self.assertIn('id="nb-copy-title"', h)
                    # 점검 목록(soft 규칙 + 겹침) — 대표가 붙여 넣기 전에 본다. 발행을 막지 않는다.
                    self.assertIn('id="nb-checks"', h)
                    for name in ("제목 길이", "본문 길이", "사이트 문장 겹침"):
                        self.assertIn(name, h)
                    self.assertRegex(h, r"[✓△]</span> 본문 길이 [\d,]+자")
            self.assertFalse(list((tmp / "en").glob("briefs/*/" + render.NAVER_BLOG_PAGE)))
            for sm in (tmp / "sitemap-full.xml", tmp / "sitemap.xml"):
                if sm.exists():
                    s = sm.read_text(encoding="utf-8")
                    self.assertNotIn(render.NAVER_BLOG_FILE, s)
                    self.assertNotIn("naver-blog", s)
            headers = (tmp / "_headers").read_text(encoding="utf-8")
            self.assertIn(f"/briefs/:date/{render.NAVER_BLOG_PAGE}\n  X-Robots-Tag: noindex", headers)
            self.assertIn("/briefs/:date/naver-blog\n  X-Robots-Tag: noindex", headers)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BlogHomeTest(unittest.TestCase):
    def test_write_shortcut_points_at_the_grm_blog(self):
        self.assertEqual(render.NAVER_BLOG_WRITE_URL, "https://blog.naver.com/grmsolution/postwrite")
        tpl = (WEB / "templates" / "naver_blog.html").read_text(encoding="utf-8")
        self.assertIn("blog_write_url", tpl)
        admin = (WEB / "templates" / "admin.html").read_text(encoding="utf-8")
        self.assertIn(f'href="{render.NAVER_BLOG_WRITE_URL}"', admin)


class BlogAdminTabTest(unittest.TestCase):
    """매주 주소가 저절로 나온다 — /admin/ 탭이 최신 발행일(state.latest.date)로 그 쪽을 연다."""

    def test_admin_tab_opens_the_latest_page(self):
        js = (WEB / "assets" / "admin.js").read_text(encoding="utf-8")
        tpl = (WEB / "templates" / "admin.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="naverblog"', tpl)
        self.assertIn('data-panel="naverblog"', tpl)
        self.assertIn('id="grm-nb-open"', tpl)
        fn = js[js.index("function renderNaverBlog"):]
        fn = fn[:fn.index("\n  }\n") + 4]
        self.assertIn("state.latest.date", fn)
        self.assertIn(f'"{render.NAVER_BLOG_PAGE}"', fn)
        self.assertIn(f'"{render.NAVER_BLOG_FILE}"', fn)
        self.assertIn('split("\\n")', fn, "제목 줄은 첫 줄")
        self.assertIn("renderNaverBlog();", js[js.index("renderLinkedin();"):][:80],
                      "최신호를 읽은 뒤 링크드인 탭과 함께 채운다")


if __name__ == "__main__":
    unittest.main()
