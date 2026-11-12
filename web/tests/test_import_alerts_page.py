#!/usr/bin/env python3
"""FDA 수입경보 전체 목록 화면 /findings/import-alerts/ (수입경보 2단계 2026-11-10).

  · 뷰(import_alerts_page.list_view) — 요약 수치·정렬(등재 중 먼저)·국가 라벨(한국어/영어, 매핑 밖은
    FDA 표기 그대로)·업체 페이지 링크는 넘겨받은 slug 만
  · 국가 라벨 표 = findings.js COUNTRY_LABELS_KO(드리프트 금지)
  · 실제 렌더 — 픽스처 스냅샷으로 ko·en 두 장, 전 행 서버 렌더, 영어판 한글 없음, sitemap·hreflang,
    FDA 원문 링크, 업체 조회 화면의 진입 링크 / 스냅샷이 없으면 페이지·링크·sitemap 모두 없음
  · 업체 페이지와 매칭된 행만 업체 페이지로 링크(1단계와 같은 매칭 결과)

CI 는 `tests/test_web_import_alerts_page.py` shim 으로 순회한다. 직접:
  python web/tests/test_import_alerts_page.py
"""
from __future__ import annotations

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
import import_alerts_page as iap  # noqa: E402
import collect_fda_import_alert as ia  # noqa: E402
import import_alerts_refresh as refresh  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"
SMALL = REPO_ROOT / "tests" / "fixtures" / "import_alerts" / "import_alerts_small.json"
URL_6640 = "https://www.accessdata.fda.gov/cms_ia/importalert_189.html"
URL_6641 = "https://www.accessdata.fda.gov/cms_ia/importalert_190.html"
HANGUL = re.compile(r"[가-힣]")



