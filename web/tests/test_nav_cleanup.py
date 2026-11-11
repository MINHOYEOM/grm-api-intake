#!/usr/bin/env python3
"""[1안 메뉴 정리 2026-11-10] 상단 메뉴·지적사항 탭·푸터 도구 열·'다음으로 볼 것'.

  · 상단 nav: '트렌드' 탭 없음(지적사항 탭으로 이동) · 서랍의 '이번 주 소식' 없음
  · 지적사항 탭(2026-11-11): 위 줄 묶음 3개(찾기·경향·점검) + 아래 줄 그 묶음의 화면, 존 모든 면에서 같다
  · 푸터 지적사항 열: 탭과 같은 묶음·이름·순서 · 둘러보기 면 없음(옛 주소 301)
  · 다음으로 볼 것: 용어 낱장·문서·업체 페이지에만, 링크는 전부 실재하는 페이지로

CI 는 `tests/test_web_nav_cleanup.py` shim 으로 순회한다. 직접:
  python web/tests/test_nav_cleanup.py
"""
from __future__ import annotations

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

# [탭 3묶음 2026-11-11 · 대표 승인] 지적사항 존의 면 — (경로, 켜질 묶음, 켜질 화면)
GROUPS = ["찾기", "경향", "점검"]
SUBS = {
    "찾기": ["검색", "문서", "분류", "업체", "실사관"],
    "경향": ["지적 영역", "지적 조항", "실사 결과", "데이터 현황"],
    "점검": ["지적 사례", "체크리스트"],
}
ZONE_PAGES = [
    ("findings/index.html", "찾기", "검색"),
    ("findings/docs/index.html", "찾기", "문서"),
    ("findings/c/index.html", "찾기", "분류"),
    ("findings/agency/index.html", "찾기", "분류"),
    ("findings/firm/index.html", "찾기", "업체"),
    ("findings/import-alerts/index.html", "찾기", "업체"),
    ("findings/inspector/index.html", "찾기", "실사관"),
    ("findings/trends/index.html", "경향", "지적 영역"),
    ("findings/citations/index.html", "경향", "지적 조항"),
    ("findings/citations/firms/index.html", "경향", "지적 조항"),
    ("findings/inspections/index.html", "경향", "실사 결과"),
    ("findings/coverage/index.html", "경향", "데이터 현황"),
    ("findings/examples/index.html", "점검", "지적 사례"),
    ("findings/examples/pmda/index.html", "점검", "지적 사례"),
    ("findings/examples/mhra/index.html", "점검", "지적 사례"),
    ("findings/checklist/index.html", "점검", "체크리스트"),
]
STRIP_H = "다음으로 볼 것"


def _nav(html: str) -> tuple[str, str]:
    m = re.search(r'<nav id="navmenu">(.*?)</nav>', html, re.S)
    assert m, "navmenu 없음"
    tabs, _sep, more = m.group(1).partition('<span class="navmore">')
    return tabs, more


def _seg(html: str) -> str:
    i = html.index('<nav class="fnd-seg"')
    return html[i:html.index("</nav>", i)]


def _labels(fragment: str) -> list[str]:
    return [re.sub(r"<[^>]+>", "", t).strip()
            for t in re.findall(r"<a [^>]*>(.*?)</a>", fragment, re.S)]


def _part(seg: str, cls: str) -> str:
    i = seg.index(f'<div class="{cls}">')
    return seg[i:seg.index("</div>", i)]


