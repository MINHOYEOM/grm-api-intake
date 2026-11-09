#!/usr/bin/env python3
"""얇은 문서·업체 페이지의 googlebot 전용 noindex (2026-11-09 Search Console 대응).

네이버는 계속 색인해야 하므로 `name="robots"` noindex 는 절대 쓰지 않는다.
CI 는 `tests/test_web_thin_page_googlebot.py` shim 으로 순회한다. 직접:
  python web/tests/test_thin_page_googlebot.py
"""
from __future__ import annotations

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

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"
GOOGLEBOT = '<meta name="googlebot" content="noindex, follow"'
ROBOTS_NOINDEX = re.compile(r'<meta\s+name="robots"[^>]*noindex', re.I)


def _doc(n_findings: int, text: str) -> dict:
    return {"findings": [{"text_ko": text, "text_orig": text} for _ in range(n_findings)]}


class ThinPredicateTest(unittest.TestCase):
    def test_doc_predicate(self):
        self.assertTrue(render.is_thin_doc_page(_doc(3, "a" * 100)))
        # 지적 수 초과 -> 얇지 않다(보수적: AND 규칙)
        self.assertFalse(render.is_thin_doc_page(
            _doc(render.THIN_DOC_MAX_FINDINGS + 1, "a" * 10)))
        # 본문 바이트 초과 -> 얇지 않다
        self.assertFalse(render.is_thin_doc_page(
            _doc(2, "a" * (render.THIN_DOC_MAX_BYTES // 2 + 1))))

    def test_doc_bytes_are_utf8(self):
        # 한글은 3바이트: 700자(2100B)는 2KB 초과 -> 얇지 않다
        self.assertFalse(render.is_thin_doc_page(_doc(1, "가" * 700)))
        self.assertTrue(render.is_thin_doc_page(_doc(1, "가" * 600)))

    def test_firm_predicate(self):
        thin = {"documents": [_doc(5, "a" * 300), _doc(6, "a" * 300)]}
        self.assertTrue(render.is_thin_firm_page(thin))
        many = {"documents": [_doc(1, "a")] * (render.THIN_FIRM_MAX_DOCS + 1)}
        self.assertFalse(render.is_thin_firm_page(many))
        big = {"documents": [_doc(1, "a" * 1500), _doc(1, "a" * 1500)]}
        self.assertFalse(render.is_thin_firm_page(big))


class ThinPageMetaTest(unittest.TestCase):
    """실제 findings_docs.json 으로 짓고, 페이지별 메타가 술어와 일치하는지 전수 대조."""

    @classmethod
    def setUpClass(cls):
        cls.data = render.load_findings_docs()
        if not cls.data:
            raise unittest.SkipTest("findings_docs.json 미존재")
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_thin_"))
        cls.out = cls._tmp / "site"
        render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _check_docs(self, root: pathlib.Path, lang: str, docs):
        thin = non_thin = 0
        for d in docs:
            p = root / "findings" / "doc" / d["slug"] / "index.html"
            if not p.is_file():
                continue
            html = p.read_text(encoding="utf-8")
            want = render.is_thin_doc_page(d, lang)
            self.assertEqual(GOOGLEBOT in html, want, f"{lang} {d['slug']}")
            self.assertIsNone(ROBOTS_NOINDEX.search(html), d["slug"])
            thin += want
            non_thin += not want
        self.assertGreater(thin, 0)
        self.assertGreater(non_thin, 0)

    def test_ko_doc_pages(self):
        self._check_docs(self.out, "ko", self.data["documents"])

    def test_en_doc_pages(self):
        docs = [d for d in self.data["documents"] if render.doc_is_english(d)]
        self._check_docs(self.out / "en", "en", docs)

    def test_firm_pages_and_no_robots_noindex(self):
        seen_thin = seen_full = 0
        for lang, root in (("ko", self.out), ("en", self.out / "en")):
            for p in (root / "findings" / "firm").glob("*/index.html"):
                html = p.read_text(encoding="utf-8")
                self.assertIsNone(ROBOTS_NOINDEX.search(html), str(p))
                if GOOGLEBOT in html:
                    seen_thin += 1
                else:
                    seen_full += 1
        self.assertGreater(seen_thin, 0)
        self.assertGreater(seen_full, 0)

    def test_other_pages_untouched(self):
        for rel in ("index.html", "findings/index.html", "sitemap.xml"):
            p = self.out / rel
            if p.is_file():
                self.assertNotIn('name="googlebot"', p.read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
