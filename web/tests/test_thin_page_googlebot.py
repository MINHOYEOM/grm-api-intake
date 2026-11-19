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


class DuplicateAndClausePredicateTest(unittest.TestCase):
    """[구글 품질 1단계 2026-11-19] 중복 B·조항 D/E 술어 — 기준을 넘으면 자동으로 돌아온다."""

    def test_duplicate_ratio(self):
        def d(i, *texts):
            return {"document_id": i, "findings": [{"text_ko": t, "text_orig": t} for t in texts]}
        docs = [d("a", "Same  Text", "x1", "x2", "x3", "x4"),
                d("b", "same text", "x1", "x2", "x3", "u1", "u2"),
                d("c", "x4", "x1", "x2", "x3", "own"),
                d("e", "solo")]
        r = render.doc_duplicate_ratios(docs)
        self.assertEqual(r["a"], 1.0)        # 공백·대소문자 정규화 후 5/5
        self.assertEqual(r["c"], 0.8)        # 4/5 = 경계(이상이면 중복)
        self.assertAlmostEqual(r["b"], 4 / 6)
        self.assertEqual(r["e"], 0.0)
        self.assertTrue(render.is_duplicate_doc_page(docs[2], r))
        self.assertFalse(render.is_duplicate_doc_page(docs[1], r))
        self.assertFalse(render.is_duplicate_doc_page(docs[3], r))
        # 보강으로 고유 지적이 늘면 비율이 내려가 자동 복귀
        docs[2]["findings"].append({"text_ko": "new", "text_orig": "new"})
        self.assertFalse(render.is_duplicate_doc_page(
            docs[2], render.doc_duplicate_ratios(docs)))

    def test_clause_reason(self):
        import clause_text_page as ct
        en_doc, ko_doc = {"source": {"lang": "en"}}, {"source": {"lang": "ko"}}
        long_c = {"chars": 500, "text_ko": ""}
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "ko"), "untranslated")
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "en"), "")
        self.assertEqual(ct.google_noindex_reason(en_doc, {"chars": 299}, "en"), "short")
        self.assertEqual(ct.google_noindex_reason(ko_doc, {"chars": 299}, "ko"), "short")
        self.assertEqual(ct.google_noindex_reason(ko_doc, long_c, "ko"), "")
        # 2단계에서 국문 번역이 생기면 국문 페이지가 돌아온다(번역 길이로 E 를 다시 잰다)
        self.assertEqual(ct.google_noindex_reason(
            en_doc, {"chars": 500, "text_ko": "가" * 300}, "ko"), "")
        self.assertEqual(ct.google_noindex_reason(
            en_doc, {"chars": 500, "text_ko": "가" * 10}, "ko"), "short")


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
        thin = dup = kept = 0
        ratios = render.doc_duplicate_ratios(self.data["documents"], lang)
        for d in docs:
            p = root / "findings" / "doc" / d["slug"] / "index.html"
            if not p.is_file():
                continue
            html = p.read_text(encoding="utf-8")
            want = render.google_noindex_doc_page(d, lang, ratios)
            self.assertEqual(GOOGLEBOT in html, want, f"{lang} {d['slug']}")
            self.assertIsNone(ROBOTS_NOINDEX.search(html), d["slug"])
            if render.is_thin_doc_page(d, lang):
                thin += 1
            elif want:
                dup += 1
            else:
                kept += 1
        self.assertGreater(thin, 0)
        self.assertGreater(dup, 0)
        self.assertGreater(kept, 0)

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

    def test_sitemap_agrees_with_page_meta(self):
        """[구글 품질 1단계 2026-11-19] 구글용 sitemap.xml 에 googlebot noindex 페이지가 하나도 없고,
        noindex 가 아닌 페이지는 하나도 빠지지 않는다. sitemap-full.xml 은 전부 싣는다(IndexNow 용)."""
        loc = re.compile(r"<loc>https?://[^/]+/([^<]*)</loc>")
        google = loc.findall((self.out / "sitemap.xml").read_text(encoding="utf-8"))
        full = loc.findall((self.out / "sitemap-full.xml").read_text(encoding="utf-8"))
        self.assertTrue(set(google) < set(full))
        self.assertEqual(len(google), len(set(google)))
        gset = set(google)
        noindexed = kept = 0
        for path in full:
            p = self.out / path / "index.html"
            if not p.is_file():
                continue
            has = GOOGLEBOT in p.read_text(encoding="utf-8")
            self.assertNotEqual(has, path in gset, path)
            noindexed += has
            kept += not has
        self.assertGreater(noindexed, 0)
        self.assertGreater(kept, 0)

    def test_clause_pages(self):
        """국문 트리의 외국어 원문 조항(D)과 300자 미만 조항(E)만 구글에서 뺀다."""
        import clause_text_page as ct
        seen = {"untranslated": 0, "short": 0, "": 0}
        for lang, root in (("ko", self.out), ("en", self.out / "en")):
            view = ct.build(ct.for_lang(ct.load_sources(), lang))
            for d, c in ct.pages(view):
                p = root / c["href"] / "index.html"
                html = p.read_text(encoding="utf-8")
                why = ct.google_noindex_reason(d, c, lang)
                self.assertEqual(GOOGLEBOT in html, bool(why), f"{lang} {c['href']}")
                self.assertIsNone(ROBOTS_NOINDEX.search(html), c["href"])
                seen[why] += 1
        self.assertTrue(all(seen.values()), seen)

    def test_other_pages_untouched(self):
        for rel in ("index.html", "findings/index.html", "sitemap.xml"):
            p = self.out / rel
            if p.is_file():
                self.assertNotIn('name="googlebot"', p.read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
