"""네이버 블로그 원고 '이번 주 483 한 건 해설' 가드 (2026-11-26 대표 결정).

매 빌드가 briefs/{pub}/naver-blog.txt 를 낸다. 지키는 것
  1. 고르는 483 은 '이번 주 꼭 볼 5건'의 483 칸과 같다(규칙 한 곳 — render.select_must_see).
  2. 문장은 발행된 카드 원문 그대로(요약·관찰·점검), 사이트 원문 링크·브리프 링크·구독 링크가 있다.
  3. HTML 태그가 섞이지 않는 평문이고, 회사 규모를 낮춰 부르는 말이 없다.
  4. 해설할 483 이 없는 주에도 파일은 생긴다(경로 예측 가능).
"""
from __future__ import annotations

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
        txt = render.build_naver_blog_483(cs, views, URL, "2026년 11월 4주차")
        c = cs[i]
        self.assertTrue(txt.startswith(f"제목: FDA 483 해설 | {c['headline_target']} — {c['title_issue']}"))
        self.assertIn(c["summary"], txt)
        for k in c["key_facts"] + c["checks"]:
            self.assertIn(k, txt)
        self.assertIn("https://grm-solutions.com/findings/doc/sample/", txt)
        self.assertIn(URL, txt)
        self.assertIn("https://grm-solutions.com/#subscribe", txt)
        self.assertIn("생성형 AI", txt)
        self.assertIsNone(re.search(r"<[a-z/][^>]*>", txt), "HTML 태그가 섞였다")
        for bad in ("중소", "작은 회사", "영세", "!"):
            self.assertNotIn(bad, txt)


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
            sm = (tmp / "sitemap-full.xml")
            if sm.exists():
                self.assertNotIn(render.NAVER_BLOG_FILE, sm.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
