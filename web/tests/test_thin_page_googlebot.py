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
        # [2단계] 짧은 원문이라도 인용 문서가 3건 이상이면 인용 블록이 서서 E 에서 풀린다. D 는 그대로.
        self.assertEqual(ct.google_noindex_reason(en_doc, {"chars": 134}, "en", 2), "short")
        self.assertEqual(ct.google_noindex_reason(en_doc, {"chars": 134}, "en", 3), "")
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "ko", 50), "untranslated")

    def test_clause_bare_reason(self):
        """[구글 유입 2026-11-27] 원문뿐(GRM 덧붙임 0)인 조항은 길어도 구글에서 뺀다."""
        import clause_text_page as ct
        en_doc = {"source": {"lang": "en"}}
        long_c = {"chars": 500, "text_ko": ""}
        self.assertFalse(ct.has_grm_addition(0, [], ""))
        self.assertFalse(ct.has_grm_addition(0, None, ""))
        self.assertTrue(ct.has_grm_addition(1, [], ""))
        self.assertTrue(ct.has_grm_addition(0, [{"id": "t"}], ""))
        self.assertTrue(ct.has_grm_addition(0, [], "findings/clause/211-22/"))
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "en", 0, bare=True), "bare")
        # 덧붙임이 생기면 다음 빌드에서 자동 복귀
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "en", 1, bare=False), "")
        # 종전 호출(bare 생략)은 판정이 바뀌지 않는다
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "en"), "")
        # 짧음(E)·미번역(D)이 우선한다
        self.assertEqual(ct.google_noindex_reason(en_doc, {"chars": 10}, "en", 0, bare=True), "short")
        self.assertEqual(ct.google_noindex_reason(en_doc, long_c, "ko", 0, bare=True), "untranslated")

    def test_render_wires_bare_at_both_sites(self):
        """렌더(페이지 meta)와 sitemap 제외 루프가 같은 bare 판정을 넘긴다 — 한쪽만 빠지면
        페이지는 noindex 인데 sitemap 에 남거나 그 반대가 된다."""
        src = pathlib.Path(render.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("clause_text_page.google_noindex_reason("), 2)
        self.assertEqual(src.count("bare=not clause_text_page.has_grm_addition("), 2)

    def test_citing_findings(self):
        import clause_text_page as ct
        src = {"schema": ct.SCHEMA, "source": {"key": "cfr21", "lang": "en"},
               "docs": [{"key": "211", "code": "21 CFR Part 211", "url": "u"},
                        {"key": "1271", "code": "21 CFR Part 1271", "url": "u"}],
               "clauses": [
                   {"id": "a", "doc": "211", "number": "211.192", "slug": "211-192",
                    "code": "21 CFR 211.192", "text": "x", "chars": 1},
                   {"id": "b", "doc": "1271", "number": "1271.75", "slug": "1271-75",
                    "code": "21 CFR 1271.75", "text": "y", "chars": 1},
                   {"id": "c", "doc": "211", "number": "211.22", "slug": "211-22",
                    "code": "21 CFR 211.22", "text": "", "chars": 0}]}
        view = ct.build([src])
        docs = [
            {"document_id": "d1", "slug": "s1", "published_date": "2025-01-01", "findings": [
                {"finding_id": "f1", "text_ko": "가 (21 CFR 211.192(a)).",
                 "cfr_refs": ["21 CFR 211.192(a)", "21 CFR 211.192"]},
                {"finding_id": "f2", "text_ko": "나 (21 CFR 1271.75). 같은 문서의 211.22 도 보라.",
                 "cfr_refs": ["21 CFR 1271.75", "21 CFR 211.22"]}]},
            {"document_id": "d2", "slug": "s2", "published_date": "2026-01-01", "findings": [
                {"finding_id": "f3", "text_ko": "다", "text_orig": "C (21 CFR 211.192 and 211.22(a)).",
                 "cfr_refs": ["21 CFR 211.192"]},
                {"finding_id": "f4", "text_ko": "", "text_orig": "(21 CFR 211.192)",
                 "cfr_refs": ["21 CFR 211.192"]},
                # 권고문에 딸려 온 조항 — 본문 첫 인용에 없으면 세지 않는다(211.34 오염 사례)
                {"finding_id": "f5", "text_ko": "라 (21 CFR 211.100). 컨설턴트(211.192)를 권한다.",
                 "cfr_refs": ["21 CFR 211.100", "21 CFR 211.192"]}]},
        ]
        self.assertEqual(ct.headline_sections("x (21 CFR 211.22(a) and 211.192). y 211.34"),
                         {"211.22", "211.192"})
        self.assertEqual(ct.headline_sections("인용 없음"), set())
        self.assertEqual(ct.headline_sections("a" * 700 + " consultant (21 CFR 211.34)."), set())
        out = ct.citing_findings(docs, view, lambda f: f.get("text_ko") or "")
        h192 = "clauses/cfr21/211/211-192/"
        # 하위항은 섹션으로 접혀 한 지적이 두 번 세지지 않는다 · 빈 본문은 세지 않는다
        self.assertEqual((out[h192]["documents"], out[h192]["findings"]), (2, 2))
        self.assertEqual([s["finding_id"] for s in out[h192]["samples"]], ["f3", "f1"])
        self.assertEqual(out["clauses/cfr21/1271/1271-75/"]["findings"], 1)  # 네 자리 Part
        self.assertEqual(len(out), 2)  # 본문 없는 조항(목차에만 서는 번호)에는 붙이지 않는다
        only = ct.citing_findings(docs, view, lambda f: f.get("text_ko") or "",
                                  doc_ok=lambda d: d["slug"] == "s2")
        self.assertEqual(only[h192]["documents"], 1)


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
        seen = {"untranslated": 0, "short": 0, "bare": 0, "": 0}
        views = {lg: ct.build(ct.for_lang(ct.load_sources(), lg)) for lg in ("ko", "en")}
        cites = render.clause_text_cites(self.data, views)
        rescued = with_block = 0
        for lang, root in (("ko", self.out), ("en", self.out / "en")):
            for d, c in ct.pages(views[lang]):
                p = root / c["href"] / "index.html"
                html = p.read_text(encoding="utf-8")
                cite = cites[lang].get(c["href"])
                n_docs = (cite or {}).get("documents", 0)
                terms = ct.terms_by_href(views[lang], render.load_glossary(), lang).get(c["href"])
                # 사례 모음 링크는 페이지에 실제로 실렸는지로 본다(렌더와 같은 판정의 결과물)
                has_cases = (d["source_key"] == "cfr21"
                             and f'findings/clause/{c["number"].replace(".", "-")}/' in html)
                why = ct.google_noindex_reason(
                    d, c, lang, n_docs,
                    bare=not ct.has_grm_addition(n_docs, terms, "x" if has_cases else ""))
                self.assertEqual(GOOGLEBOT in html, bool(why), f"{lang} {c['href']}")
                self.assertIsNone(ROBOTS_NOINDEX.search(html), c["href"])
                seen[why] += 1
                # [2단계] 인용 블록은 인용이 있는 조항에만, 사례마다 문서 링크가 실제 페이지로 간다
                self.assertEqual('class="ct-cites"' in html, bool(cite), c["href"])
                if cite:
                    with_block += 1
                    for s in cite["samples"]:
                        self.assertIn(f'findings/doc/{s["doc_slug"]}/', html)
                        self.assertTrue((root / "findings" / "doc" / s["doc_slug"] / "index.html").is_file(),
                                        f"{lang} {s['doc_slug']}")
                    if not why and ct.google_noindex_reason(d, c, lang) == "short":
                        rescued += 1
        self.assertTrue(all(seen.values()), seen)
        self.assertGreater(with_block, 0)
        self.assertGreater(rescued, 0)  # 짧은 원문이지만 인용으로 돌아온 조항이 실제로 있다

    def test_stage2_translation_and_gist_blocks(self):
        """[구글 품질 2단계 2026-11-20] 국문 참고 번역·GRM 요지가 실빌드에 실리는가(국문 트리만).
        번역된 조항은 국문 블록이 있고 '준비 중' 문구가 없다. 300자 이상이면 sitemap.xml 로 돌아온다.
        요지는 doc_gist.view 가 넘긴 문서에만, 영문 트리에는 없다."""
        import clause_text_page as ct
        import doc_gist
        gset = set(re.findall(r"<loc>https?://[^/]+/([^<]*)</loc>",
                              (self.out / "sitemap.xml").read_text(encoding="utf-8")))
        n_ko = n_back = 0
        for d, c in ct.pages(ct.build(ct.load_sources())):
            if d["source"]["lang"] == "ko":
                continue
            ko = (self.out / c["href"] / "index.html").read_text(encoding="utf-8")
            en = (self.out / "en" / c["href"] / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("국문 참고 번역", en, c["href"])
            if c["ko_paras"]:
                n_ko += 1
                self.assertIn("국문 참고 번역", ko, c["href"])
                import html as _h
                self.assertIn(c["ko_paras"][0], _h.unescape(ko), c["href"])
                self.assertNotIn("국문 번역은 준비 중", ko, c["href"])
                if len(c["text_ko"]) >= ct.GOOGLE_MIN_CHARS:
                    n_back += 1
                    self.assertIn(c["href"], gset, c["href"])
            else:
                self.assertNotIn("국문 참고 번역", ko, c["href"])
        self.assertGreater(n_ko, 0)
        self.assertGreater(n_back, 0)
        gists = doc_gist.load_outbox()
        n_gist = 0
        for d in self.data["documents"]:
            p = self.out / "findings" / "doc" / d["slug"] / "index.html"
            if not p.is_file():
                continue
            v = doc_gist.view(d, gists)
            html = p.read_text(encoding="utf-8")
            self.assertEqual('class="fd-gist"' in html, v is not None, d["slug"])
            n_gist += v is not None
            pe = self.out / "en" / "findings" / "doc" / d["slug"] / "index.html"
            if pe.is_file():
                self.assertNotIn('class="fd-gist"', pe.read_text(encoding="utf-8"), d["slug"])
        self.assertGreater(n_gist, 0)

    def test_other_pages_untouched(self):
        for rel in ("index.html", "findings/index.html", "sitemap.xml"):
            p = self.out / rel
            if p.is_file():
                self.assertNotIn('name="googlebot"', p.read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