class ListViewTest(unittest.TestCase):
    def setUp(self):
        self.snap = render.load_import_alerts(SMALL)
        self.ko = iap.list_view(self.snap, tr=render.Translator("ko"), lang="ko")
        self.en = iap.list_view(self.snap, tr=render.Translator("en"), lang="en")

    def test_none_without_snapshot(self):
        self.assertIsNone(iap.list_view(None, tr=render.Translator("ko"), lang="ko"))

    def test_summary_counts(self):
        v = self.ko
        self.assertEqual((v["total"], v["active"], v["korea"], v["korea_active"]), (6, 5, 2, 2))
        self.assertEqual(v["measured_on"], "2026-11-09")
        self.assertEqual([(a["alert_no"], a["n"], a["url"]) for a in v["alerts"]],
                         [("66-40", 3, URL_6640), ("66-41", 3, URL_6641)])
        self.assertTrue(all(a["title"] for a in v["alerts"]))

    def test_active_first_then_newest(self):
        rows = self.ko["rows"]
        self.assertEqual([r["active"] for r in rows], [True] * 5 + [False])
        dates = [r["firm_published"] for r in rows if r["active"]]
        self.assertEqual(dates, sorted(dates, reverse=True))
        self.assertEqual(rows[-1]["firm_name"], "Delta Bio Inc.")

    def test_country_labels(self):
        by = {(r["alert_no"], r["firm_name"]): r for r in self.ko["rows"]}
        alpha = by[("66-40", "Alpha Pharma Co., Ltd.")]
        self.assertEqual((alpha["country_key"], alpha["country_label"]), ("KR", "대한민국"))
        en_alpha = next(r for r in self.en["rows"] if r["firm_name"] == "Alpha Pharma Co., Ltd.")
        self.assertEqual(en_alpha["country_label"], "South Korea")
        self.assertEqual(self.ko["countries"][0], {"key": "KR", "label": "대한민국", "n": 2})

    @staticmethod
    def _one(country):
        return {"measured_on": "2026-11-09", "listings": [
            {"alert_no": "66-41", "firm_key": "k", "firm_name": "Zeta Ltd", "name_norm": "ZETA",
             "country": country, "city": "Lahore", "firm_published": "2020-01-01",
             "product_codes": [], "active": True}]}

    def test_unmapped_country_kept_verbatim(self):
        v = iap.list_view(self._one("ATLANTIS"), tr=render.Translator("ko"), lang="ko")
        self.assertEqual((v["rows"][0]["country_key"], v["rows"][0]["country_label"]),
                         ("x-atlantis", "ATLANTIS"))

    def test_extra_country_korean_only_on_ko(self):
        # [2026-11-10] 68코드 표 밖 나라는 한국어 화면에서만 보충 표로 — 영어 화면은 FDA 표기 그대로.
        ko = iap.list_view(self._one("PAKISTAN"), tr=render.Translator("ko"), lang="ko")
        en = iap.list_view(self._one("PAKISTAN"), tr=render.Translator("en"), lang="en")
        self.assertEqual((ko["rows"][0]["country_key"], ko["rows"][0]["country_label"]),
                         ("x-pakistan", "파키스탄"))
        self.assertEqual(en["rows"][0]["country_label"], "PAKISTAN")
        self.assertEqual(ko["countries"][0]["label"], "파키스탄")

    def test_extra_table_never_shadows_iso_table(self):
        # 보충 표 키는 전부 'x-' — ISO2 표와 겹치지 않고, 이름도 겹치지 않는다(도미니카공화국≠도미니카연방).
        self.assertTrue(all(k.startswith("x-") for k in iap.EXTRA_COUNTRY_LABELS_KO))
        self.assertFalse(set(iap.EXTRA_COUNTRY_LABELS_KO.values()) & set(iap.COUNTRY_LABELS_KO.values()))

    def test_committed_snapshot_ko_labels_all_korean(self):
        # 커밋 스냅샷 기준 한국어 화면의 나라 이름은 전부 한글(11-10: 48개국이 FDA 대문자였다).
        snap = render.load_import_alerts(render.IMPORT_ALERTS_FILE)
        if not snap:
            self.skipTest("스냅샷 없음")
        v = iap.list_view(snap, tr=render.Translator("ko"), lang="ko")
        latin = sorted({c["label"] for c in v["countries"] if not iap._HANGUL_RE.search(c["label"])})
        self.assertEqual(latin, [], "보충 표(EXTRA_COUNTRY_LABELS_KO)에 한 줄 더하라")

    def test_firm_link_only_when_slug_given(self):
        v = iap.list_view(self.snap, tr=render.Translator("ko"), lang="ko",
                          firm_slugs={"66-41|CANADA|BETAHEALTHCARE": "beta-healthcare"})
        linked = [(r["firm_name"], r["firm_slug"]) for r in v["rows"] if r["firm_slug"]]
        self.assertEqual(linked, [("Beta Healthcare", "beta-healthcare")])
        self.assertEqual(v["linked"], 1)
        self.assertEqual(self.ko["linked"], 0)

    def test_en_drops_rows_with_hangul(self):
        snap = {"measured_on": "2026-11-09", "listings": [
            {"alert_no": "66-40", "firm_key": "a", "firm_name": "한글제약", "country": "KOREA (THE REPUBLIC OF)",
             "city": "Seoul", "firm_published": "2020-01-01", "product_codes": [], "active": True},
            {"alert_no": "66-40", "firm_key": "b", "firm_name": "Eta Co", "country": "INDIA",
             "city": "Pune", "firm_published": "2020-01-01", "product_codes": [], "active": True}]}
        self.assertEqual(iap.list_view(snap, tr=render.Translator("ko"), lang="ko")["total"], 2)
        en = iap.list_view(snap, tr=render.Translator("en"), lang="en")
        self.assertEqual([r["firm_name"] for r in en["rows"]], ["Eta Co"])
        self.assertEqual(en["korea"], 0)


class CountryLabelsSyncTest(unittest.TestCase):
    """Python 표가 findings.js COUNTRY_LABELS_KO 와 같아야 한다(같은 계약의 사본)."""

    def test_same_as_findings_js(self):
        src = (WEB_DIR / "assets" / "findings.js").read_text(encoding="utf-8")
        m = re.search(r"var COUNTRY_LABELS_KO = \{(.*?)\n  \};", src, re.S)
        self.assertIsNotNone(m)
        js = dict(re.findall(r'\b([A-Z]{2}): _t\("([^"]+)"\)', m.group(1)))
        self.assertGreaterEqual(len(js), 68)
        self.assertEqual(iap.COUNTRY_LABELS_KO, js)


def _render_with_snapshot(out: pathlib.Path, snap_path: pathlib.Path, *, doc_pages: bool) -> None:
    saved = render.IMPORT_ALERTS_FILE
    render.IMPORT_ALERTS_FILE = snap_path
    try:
        render.render_site(SINGLE_FIXTURES, out, render_doc_pages=doc_pages)
    finally:
        render.IMPORT_ALERTS_FILE = saved


class RenderedListPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ialist_"))
        cls.out = cls._tmp / "site"
        _render_with_snapshot(cls.out, SMALL, doc_pages=False)
        rd = lambda *p: cls.out.joinpath(*p).read_text(encoding="utf-8")  # noqa: E731
        cls.ko = rd("findings", "import-alerts", "index.html")
        cls.en = rd("en", "findings", "import-alerts", "index.html")
        cls.sitemap = rd("sitemap.xml")
        cls.llms = rd("llms.txt")
        cls.firm_ko = rd("findings", "firm", "index.html")
        cls.firm_en = rd("en", "findings", "firm", "index.html")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @staticmethod
    def _main(html):
        return html.split("<main>", 1)[1].split("</main>", 1)[0]

    def test_all_rows_server_rendered(self):
        for html in (self.ko, self.en):
            body = self._main(html)
            self.assertEqual(body.count("<tr data-c="), 6)
            for name in ("Alpha Pharma Co., Ltd.", "ALPHA PHARMA CO., LTD", "Gamma Labs Ltd.",
                         "Beta Healthcare", "Delta Bio Inc."):
                self.assertIn(name, body)

    def test_ko_summary_and_notice(self):
        body = self._main(self.ko)
        self.assertIn("2026-11-09 확인 기준 · 전체 6곳 · 현재 등재 5곳 · 한국 2곳", body)
        self.assertIn("등재 사유의 해석이나 위반 판단은 싣지 않습니다", body)
        self.assertIn(f'href="{URL_6640}"', body)
        self.assertIn(f'href="{URL_6641}"', body)
        self.assertIn('href="index.html?country=KR#ia-list"', body)
        self.assertIn("한국 업체만 보기 (2곳)", body)
        self.assertIn('<option value="KR">대한민국 (2)</option>', body)
        self.assertIn(">등재 해제</td>", body)
        self.assertIn('<tr data-c="KR"><td>Alpha Pharma Co., Ltd.</td><td>대한민국</td><td>Cheonan</td>'
                      '<td>66-40</td><td>2024-04-10</td><td>2</td><td>등재 중</td></tr>', body)
        self.assertIn('<tr data-c="JP" class="ia-off"><td>Delta Bio Inc.</td>', body)

    def test_page_weight_per_row(self):
        """2,218행 실데이터에서 페이지가 무거워지지 않게 — 행 마크업은 필요한 것만(행당 상한)."""
        rows = self.ko.split('id="ia-rows">', 1)[1].split("</tbody>", 1)[0]
        self.assertLess(len(rows.encode("utf-8")) / 6, 200)

    def test_en_has_no_korean(self):
        body = self._main(self.en)
        self.assertIsNone(HANGUL.search(body), HANGUL.search(body) and body[
            max(0, HANGUL.search(body).start() - 80):HANGUL.search(body).start() + 40])
        self.assertIn("As of 2026-11-09 · 6 in total · 5 currently listed · 2 in South Korea", body)
        self.assertIn('<option value="KR">South Korea (2)</option>', body)
        self.assertIn(">Delisted</td>", body)

    def test_inline_script_has_no_korean(self):
        for html in (self.ko, self.en):
            scripts = [s.split("</script>", 1)[0] for s in html.split("<script>")[1:]]
            mine = [s for s in scripts if 'getElementById("ia-rows")' in s]
            self.assertEqual(len(mine), 1)
            self.assertIsNone(HANGUL.search(mine[0]))

    def test_filter_bar_hidden_without_js(self):
        self.assertIn('<form class="ia-bar" id="ia-bar" role="search" hidden>', self.ko)
        self.assertIn(".ia-bar[hidden]{display:none}", self.ko)
        self.assertIn(".ia-tbl tr[hidden]{display:none}", self.ko)

    def test_sitemap_hreflang_llms(self):
        self.assertIn("/findings/import-alerts/</loc><lastmod>2026-11-09</lastmod>", self.sitemap)
        self.assertIn("/en/findings/import-alerts/</loc>", self.sitemap)
        for html in (self.ko, self.en):
            self.assertIn('hreflang="en" href="https://grm-solutions.com/en/findings/import-alerts/"', html)
            self.assertIn('hreflang="ko" href="https://grm-solutions.com/findings/import-alerts/"', html)
        self.assertIn("/en/findings/import-alerts/", self.llms)

    def test_entry_link_on_firm_lookup(self):
        """[탭 3묶음 2026-11-11] 수입경보 목록은 '찾기 › 업체' 화면 안의 보기 — 두 화면 위에 같은 보기 전환."""
        self.assertIn('<a href="../../findings/firm/index.html" class="on" aria-current="page">업체 찾기</a>'
                      '<a href="../../findings/import-alerts/index.html">수입경보 목록</a>', self.firm_ko)
        self.assertIn('<a href="../../findings/firm/index.html">업체 찾기</a>'
                      '<a href="../../findings/import-alerts/index.html" class="on" aria-current="page">수입경보 목록</a>',
                      self.ko)
        self.assertIn(">Import Alert list</a>", self.firm_en)
        # [메뉴 3개 2026-11-12] 탭 3묶음은 폐기 — 상단 메뉴의 '찾아보기'가 켜진다.
        self.assertIn('class="on">찾아보기</a>', self.ko)
        self.assertNotIn('class="fnd-seg"', self.ko)

    def test_no_firm_links_without_doc_pages(self):
        self.assertNotIn("findings/firm/", self._main(self.ko).split('id="ia-rows"', 1)[1])


class AbsentSnapshotPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ialist_none_"))
        cls.out = cls._tmp / "site"
        _render_with_snapshot(cls.out, cls._tmp / "no_such.json", doc_pages=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_no_page_no_link_no_sitemap(self):
        self.assertFalse((self.out / "findings" / "import-alerts").exists())
        self.assertFalse((self.out / "en" / "findings" / "import-alerts").exists())
        self.assertNotIn("import-alerts", (self.out / "sitemap.xml").read_text(encoding="utf-8"))
        self.assertNotIn("import-alerts", (self.out / "llms.txt").read_text(encoding="utf-8"))
        for rel in (("findings", "firm", "index.html"), ("en", "findings", "firm", "index.html")):
            self.assertNotIn("import-alerts", self.out.joinpath(*rel).read_text(encoding="utf-8"))


class FirmLinkFromListTest(unittest.TestCase):
    """업체 페이지와 매칭된 행만 그 업체 페이지로 링크한다(1단계와 같은 매칭 결과). 실제 문서
    정본으로 업체 페이지를 짓고, 스냅샷은 그 업체 이름으로 그 자리에서 만든다."""

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
                and not HANGUL.search(g[0]["firm_name"])]
        if not cand:
            raise unittest.SkipTest("후보 업체 부족")
        cls.hit_key, hit = cand[0]
        cls.hit_name = hit[0]["firm_name"]

        def row(alert, name, country, city):
            return {"alert_no": alert, "firm_key": ia.firm_key(alert, country, name),
                    "firm_name": name, "name_norm": ia.normalize_firm_name(name),
                    "country": country, "city": city, "firm_published": "2024-04-10",
                    "product_codes": ["53LD99"], "active": True, "first_seen": "2026-11-09"}

        rows = [row("66-40", cls.hit_name, "INDIA", "Testpur"),
                row("66-41", "Unrelated Facility Zzq Ltd", "CHINA", "Nowhere")]
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ialist_firm_"))
        snap_path = cls._tmp / "import_alerts.json"
        snap_path.write_text(refresh.serialize(
            refresh.build_snapshot(rows, measured_on="2026-11-09")), encoding="utf-8")
        cls.out = cls._tmp / "site"
        _render_with_snapshot(cls.out, snap_path, doc_pages=True)
        cls.slug = render._firm_slug(cls.hit_key)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_matched_row_links_to_firm_page_both_trees(self):
        for root in (self.out, self.out / "en"):
            with self.subTest(root=str(root)):
                html = (root / "findings" / "import-alerts" / "index.html").read_text(encoding="utf-8")
                rows = html.split('id="ia-rows"', 1)[1].split("</tbody>", 1)[0]
                links = re.findall(r'href="\.\./\.\./findings/firm/([^/"]+)/"', rows)
                self.assertEqual(links, [self.slug])
                self.assertTrue((root / "findings" / "firm" / self.slug / "index.html").is_file())
                self.assertIn("Unrelated Facility Zzq Ltd</td>", rows)

    def test_firm_page_block_links_back_to_list(self):
        html = (self.out / "findings" / "firm" / self.slug / "index.html").read_text(encoding="utf-8")
        blk = html[html.index('<section class="ff-ia"'):]
        blk = blk[:blk.index("</section>")]
        self.assertIn('findings/import-alerts/">수입경보 전체 목록</a>', blk)


if __name__ == "__main__":
    unittest.main()
