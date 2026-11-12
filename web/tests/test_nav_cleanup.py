#!/usr/bin/env python3
"""[메뉴 3개·찾아보기·전역 검색 2026-11-12 · 대표 확정안] 상단 메뉴·찾아보기·푸터·'다음으로 볼 것'.

  · 상단 nav: 주간 브리프 / 찾아보기 / 관심 목록(반응 계층이 켜진 빌드만) — "지적사항" 메뉴 없음
  · 탭 3묶음(찾기·경향·점검, #1533)은 폐기 — 어느 면에도 `fnd-seg` 가 없다
  · 찾아보기(/browse/): 옛 탭이 잇던 면을 전부 잇고, 링크는 전부 실재하는 페이지로
  · 헤더 검색칸 → /search/(noindex·sitemap 제외), 색인 assets/lookup*.json
  · 푸터 '찾아보기' 열 · 옛 둘러보기 주소(/findings/browse/)는 /browse/ 로 301
  · 다음으로 볼 것: 용어 낱장·문서·업체 페이지에만, 링크는 전부 실재하는 페이지로

CI 는 `tests/test_web_nav_cleanup.py` shim 으로 순회한다. 직접:
  python web/tests/test_nav_cleanup.py
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(WEB_DIR.parent))
import render  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"

# 옛 탭 3묶음이 잇던 면 — 이제 찾아보기가 잇는다(nav 의 '찾아보기'가 켜진다).
ZONE_PAGES = [
    "findings/index.html", "findings/docs/index.html", "findings/c/index.html",
    "findings/agency/index.html", "findings/firm/index.html",
    "findings/import-alerts/index.html", "findings/inspector/index.html",
    "findings/trends/index.html", "findings/citations/index.html",
    "findings/citations/firms/index.html", "findings/inspections/index.html",
    "findings/coverage/index.html", "findings/examples/index.html",
    "findings/examples/pmda/index.html", "findings/examples/mhra/index.html",
    "findings/checklist/index.html",
]
# 찾아보기가 반드시 잇는 면(옛 탭의 화면 전부 + 용어사전·자료실·이용안내).
BROWSE_MUST = [
    "findings/firm/", "findings/inspector/", "glossary/", "findings/trends/",
    "findings/c/", "findings/inspections/", "findings/docs/", "findings/",
    "findings/checklist/", "findings/coverage/", "library/", "guide/",
]
STRIP_H = "다음으로 볼 것"


def _nav(html: str) -> tuple[str, str]:
    m = re.search(r'<nav id="navmenu">(.*?)</nav>', html, re.S)
    assert m, "navmenu 없음"
    tabs, _sep, more = m.group(1).partition('<span class="navmore">')
    return tabs, more


def _labels(fragment: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", t).strip()
            for t in re.findall(r"<a [^>]*>(.*?)</a>", fragment, re.S)]


def _fnd_col(html: str, h5: str = "찾아보기") -> str:
    i = html.index(f'<div class="foot-fnd"><h5>{h5}</h5>')
    return html[i:html.index("</div>", i)]


def _strip(html: str) -> str | None:
    i = html.find('<aside class="wrap grm-next"')
    return None if i < 0 else html[i:html.index("</aside>", i)]


class NavCleanupRenderedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_nav1_"))
        cls.out = cls._tmp / "site"
        render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _read(self, rel: str) -> str:
        return (self.out / rel).read_text(encoding="utf-8")

    def _all_pages(self):
        return sorted(self.out.rglob("index.html"))

    # ── 상단 nav ─────────────────────────────────────────────────────────
    def test_nav_is_brief_browse_watchlist(self):
        for rel in ("index.html", "archive/index.html", "findings/trends/index.html",
                    "glossary/index.html", "browse/index.html"):
            tabs, more = _nav(self._read(rel))
            self.assertNotIn("이번 주 소식", tabs + more, rel)
            # 반응 계층(관심 목록)은 로컬·CI 빌드에서 꺼져 있다.
            self.assertEqual(_labels(tabs), ["주간 브리프", "찾아보기"], rel)
            self.assertNotIn("지적사항", tabs, rel)
            self.assertNotIn("주간 퀴즈", tabs + more, rel)

    def test_zone_pages_light_browse_tab(self):
        for rel in ZONE_PAGES + ["glossary/index.html", "library/index.html", "browse/index.html"]:
            tabs, _ = _nav(self._read(rel))
            self.assertIn('class="on">찾아보기</a>', tabs, rel)

    def test_archive_top_links_latest_issue(self):
        html = self._read("archive/index.html")
        m = re.search(r'<a class="arc-now[^"]*"[^>]*href="([^"]+)"', html)
        self.assertIsNotNone(m, "아카이브 맨 위 '이번 주 브리프' 블록이 없다")
        self.assertLess(html.index('class="arc-now'), html.index('id="static-issues"'))
        latest = re.search(r'<a class="issue latest" href="([^"]+)"', html)
        self.assertEqual(m.group(1), latest.group(1))
        self.assertTrue((self.out / "archive" / m.group(1)).resolve().is_file())

    # ── 탭 3묶음 폐기 · 찾아보기 ─────────────────────────────────────────
    def test_no_segment_tabs_anywhere(self):
        for p in self._all_pages():
            html = p.read_text(encoding="utf-8")
            self.assertNotIn('class="fnd-seg"', html, str(p))

    def test_browse_links_every_former_tab_target(self):
        for root in ("", "en/"):
            html = self._read(root + "browse/index.html")
            main = html[html.index('<main class="bw">'):html.index("</main>")]
            here = (self.out / root / "browse")
            dests = {here.joinpath(h).resolve() for h in re.findall(r'href="([^"]+)"', main)
                     if not h.startswith("#")}
            for d in dests:
                self.assertTrue(d.is_file(), f"{root}browse → {d}")
            for must in BROWSE_MUST:
                target = (self.out / root / must / "index.html").resolve()
                if root and not target.is_file():
                    continue  # 영어 트리에 없는 면은 광고하지 않는다
                self.assertIn(target, dests, f"{root}browse 에 {must} 없음")

    def test_facet_views_kept_on_facet_pages(self):
        html = self._read("findings/c/index.html")
        self.assertIn('class="fnd-views"', html)

    def test_browse_redirect_and_sitemap(self):
        self.assertFalse((self.out / "findings" / "browse").exists())
        sm = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        self.assertNotIn("findings/browse/", sm)
        self.assertIn("/browse/</loc>", sm)
        self.assertNotIn("/search/", sm)
        red = (self.out / "_redirects").read_text(encoding="utf-8")
        self.assertIn("/findings/browse/ /browse/ 301\n", red)
        self.assertIn("/en/findings/browse/ /en/browse/ 301\n", red)

    # ── 전역 검색 ─────────────────────────────────────────────────────────
    def test_header_search_form_and_search_page(self):
        for root in ("", "en/"):
            html = self._read(root + "glossary/index.html")
            m = re.search(r'<form class="nav-search" action="([^"]+)"', html)
            self.assertIsNotNone(m, root)
            self.assertTrue((self.out / root / "glossary").joinpath(m.group(1)).resolve().is_file())
            sp = self._read(root + "search/index.html")
            self.assertIn('name="robots" content="noindex', sp)
            self.assertNotIn('class="nav-search"', sp)  # 검색 화면 자신은 헤더 칸이 없다
            self.assertIn('id="grm-search-cfg"', sp)
        for name, lang in (("lookup.json", "ko"), ("lookup.en.json", "en")):
            d = json.loads((self.out / "assets" / name).read_text(encoding="utf-8"))
            self.assertEqual(d["schema"], "grm-lookup/v1")
            self.assertEqual(d["lang"], lang)
            self.assertTrue(d["terms"])
            pfx = "" if lang == "ko" else "en/"
            for t in d["terms"][:20]:
                self.assertTrue((self.out / pfx / "glossary" / t["id"] / "index.html").is_file(), t["id"])
            for f in d["firms"]:
                self.assertTrue((self.out / pfx / "findings" / "firm" / f["slug"] / "index.html").is_file(), f["slug"])
            for c in d["clauses"]:
                self.assertTrue((self.out / pfx / "findings" / "clause" / c["slug"] / "index.html").is_file(), c["slug"])
            if lang == "en":
                blob = json.dumps(d, ensure_ascii=False)
                self.assertIsNone(re.search(r'"n": "[^"]*[가-힣]', blob), "영어 색인 표제에 한글")

    # ── 푸터 '찾아보기' 열 ────────────────────────────────────────────────
    def test_footer_browse_column(self):
        for rel in ("index.html", "findings/trends/index.html", "glossary/index.html"):
            html = self._read(rel)
            col = _fnd_col(html)
            self.assertEqual(_labels(col)[-1], "전체 색인", rel)
            for h in re.findall(r'href="([^"]+)"', col):
                self.assertTrue((self.out / rel).parent.joinpath(h).resolve().is_file(), f"{rel} → {h}")
            foot = html[html.index('<footer class="site">'):]
            self.assertNotIn("<h5>지적사항</h5>", foot, rel)
            self.assertNotIn('class="foot-grp"', foot, rel)

    def test_footer_content_links_news_after_brief(self):
        """[2026-11-10] /news/ 는 상단 nav 가 아니라 푸터 콘텐츠 열, 주간 브리프 바로 다음."""
        for rel in ("index.html", "findings/index.html"):
            html = self._read(rel)
            i = html.index("<h5>콘텐츠</h5>")
            col = html[i:html.index("</div>", i)]
            labels = _labels(col)
            self.assertEqual(labels[:2], ["주간 브리프", "최근 규제 소식"], rel)
            href = re.search(r'href="([^"]*news/index\.html)"', col).group(1)
            self.assertTrue((self.out / rel).parent.joinpath(href).resolve().is_file(), rel)
            tabs, _ = _nav(html)
            self.assertNotIn("news/", tabs, rel)

    # ── 다음으로 볼 것 ────────────────────────────────────────────────────
    def _strip_targets(self):
        def pick(d, n):
            # 별칭 리다이렉트 스텁(base.html 을 쓰지 않는 장)은 대상이 아니다.
            out = [p for p in sorted(d.glob("*/index.html"))
                   if '<footer class="site">' in p.read_text(encoding="utf-8")]
            return out[:n]
        return {
            "glossary": pick(self.out / "glossary", 5),
            "doc": pick(self.out / "findings" / "doc", 5),
            "firm": pick(self.out / "findings" / "firm", 5),
            "en_glossary": pick(self.out / "en" / "glossary", 3),
            "en_doc": pick(self.out / "en" / "findings" / "doc", 3),
        }

    def test_strip_on_entry_pages_and_links_resolve(self):
        for kind, pages in self._strip_targets().items():
            self.assertTrue(pages, f"{kind} 페이지가 픽스처 빌드에 없다")
            for p in pages:
                html = p.read_text(encoding="utf-8")
                strip = _strip(html)
                self.assertIsNotNone(strip, f"{kind} {p}")
                self.assertEqual(html.count('class="wrap grm-next"'), 1)
                # 본문(main) 뒤 · 푸터 앞
                self.assertLess(html.rindex("</main>"), html.index('class="wrap grm-next"'))
                self.assertLess(html.index('class="wrap grm-next"'), html.index('<footer class="site">'))
                hrefs = re.findall(r'href="([^"]+)"', strip)
                self.assertIn("findings/checklist/index.html", " ".join(hrefs))
                for h in hrefs:
                    if h.startswith("#"):
                        self.assertIn(f'id="{h[1:]}"', html, f"{p} → {h}")
                        continue
                    self.assertTrue((p.parent / h).resolve().is_file(), f"{p} → {h}")
        ko = self._strip_targets()["glossary"][0].read_text(encoding="utf-8")
        self.assertEqual(_labels(_strip(ko))[:2], ["이번 주 브리프", "실사 대비 체크리스트"])

    def test_strip_absent_elsewhere(self):
        entry_dirs = {self.out / "glossary", self.out / "findings" / "doc",
                      self.out / "findings" / "firm",
                      self.out / "en" / "glossary", self.out / "en" / "findings" / "doc",
                      self.out / "en" / "findings" / "firm"}
        checked = 0
        for p in self._all_pages():
            if p.parent.parent in entry_dirs:
                continue
            html = p.read_text(encoding="utf-8")
            if '<footer class="site">' not in html:
                continue
            self.assertIsNone(_strip(html), str(p))
            checked += 1
        self.assertGreater(checked, 20)

    def test_strip_subscribe_link_only_with_band(self):
        """구독 링크는 구독 밴드(#subscribe)가 그려질 때만 — 로컬·CI 빌드는 밴드가 꺼져 있다."""
        p = self._strip_targets()["glossary"][0]
        html = p.read_text(encoding="utf-8")
        self.assertNotIn('class="subscribe"', html)
        self.assertNotIn('href="#subscribe"', _strip(html))


class NavCleanupTemplateTest(unittest.TestCase):
    def test_strip_wired_only_for_entry_templates(self):
        src = (WEB_DIR / "render.py").read_text(encoding="utf-8")
        wired = re.findall(r'emit\("([a-z_]+\.html)",[^\n]*\n\s*next_strip=True', src)
        self.assertEqual(sorted(set(wired)),
                         ["findings_doc.html", "findings_firm_page.html", "glossary_term.html"])

    def test_seg_partials_removed(self):
        for name in ("trends_seg.html", "findings_seg.html"):
            self.assertFalse((WEB_DIR / "partials" / name).exists(), name)
            for tpl in (WEB_DIR / "templates").glob("*.html"):
                self.assertNotIn(name, tpl.read_text(encoding="utf-8"), tpl.name)


if __name__ == "__main__":
    unittest.main()
