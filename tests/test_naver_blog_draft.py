"""네이버 블로그 원고 '이번 주 483 한 건 해설' 가드 (2026-11-26 대표 결정).

매 빌드가 briefs/{pub}/naver-blog.txt 를 낸다. 지키는 것
  1. 고르는 483 은 '이번 주 꼭 볼 5건'의 483 칸과 같다(규칙 한 곳 — render.select_must_see).
  2. 문장은 발행된 카드 원문 그대로(요약·관찰·점검), 사이트 원문 링크·브리프 링크·구독 링크가 있다.
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
        txt = render.build_naver_blog_483(cs, views, URL, "2026년 11월 4주차", "2026-11-23")
        c = cs[i]
        self.assertTrue(txt.startswith(f"제목: FDA 483 해설 | {c['headline_target']} — {c['title_issue']}"))
        self.assertIn(c["summary"], txt)
        for k in c["key_facts"] + c["checks"]:
            self.assertIn(k, txt)
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