def _fnd_col(html: str) -> str:
    i = html.index('<div class="foot-fnd"><h5>지적사항</h5>')
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
    def test_nav_has_no_trends_tab_and_no_this_week_item(self):
        for rel in ("index.html", "archive/index.html", "findings/trends/index.html",
                    "glossary/index.html"):
            tabs, more = _nav(self._read(rel))
            self.assertNotIn("findings/trends/", tabs, rel)
            self.assertNotIn(">트렌드<", tabs, rel)
            self.assertNotIn("이번 주 소식", tabs + more, rel)
            self.assertEqual(_labels(tabs),
                             ["주간 브리프", "지적사항", "자료실", "용어사전", "이용안내"], rel)

    def test_trends_zone_lights_findings_tab(self):
        for rel, _group, _active in ZONE_PAGES:
            tabs, _ = _nav(self._read(rel))
            self.assertIn('class="on">지적사항</a>', tabs, rel)

    def test_archive_top_links_latest_issue(self):
        html = self._read("archive/index.html")
        m = re.search(r'<a class="arc-now[^"]*"[^>]*href="([^"]+)"', html)
        self.assertIsNotNone(m, "아카이브 맨 위 '이번 주 브리프' 블록이 없다")
        self.assertLess(html.index('class="arc-now'), html.index('id="static-issues"'))
        latest = re.search(r'<a class="issue latest" href="([^"]+)"', html)
        self.assertEqual(m.group(1), latest.group(1))
        self.assertTrue((self.out / "archive" / m.group(1)).resolve().is_file())

    # ── 지적사항 탭(묶음 3 · 화면) ─────────────────────────────────────────
    def test_segment_groups_and_subs_across_zone(self):
        group_dests = None
        sub_dests: dict[str, list] = {}
        for rel, group, active in ZONE_PAGES:
            seg = _seg(self._read(rel))
            g, sub = _part(seg, "fnd-seg-g"), _part(seg, "fnd-seg-s")
            self.assertEqual(_labels(g), GROUPS, rel)
            self.assertEqual(re.findall(r'class="on" aria-current="true">([^<]+)</a>', g), [group], rel)
            self.assertEqual(_labels(sub), SUBS[group], rel)
            self.assertEqual(re.findall(r'aria-current="page">([^<]+)</a>', sub), [active], rel)
            # 면마다 깊이가 달라(../ vs ../../) 상대 경로 문자열은 다르다 — 해석한 목적지로 비교한다.
            here = (self.out / rel).parent
            gd = [here.joinpath(h).resolve() for h in re.findall(r'href="([^"]+)"', g)]
            sd = [here.joinpath(h).resolve() for h in re.findall(r'href="([^"]+)"', sub)]
            group_dests = group_dests or gd
            self.assertEqual(gd, group_dests, f"{rel} 의 묶음 링크가 다른 면과 다르다")
            sub_dests.setdefault(group, sd)
            self.assertEqual(sd, sub_dests[group], f"{rel} 의 화면 링크가 같은 묶음의 다른 면과 다르다")
            for d in gd + sd:
                self.assertTrue(d.is_file(), f"{rel} → {d}")
            self.assertNotIn('class="tr-seg"', self._read(rel), rel)
        self.assertEqual(set(sub_dests), set(GROUPS))

    def test_segment_on_facet_item_pages(self):
        pages = sorted((self.out / "findings" / "c").glob("*/index.html"))[:3]
        self.assertTrue(pages, "분류 모음 하위 페이지가 픽스처 빌드에 없다")
        for p in pages:
            seg = _seg(p.read_text(encoding="utf-8"))
            self.assertEqual(re.findall(r'aria-current="page">([^<]+)</a>', seg), ["분류"], str(p))

    def test_en_segment_matches(self):
        segs = {rel: _seg(self._read("en/" + rel)) for rel, _g, _a in ZONE_PAGES
                if (self.out / "en" / rel).is_file()}
        self.assertIn("findings/trends/index.html", segs)
        for rel, seg in segs.items():
            g = _part(seg, "fnd-seg-g")
            self.assertEqual(_labels(g), ["Find", "Patterns", "Check"], rel)
            for h in re.findall(r'href="([^"]+)"', seg):
                self.assertTrue((self.out / "en" / rel).parent.joinpath(h).resolve().is_file(), f"en/{rel} → {h}")

    def test_browse_face_retired_with_redirect(self):
        """둘러보기 면은 없앴다 — 페이지·sitemap 에 없고, 옛 주소는 검색 면으로 301."""
        self.assertFalse((self.out / "findings" / "browse").exists())
        self.assertFalse((self.out / "en" / "findings" / "browse").exists())
        self.assertNotIn("findings/browse/", (self.out / "sitemap.xml").read_text(encoding="utf-8"))
        red = (self.out / "_redirects").read_text(encoding="utf-8")
        self.assertIn("/findings/browse/ /findings/ 301\n", red)
        self.assertIn("/en/findings/browse/ /en/findings/ 301\n", red)
        for p in self._all_pages():
            self.assertNotIn("findings/browse/", p.read_text(encoding="utf-8"), str(p))

    # ── 푸터 '지적사항' 열 ────────────────────────────────────────────────
    def test_footer_findings_column_follows_tabs(self):
        for rel in ("index.html", "findings/trends/index.html", "glossary/index.html"):
            html = self._read(rel)
            col = _fnd_col(html)
            self.assertEqual(re.findall(r'<span class="foot-grp">([^<]+)</span>', col), GROUPS, rel)
            want = [t for g in GROUPS for t in SUBS[g] if t != "데이터 현황"]
            self.assertEqual(_labels(col), want, rel)
            for h in re.findall(r'href="([^"]+)"', col):
                self.assertTrue((self.out / rel).parent.joinpath(h).resolve().is_file(), f"{rel} → {h}")
            foot = html[html.index('<footer class="site">'):]
            self.assertNotIn("<h5>도구</h5>", foot, rel)
            self.assertNotIn("실사 지적사항</a>", foot, rel)

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

    def test_trends_seg_partial_removed(self):
        self.assertFalse((WEB_DIR / "partials" / "trends_seg.html").exists())
        for tpl in (WEB_DIR / "templates").glob("*.html"):
            self.assertNotIn("trends_seg", tpl.read_text(encoding="utf-8"), tpl.name)


if __name__ == "__main__":
    unittest.main()
