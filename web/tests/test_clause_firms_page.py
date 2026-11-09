#!/usr/bin/env python3
"""FDA 지적 조항 × 업체 — 업체 페이지·조항 페이지 블록 (2026-11-10).

  · 스냅샷 로더 — 없거나 깨지거나 스키마가 다르면 None(블록 생략, 빌드 계속)
  · 매칭 규칙 — 업체 정본 키(정규화+별칭) 일치 **그리고** 법인격까지 일치, 같은 이름이
    두 나라 이상이면 제외
  · 뷰 — 조항별 실사 수 합산·최근 실사일·정렬·상한, 조항 페이지 상위 N + 전체 수
  · 실제 렌더 — 매칭된 업체 페이지에만 블록, 조항 페이지 목록이 그 업체 페이지로 링크,
    영어판 본문에 한글 없음, 스냅샷이 없으면 어디에도 블록 없음

CI 는 `tests/test_web_clause_firms_page.py` shim 으로 순회한다. 직접:
  python web/tests/test_clause_firms_page.py
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import sys
import tempfile
import unittest
from collections import defaultdict

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = WEB_DIR.parent
sys.path.insert(0, str(WEB_DIR))
sys.path.insert(0, str(REPO_ROOT))
import render  # noqa: E402
import clause_firms_page as cf  # noqa: E402
import fda_citation_firms_refresh as refresh  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"


def loose(n):
    return render._canonical_firm_key(render._normalize_firm_name(n))


def rows_for(name, *, fei, country="IN", country_name="India", city="Testpur", n=1,
             code="21 CFR 211.192", start=0, date="2025-05-06"):
    return [{"inspection_id": f"{fei}-{i}", "citation_id": "1", "fei_number": fei,
             "legal_name": name, "city": city, "state_code": "", "country_name": country_name,
             "country_key": country, "inspection_end_date": date, "act_cfr_number": code,
             "short_description": "Investigations", "ingested_at": "2026-09-25"}
            for i in range(start, start + n)]


def snap_of(rows):
    return json.loads(refresh.serialize(refresh.build_snapshot(rows)))


class LoaderTest(unittest.TestCase):
    def test_missing_broken_or_wrong_schema_is_none(self):
        good = snap_of(rows_for("Alpha Pharma Ltd", fei="1"))
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "s.json"
            self.assertIsNone(cf.load_snapshot(p))
            p.write_text("{not json", encoding="utf-8")
            self.assertIsNone(cf.load_snapshot(p))
            for broken in ({**good, "schema_version": "v0"}, {**good, "as_of": ""},
                           {**good, "firms": []}, [good]):
                p.write_text(json.dumps(broken), encoding="utf-8")
                self.assertIsNone(cf.load_snapshot(p))
            p.write_text(json.dumps(good), encoding="utf-8")
            self.assertEqual(cf.load_snapshot(p)["as_of"], "2026-09-25")
        self.assertEqual(cf.build_index(None, loose), {})
        self.assertEqual(cf.link_firm_pages(None, {"x": ["X"]}, loose), {})


class MatchRuleTest(unittest.TestCase):
    def setUp(self):
        self.snap = snap_of(
            rows_for("Alpha Pharma Limited", fei="1")
            + rows_for("Alpha Pharma Ltd.", fei="2", city="Vizag")
            + rows_for("Sandoz Private Limited", fei="3")
            + rows_for("Delta Pharma Inc.", fei="4", country="CA", country_name="Canada")
            + rows_for("Delta Pharma, Inc.", fei="5", country="US", country_name="United States"))
        self.idx = cf.build_index(self.snap, loose)

    def names(self, key, docs):
        return [f["name"] for f in cf.match_firm(key, docs, self.idx)]

    def test_suffix_synonym_and_punctuation(self):
        self.assertEqual(sorted(self.names(loose("Alpha Pharma Ltd"), ["Alpha Pharma, Ltd"])),
                         ["Alpha Pharma Limited", "Alpha Pharma Ltd."])

    def test_legal_suffix_must_agree(self):
        # 정규화 키(sandoz)는 같지만 법인격이 다르다 — 붙으면 안 된다.
        self.assertEqual(loose("Sandoz, Inc."), loose("Sandoz Private Limited"))
        self.assertEqual(self.names(loose("Sandoz, Inc."), ["Sandoz, Inc."]), [])
        self.assertEqual(self.names(loose("Alpha Pharma"), ["Alpha Pharma"]), [])

    def test_multi_country_name_excluded(self):
        self.assertEqual(self.names(loose("Delta Pharma Inc"), ["Delta Pharma Inc"]), [])

    def test_other_page_key_never_matches(self):
        self.assertEqual(self.names("alpha", ["Alpha Pharma Ltd"]), [])

    def test_link_firm_pages_drops_empty(self):
        got = cf.link_firm_pages(self.snap, {loose("Alpha Pharma Ltd"): ["Alpha Pharma Ltd"],
                                             loose("Sandoz Inc"): ["Sandoz Inc"]}, loose)
        self.assertEqual(list(got), [loose("Alpha Pharma Ltd")])

    def test_strict_key(self):
        self.assertEqual(cf.strict_key("The Dr. Reddy’s Laboratories, Limited"),
                         cf.strict_key("Dr Reddy's Laboratories Ltd"))
        self.assertNotEqual(cf.strict_key("X Inc"), cf.strict_key("X LLC"))
        self.assertEqual(cf.slug_if("a", None), "a")
        self.assertIsNone(cf.slug_if("a", {"b"}))
        self.assertIsNone(cf.slug_if(None, None))


class ViewTest(unittest.TestCase):
    def test_firm_view_sums_sites_and_caps(self):
        rows = (rows_for("A Ltd", fei="1", n=2, date="2024-01-02")
                + rows_for("A Ltd", fei="2", n=1, date="2025-07-08")
                + rows_for("A Ltd", fei="2", n=1, code="21 CFR 211.22(d)", start=5,
                           date="2023-03-04"))
        snap = snap_of(rows)
        v = cf.firm_view(snap["firms"], snap, max_codes=1)
        self.assertEqual(v["codes"], [{"code": "21 CFR 211.192", "title": "Investigations",
                                       "inspections": 3, "last": "2025-07-08",
                                       "section": "211.192"}])
        self.assertEqual((v["more"], v["n_codes"], v["inspections"]), (1, 2, 4))
        self.assertEqual(v["period"], "2023-03-04~2025-07-08")
        self.assertEqual(len(v["sites"]), 2)
        self.assertIsNone(cf.firm_view([], snap))
        one = snap_of(rows_for("B Inc", fei="9"))
        self.assertEqual(cf.firm_view(one["firms"], one)["period"], "2025-05-06")

    def test_clause_view_order_top_and_links(self):
        snap = snap_of(rows_for("Few Inc", fei="1", n=1, date="2026-01-01")
                       + rows_for("Many Inc", fei="2", n=3)
                       + rows_for("Recent Inc", fei="3", n=1, date="2026-02-02")
                       + rows_for("Other Inc", fei="4", code="21 CFR 211.22(d)"))
        v = cf.clause_view(snap, "211.192", lambda f: "many" if f["name"] == "Many Inc" else None,
                           top_n=2)
        self.assertEqual(v["total"], 3)
        self.assertEqual([(r["name"], r["slug"]) for r in v["rows"]],
                         [("Many Inc", "many"), ("Recent Inc", "")])
        self.assertEqual(v["rows"][0]["place"], "Testpur, India")
        self.assertIsNone(cf.clause_view(snap, "211.68", lambda f: None))
        self.assertIsNone(cf.clause_view(None, "211.192", lambda f: None))


class RenderedPagesTest(unittest.TestCase):
    """실제 문서 정본으로 업체·조항 페이지를 짓되, 스냅샷은 그 업체 이름으로 그 자리에서 만든다."""

    SECTION = "211.192"

    @classmethod
    def setUpClass(cls):
        data = render.load_findings_docs()
        if not data:
            raise unittest.SkipTest("findings_docs.json 미존재")
        groups = defaultdict(list)
        for d in data["documents"]:
            k = render._canonical_firm_key(d.get("firm_key") or "")
            if k:
                groups[k].append(d)
        cand = [(k, g) for k, g in sorted(groups.items())
                if sum(1 for d in g if render.doc_is_english(d)) >= 2
                and len({d["firm_name"] for d in g}) == 1 and render._firm_slug(k)
                and not re.search(r"[가-힣]", g[0]["firm_name"])]
        if len(cand) < 3:
            raise unittest.SkipTest("후보 업체 부족")
        (cls.hit_key, hit), (cls.decoy_key, decoy), (cls.multi_key, multi) = (
            cand[0], cand[len(cand) // 2], cand[-1])
        cls.hit_name = hit[0]["firm_name"]
        decoy_name, multi_name = decoy[0]["firm_name"] + " GmbH", multi[0]["firm_name"]
        assert loose(decoy_name) == cls.decoy_key
        rows = (rows_for(cls.hit_name, fei="91", n=60)                     # 조항 페이지 1위
                + rows_for(cls.hit_name, fei="92", n=2, city="Secondpur", start=100,
                           code="21 CFR 211.22(d)", date="2026-02-03")
                # 미끼 — 정규화 키는 같고 법인격(GmbH)만 다르다 → 붙으면 안 된다.
                + rows_for(decoy_name, fei="93", n=50, country="DE", country_name="Germany")
                # 같은 이름이 두 나라 → 매칭 제외.
                + rows_for(multi_name, fei="94", n=40, country="US", country_name="United States")
                + rows_for(multi_name, fei="95", n=1, country="CA", country_name="Canada"))
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_cf_"))
        snap_path = cls._tmp / "fda_citation_firms.json"
        snap_path.write_text(refresh.serialize(refresh.build_snapshot(rows)), encoding="utf-8")
        cls._saved = cf.DATA_FILE
        cf.DATA_FILE = snap_path
        try:
            cls.out = cls._tmp / "site"
            render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=True)
        finally:
            cf.DATA_FILE = cls._saved
        cls.hit_slug = render._firm_slug(cls.hit_key)
        cls.decoy_slug = render._firm_slug(cls.decoy_key)
        cls.multi_slug = render._firm_slug(cls.multi_key)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @staticmethod
    def _block(html, marker):
        i = html.find(marker)
        return None if i < 0 else html[i:html.find("</section>", i)]

    def _read(self, p):
        self.assertTrue(p.is_file(), str(p))
        return p.read_text(encoding="utf-8")

    def test_ko_firm_block(self):
        html = self._read(self.out / "findings" / "firm" / self.hit_slug / "index.html")
        blk = self._block(html, "FDA 실사에서 지적받은 21 CFR 조항")
        self.assertIsNotNone(blk)
        self.assertIn("실사 62건(2025-05-06~2026-02-03)", blk)
        self.assertIn('findings/clause/211-192/">21 CFR 211.192</a>', blk)
        self.assertIn("2026-02-03", blk)
        self.assertIn("제조소 2곳", blk)
        self.assertIn("Secondpur, India", blk)

    def test_en_firm_block_no_korean(self):
        html = self._read(self.out / "en" / "findings" / "firm" / self.hit_slug / "index.html")
        blk = self._block(html, "21 CFR sections cited in FDA inspections")
        self.assertIsNotNone(blk)
        self.assertIn("62 FDA inspections", blk)
        self.assertIn("2 sites", blk)
        self.assertIsNone(re.search(r"[가-힣]", re.sub(r"<[^>]+>", "", blk)))

    def test_only_hit_firm_has_block(self):
        for lang, root, marker in (("ko", self.out, "FDA 실사에서 지적받은 21 CFR 조항"),
                                   ("en", self.out / "en", "21 CFR sections cited in FDA")):
            with_block = [p.parent.name for p in (root / "findings" / "firm").glob("*/index.html")
                          if marker in p.read_text(encoding="utf-8")]
            self.assertEqual(with_block, [self.hit_slug], lang)

    def test_clause_page_lists_and_links(self):
        for root, marker in ((self.out, "이 조항으로 FDA 실사 지적을 받은 업체"),
                             (self.out / "en", "Companies cited under this section")):
            p = root / "findings" / "clause" / "211-192" / "index.html"
            blk = self._block(self._read(p), marker)
            self.assertIsNotNone(blk, str(p))
            self.assertIn(f'findings/firm/{self.hit_slug}/">{self.hit_name}</a>', blk)
            # 미끼·다국가 이름은 목록엔 있어도 링크가 없다.
            self.assertNotIn(f"findings/firm/{self.decoy_slug}/", blk)
            self.assertNotIn(f"findings/firm/{self.multi_slug}/", blk)
            self.assertIn(">60<", blk)
        self.assertIn("제조소 4곳 중", self._block(
            self._read(self.out / "findings" / "clause" / "211-192" / "index.html"),
            "이 조항으로 FDA"))


class AbsentSnapshotTest(unittest.TestCase):
    def test_absent_file_yields_nothing(self):
        snap = cf.load_snapshot(pathlib.Path(tempfile.gettempdir()) / "grm_no_such_cf.json")
        self.assertIsNone(snap)
        self.assertEqual(cf.link_firm_pages(snap, {"a": ["A Inc"]}, loose), {})
        self.assertIsNone(cf.clause_view(snap, "211.192", lambda f: None))


if __name__ == "__main__":
    unittest.main()
