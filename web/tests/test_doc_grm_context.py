#!/usr/bin/env python3
"""문서·업체 페이지의 GRM 고유 맥락 블록 (SEO 2단계 2026-11-09).

  · 다른 업체의 비슷한 지적(findings_similar.json — findings_similar_edges RPC)
  · 이 문서가 인용한 조항(조항 페이지가 있는 21 CFR 섹션만)
  · 업체 페이지: 비슷한 지적을 받은 다른 업체

블록은 데이터가 있을 때만 서고(빈 제목 금지), 링크는 그 언어 트리에 실제로 있는 페이지로만 간다.
CI 는 `tests/test_web_doc_grm_context.py` shim 으로 순회한다. 직접:
  python web/tests/test_doc_grm_context.py
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
import findings_docs_refresh as refresh  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"
SIM_H = {"ko": "다른 업체의 비슷한 지적", "en": "Similar findings at other companies"}
CL_H = {"ko": "이 문서가 인용한 조항", "en": "Sections cited in this document"}
FIRM_H = {"ko": "비슷한 지적을 받은 다른 업체", "en": "Other companies with similar findings"}


def _f(fid, ko="한국어 본문", orig="English body", cfr=None):
    return {"finding_id": fid, "text_ko": ko, "text_orig": orig, "cfr_refs": cfr or []}


def _d(doc_id, firm, findings, date="2024-01-01"):
    return {"document_id": doc_id, "slug": doc_id, "firm_key": firm, "firm_name": firm.upper(),
            "agency": "FDA", "published_date": date, "inspection_date": "", "findings": findings}


class SelectSimilarTest(unittest.TestCase):
    """갱신 스크립트의 문서 단위 선택 — 양방향·다른 업체·업체당 1건·점수순."""

    def setUp(self):
        self.docs = [
            _d("A", "alpha", [_f("a1"), _f("a2")]),
            _d("B", "beta", [_f("b1")]),
            _d("B2", "beta", [_f("b2")]),
            _d("C", "gamma", [_f("c1")]),
            _d("A2", "alpha", [_f("a3")]),
        ]

    def test_both_directions_other_firm_one_per_firm(self):
        edges = [("a1", "b1", 0.5), ("b2", "a2", 0.7), ("c1", "a1", 0.45),
                 ("a1", "a3", 0.9),          # 같은 업체 → 제외
                 ("a2", "c1", 0.39)]         # 하한 미만 → 제외
        out = refresh.select_similar_by_document(self.docs, edges)
        self.assertEqual(out["A"], [["a2", "B2", "b2", 0.7], ["a1", "C", "c1", 0.45]])
        self.assertEqual(out["C"], [["c1", "A", "a1", 0.45]])
        self.assertNotIn("A2", out)

    def test_unknown_finding_ignored_and_per_doc_cap(self):
        docs = [_d("X", "x", [_f("x1")])] + [_d(f"T{i}", f"t{i}", [_f(f"t{i}")]) for i in range(8)]
        edges = [("x1", f"t{i}", 0.5 + i / 100) for i in range(8)] + [("x1", "ghost", 0.99)]
        out = refresh.select_similar_by_document(docs, edges, per_doc=3)
        self.assertEqual([r[1] for r in out["X"]], ["T7", "T6", "T5"])

    def test_write_is_line_per_document_and_loadable(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_sim_"))
        try:
            p = tmp / "s.json"
            refresh.write_similar(p, self.docs, [("a1", "b1", 0.5), ("c1", "a1", 0.45)], "2026-11-09")
            text = p.read_text(encoding="utf-8")
            self.assertEqual(json.loads(text)["by_document"]["A"][0], ["a1", "B", "b1", 0.5])
            self.assertEqual(render.load_findings_similar(p)["C"], [["c1", "A", "a1", 0.45]])
            self.assertGreaterEqual(text.count("\n"), 4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_fetch_validates_rows(self):
        rows = [{"finding_id": "a1", "similar_id": "b1", "score": "0.51234"}]
        got = refresh.fetch_similar_edges("u", "k", ["a1", "b1"], post=lambda ids: rows, pacing=0)
        self.assertEqual(got, [("a1", "b1", 0.5123)])
        with self.assertRaises(RuntimeError):
            refresh.fetch_similar_edges("u", "k", ["a1"], post=lambda ids: [{"finding_id": "a1"}],
                                        pacing=0)


class LoaderDegradeTest(unittest.TestCase):
    def test_missing_or_wrong_schema_is_empty(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="grm_sim_"))
        try:
            self.assertEqual(render.load_findings_similar(tmp / "none.json"), {})
            bad = tmp / "bad.json"
            bad.write_text(json.dumps({"schema_version": "x", "by_document": {"A": []}}), encoding="utf-8")
            self.assertEqual(render.load_findings_similar(bad), {})
            broken = tmp / "broken.json"
            broken.write_text("{", encoding="utf-8")
            self.assertEqual(render.load_findings_similar(broken), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ViewTest(unittest.TestCase):
    def setUp(self):
        self.a = _d("A", "alpha", [_f("a1"), _f("a2", cfr=["21 CFR 211.192(a)"])])
        self.b = _d("B", "beta", [_f("b1", ko="비슷한 본문", orig="Similar body")])
        self.b2 = _d("B2", "beta", [_f("b2")])
        self.c = _d("C", "gamma", [_f("c1", orig="한글 원문")])      # 영어판에서 빠진다
        self.a2 = _d("A2", "alpha", [_f("a3")])
        self.by_id = {d["document_id"]: d for d in (self.a, self.b, self.b2, self.c, self.a2)}
        self.entries = [["a2", "B", "b1", 0.7], ["a1", "B2", "b2", 0.6], ["a1", "C", "c1", 0.5],
                        ["a1", "A2", "a3", 0.9], ["a1", "GONE", "x", 0.9]]

    def test_doc_view_filters(self):
        rows = render.doc_similar_view(self.a, self.entries, self.by_id, "ko", None)
        self.assertEqual([(r["slug"], r["no"]) for r in rows], [("B", 2), ("C", 1)])
        self.assertEqual(rows[0]["excerpt"], "비슷한 본문")
        en = render.doc_similar_view(self.a, self.entries, self.by_id, "en", None)
        self.assertEqual([r["slug"] for r in en], ["B"])
        self.assertEqual(en[0]["excerpt"], "Similar body")
        only_c = render.doc_similar_view(self.a, self.entries, self.by_id, "ko", {"C"})
        self.assertEqual([r["slug"] for r in only_c], ["C"])
        self.assertEqual(render.doc_similar_view(self.a, None, self.by_id), [])

    def test_excerpt_is_bounded(self):
        long = "가" * 400
        self.assertLessEqual(len(render._excerpt(long)), render.SIMILAR_EXCERPT_CHARS + 1)
        self.assertTrue(render._excerpt(long).endswith("…"))
        self.assertEqual(render._excerpt("  짧은   문장 "), "짧은 문장")

    def test_clause_view(self):
        clauses = {"211.192": {"code": "21 CFR 211.192", "slug": "211-192", "title_en": "Review",
                               "documents": 5}}
        rows = render.doc_clause_view(self.a, clauses, "ko")
        self.assertEqual(rows, [{"code": "21 CFR 211.192", "slug": "211-192", "title_en": "Review",
                                 "others": 4, "nos": [2]}])
        self.assertEqual(render.doc_clause_view(self.b, clauses, "ko"), [])
        one = {"211.192": {**clauses["211.192"], "documents": 1}}
        self.assertEqual(render.doc_clause_view(self.a, one, "ko"), [])

    def test_firm_view_aggregates(self):
        sim = {"A": self.entries, "A2": [["a3", "B", "b1", 0.8]]}
        rows = render.firm_similar_view([self.a, self.a2], sim, self.by_id, "ko", None,
                                        lambda k: k, lambda t: "beta-firm" if t["firm_key"] == "beta" else None)
        self.assertEqual([(r["name"], r["count"], r["firm_slug"]) for r in rows],
                         [("BETA", 2, "beta-firm"), ("GAMMA", 1, None)])


class RenderedPagesTest(unittest.TestCase):
    """실제 정본으로 짓고, 블록 유무가 뷰와 일치하는지·링크가 실재하는지 전수 대조."""

    @classmethod
    def setUpClass(cls):
        cls.data = render.load_findings_docs()
        cls.sim = render.load_findings_similar()
        if not cls.data or not cls.sim:
            raise unittest.SkipTest("findings_docs.json / findings_similar.json 미존재")
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ctx_"))
        cls.out = cls._tmp / "site"
        render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    _HREF = re.compile(r'href="([^"#?]+)/?(?:#([^"]*))?"')

    def _section(self, html, heading):
        i = html.find(f">{heading}</h2>")
        if i < 0:
            return None
        return html[i:html.find("</section>", i)]

    def _resolve(self, page: pathlib.Path, href: str) -> pathlib.Path:
        target = (page.parent / href).resolve()
        return target / "index.html" if target.suffix != ".html" else target

    def _check_tree(self, lang, root):
        docs = [d for d in self.data["documents"]
                if lang == "ko" or render.doc_is_english(d)]
        seen_sim = seen_cl = seen_none = 0
        for d in docs:
            p = root / "findings" / "doc" / d["slug"] / "index.html"
            if not p.is_file():
                continue
            html = p.read_text(encoding="utf-8")
            for key, heading in (("sim", SIM_H[lang]), ("cl", CL_H[lang])):
                sec = self._section(html, heading)
                if sec is None:
                    continue
                items = re.findall(r"<li>", sec)
                self.assertTrue(items, f"빈 블록 {lang} {d['slug']} {key}")
                for href, anchor in self._HREF.findall(sec):
                    tgt = self._resolve(p, href)
                    self.assertTrue(tgt.is_file(), f"{lang} {d['slug']} → {href}")
                    if anchor:
                        self.assertIn(f'id="{anchor}"', tgt.read_text(encoding="utf-8"))
                if key == "sim":
                    seen_sim += 1
                else:
                    seen_cl += 1
            if SIM_H[lang] not in html and CL_H[lang] not in html:
                seen_none += 1
        self.assertGreater(seen_sim, 0)
        self.assertGreater(seen_cl, 0)
        self.assertGreater(seen_none, 0)   # 데이터가 없는 문서는 블록이 없다

    def test_ko_doc_pages(self):
        self._check_tree("ko", self.out)

    def test_en_doc_pages_no_korean_in_block(self):
        self._check_tree("en", self.out / "en")
        for p in list((self.out / "en" / "findings" / "doc").glob("*/index.html"))[:400]:
            sec = self._section(p.read_text(encoding="utf-8"), SIM_H["en"])
            if sec:
                body = re.sub(r"<[^>]+>", "", sec)
                self.assertIsNone(re.search(r"[가-힣]", body), str(p))

    def test_firm_pages_links_resolve(self):
        seen = 0
        for lang, root in (("ko", self.out), ("en", self.out / "en")):
            for p in (root / "findings" / "firm").glob("*/index.html"):
                sec = self._section(p.read_text(encoding="utf-8"), FIRM_H[lang])
                if sec is None:
                    continue
                seen += 1
                hrefs = self._HREF.findall(sec)
                self.assertTrue(hrefs, str(p))
                for href, _a in hrefs:
                    self.assertTrue(self._resolve(p, href).is_file(), f"{p} → {href}")
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
