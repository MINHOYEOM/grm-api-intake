#!/usr/bin/env python3
"""FDA 지적 조항 × 제조소 전체 목록 /findings/citations/firms/ (2026-11-10).

  · 뷰(citation_firms_page.list_view) — 실제 커밋 스냅샷 전 행(2,208), 링크는 넘겨받은 slug 만
  · 실제 렌더 — 작은 픽스처 스냅샷으로 ko·en 두 장(전 행 서버 렌더·필터 마크업·sitemap·영어판 한글 없음)

CI 는 `tests/test_web_citation_firms_page.py` shim 으로 순회한다.
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
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402
import clause_firms_page as cf  # noqa: E402
import citation_firms_page as cfp  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"
HANGUL = re.compile(r"[가-힣]")


def _firm(fei, name, country, cname, city, codes, state=""):
    return {"fei": fei, "name": name, "city": city, "state": state, "country": country,
            "country_name": cname, "inspections": sum(c[1] for c in codes) and len(codes),
            "first": "2020-01-02", "last": codes[0][2], "codes": codes,
            "sections": [[c[0][len("21 CFR "):].split("(")[0], c[1], c[2]] for c in codes]}


FIX = {
    "schema_version": cf.SCHEMA_VERSION, "source": "FDA Data Dashboard - Inspections Citations",
    "as_of": "2026-11-08", "totals": {},
    "titles": {"21 CFR 211.192": "Production record review", "21 CFR 211.22(d)": "QCU procedures"},
    "firms": [
        _firm("1", "Alpha Pharma Inc.", "US", "United States", "Boston", [["21 CFR 211.192", 3, "2024-05-01"]], "MA"),
        _firm("2", "Beta Labs Ltd", "IN", "India", "Pune", [["21 CFR 211.22(d)", 1, "2023-01-05"],
                                                         ["21 CFR 211.192", 2, "2023-01-05"]]),
        _firm("3", "Gamma Co", "XX", "Narnia", "Cair", [["21 CFR 999.1", 1, "2022-02-02"]]),
    ],
}


class RealSnapshotViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = cf.load_snapshot()
        if not cls.snap:
            raise unittest.SkipTest("fda_citation_firms.json 미존재")

    def test_every_site_listed(self):
        v = cfp.list_view(self.snap, tr=render.Translator("ko"), lang="ko")
        self.assertEqual(v["total"], len(self.snap["firms"]))
        self.assertGreaterEqual(v["total"], 2000)
        self.assertEqual(sum(len(r["codes"]) for r in v["rows"]),
                         sum(len(f["codes"]) for f in self.snap["firms"]))

    def test_links_only_when_given(self):
        v = cfp.list_view(self.snap, tr=render.Translator("ko"), lang="ko")
        self.assertEqual((v["linked"], v["clause_links"]), (0, 0))
        f0 = self.snap["firms"][0]
        sec = f0["sections"][0][0]
        v = cfp.list_view(self.snap, tr=render.Translator("ko"), lang="ko",
                          site_slugs={cf.firm_id(f0): "some-firm"},
                          clause_slugs={sec.replace(".", "-")})
        self.assertEqual(v["linked"], 1)
        self.assertGreaterEqual(v["clause_links"], 1)
        for r in v["rows"]:
            for c in r["codes"]:
                if c["href"]:
                    self.assertEqual(c["href"], sec.replace(".", "-"))


class RenderedPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_citfirms_"))
        snap = cls._tmp / "fda_citation_firms.json"
        snap.write_text(json.dumps(FIX, ensure_ascii=False), encoding="utf-8")
        saved = cf.DATA_FILE
        cf.DATA_FILE = snap
        try:
            cls.out = cls._tmp / "site"
            render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=False)
        finally:
            cf.DATA_FILE = saved
        rd = lambda *p: cls.out.joinpath(*p).read_text(encoding="utf-8")  # noqa: E731
        cls.ko = rd("findings", "citations", "firms", "index.html")
        cls.en = rd("en", "findings", "citations", "firms", "index.html")
        cls.sitemap = rd("sitemap.xml")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @staticmethod
    def _main(html):
        return html.split("<main>", 1)[1].split("</main>", 1)[0]

    def test_all_rows_rendered(self):
        for html in (self.ko, self.en):
            body = self._main(html)
            self.assertEqual(body.count("<tr data-c="), 3)
            for n in ("Alpha Pharma Inc.", "Beta Labs Ltd", "Gamma Co"):
                self.assertIn(n, body)
            self.assertIn('<dd lang="en">Production record review</dd>', body)

    def test_ko_summary_and_filters(self):
        body = self._main(self.ko)
        self.assertIn("2026-11-08 확인 기준 · 제조소 3곳 · 3개국", body)
        for ident in ('id="cf-q"', 'id="cf-k"', 'id="cf-c"', 'id="cf-bar"'):
            self.assertIn(ident, body)
        self.assertIn('<option value="US">미국 (1)</option>', body)
        self.assertIn(".cf-bar[hidden]{display:none}", self.ko)

    def test_en_no_korean_and_labels(self):
        body = self._main(self.en)
        self.assertIsNone(HANGUL.search(body))
        self.assertIn("As of 2026-11-08 · 3 facilities · 3 countries", body)
        self.assertIn('<option value="US">United States (1)</option>', body)

    def test_links_point_only_to_existing_routes(self):
        for root in (self.out, self.out / "en"):
            html = (root / "findings" / "citations" / "firms" / "index.html").read_text(encoding="utf-8")
            body = self._main(html)
            for slug in re.findall(r'href="\.\./\.\./\.\./\.\./findings/firm/([^/"]+)/"', body):
                self.assertTrue((root / "findings" / "firm" / slug / "index.html").is_file(), slug)
            for slug in re.findall(r'href="\.\./\.\./\.\./\.\./findings/clause/([^/"]+)/"', body):
                self.assertTrue((root / "findings" / "clause" / slug / "index.html").is_file(), slug)

    def test_sitemap_hreflang(self):
        self.assertIn("/findings/citations/firms/</loc><lastmod>2026-11-08</lastmod>", self.sitemap)
        self.assertIn("/en/findings/citations/firms/</loc>", self.sitemap)
        self.assertIn('hreflang="en" href="https://grm-solutions.com/en/findings/citations/firms/"', self.ko)


if __name__ == "__main__":
    unittest.main()
