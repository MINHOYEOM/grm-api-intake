#!/usr/bin/env python3
"""업체 페이지 상단 "FDA 수입경보 대상" 표시 (수입경보 1단계 2026-11-10).

  · 스냅샷 로더 — 없거나 깨지거나 스키마가 다르면 None(표시 생략, 빌드 계속)
  · 매칭 규칙 — 표기명의 수집기 정규화 == name_norm **그리고** 법인격 접미어까지 일치,
    두 나라 이상에 걸친 이름 제외, 해제(active=false) 제외
  · 실제 렌더 — 매칭된 업체만 블록이 서고(경보 번호·등재일·출처·FDA 원문 링크), 영어판에 한글 없음,
    스냅샷이 없으면 어느 업체 페이지에도 블록이 없다

CI 는 `tests/test_web_import_alert_firm.py` shim 으로 순회한다. 직접:
  python web/tests/test_import_alert_firm.py
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
import collect_fda_import_alert as ia  # noqa: E402
import import_alerts_refresh as refresh  # noqa: E402

SINGLE_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "single"
SMALL = REPO_ROOT / "tests" / "fixtures" / "import_alerts" / "import_alerts_small.json"
URL_6640 = "https://www.accessdata.fda.gov/cms_ia/importalert_189.html"
URL_6641 = "https://www.accessdata.fda.gov/cms_ia/importalert_190.html"


class LoaderTest(unittest.TestCase):
    def test_fixture_loads(self):
        snap = render.load_import_alerts(SMALL)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["measured_on"], "2026-11-09")

    def test_missing_broken_or_wrong_schema_is_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "ia.json"
            self.assertIsNone(render.load_import_alerts(p))
            p.write_text("{not json", encoding="utf-8")
            self.assertIsNone(render.load_import_alerts(p))
            obj = json.loads(SMALL.read_text(encoding="utf-8"))
            for broken in ({**obj, "schema_version": "grm-import-alerts/v0"},
                           {**obj, "measured_on": ""}, {**obj, "listings": None}, [obj]):
                p.write_text(json.dumps(broken), encoding="utf-8")
                self.assertIsNone(render.load_import_alerts(p))
        self.assertEqual(render.build_import_alert_index(None), {})


class MatchRuleTest(unittest.TestCase):
    def setUp(self):
        self.idx = render.build_import_alert_index(render.load_import_alerts(SMALL))

    def m(self, *names):
        return render.firm_import_alerts(names, self.idx)

    def test_exact_name_two_alerts(self):
        rows = self.m("Alpha Pharma Co., Ltd.")
        self.assertEqual([(a["alert_no"], a["url"]) for a in rows],
                         [("66-40", URL_6640), ("66-41", URL_6641)])
        a = rows[0]
        self.assertEqual(a["place"], "Cheonan, KOREA (THE REPUBLIC OF)")
        self.assertEqual(a["firm_published"], "2024-04-10")
        self.assertEqual(a["n_products"], 2)

    def test_suffix_synonyms_and_punctuation(self):
        self.assertEqual(len(self.m("ALPHA PHARMA COMPANY LIMITED")), 2)
        self.assertEqual(len(self.m("Alpha  Pharma Co Ltd")), 2)

    def test_legal_suffix_must_agree(self):
        # 수집기 정규화만 같고 법인격이 다르면(미국 법인 vs 캐나다 제조소 같은 경우) 잇지 않는다.
        self.assertEqual(self.m("Beta Healthcare Corporation"), [])
        self.assertEqual(self.m("Alpha Pharma Inc."), [])
        self.assertEqual(self.m("Alpha Pharma"), [])
        self.assertEqual(len(self.m("Beta Healthcare")), 1)

    def test_partial_name_never_matches(self):
        self.assertEqual(self.m("Alpha Pharma Korea Co., Ltd."), [])
        self.assertEqual(self.m("Beta"), [])

    def test_multi_country_name_excluded(self):
        self.assertEqual(self.m("Gamma Labs Ltd."), [])

    def test_inactive_excluded(self):
        self.assertEqual(self.m("Delta Bio Inc."), [])

    def test_any_display_name_of_the_firm(self):
        self.assertEqual(len(self.m("Alpha Pharma Inc.", "Alpha Pharma Co., Ltd.")), 2)
        self.assertEqual(self.m("", None), [])


class RenderedFirmPagesTest(unittest.TestCase):
    """실제 문서 정본으로 업체 페이지를 짓되, 스냅샷은 그 업체 이름으로 그 자리에서 만든다
    (정본이 매주 바뀌어도 테스트가 고정 업체명에 묶이지 않게)."""

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
        if len(cand) < 2:
            raise unittest.SkipTest("후보 업체 부족")
        (cls.hit_key, hit), (cls.decoy_key, decoy) = cand[0], cand[len(cand) // 2]
        cls.hit_name, decoy_name = hit[0]["firm_name"], decoy[0]["firm_name"]

        def row(alert, name, country, city, pub):
            return {"alert_no": alert, "firm_key": ia.firm_key(alert, country, name),
                    "firm_name": name, "name_norm": ia.normalize_firm_name(name),
                    "country": country, "city": city, "firm_published": pub,
                    "product_codes": ["53LD99"], "active": True, "first_seen": "2026-11-09"}

        rows = [row("66-40", cls.hit_name, "INDIA", "Testpur", "2024-04-10"),
                row("66-41", cls.hit_name, "INDIA", "Testpur", "2025-01-20"),
                # 미끼 — 수집기 정규화는 같고(GMBH 는 법인격) 법인격 표기만 다르다 → 붙으면 안 된다.
                row("66-40", decoy_name + " GmbH", "GERMANY", "Decoyburg", "2020-01-01")]
        assert ia.normalize_firm_name(decoy_name + " GmbH") == ia.normalize_firm_name(decoy_name)
        cls._tmp = pathlib.Path(tempfile.mkdtemp(prefix="grmweb_ia_"))
        snap_path = cls._tmp / "import_alerts.json"
        snap_path.write_text(refresh.serialize(
            refresh.build_snapshot(rows, measured_on="2026-11-09")), encoding="utf-8")
        cls._saved = render.IMPORT_ALERTS_FILE
        render.IMPORT_ALERTS_FILE = snap_path
        try:
            cls.out = cls._tmp / "site"
            render.render_site(SINGLE_FIXTURES, cls.out, render_doc_pages=True)
        finally:
            render.IMPORT_ALERTS_FILE = cls._saved
        cls.hit_slug = render._firm_slug(cls.hit_key)
        cls.decoy_slug = render._firm_slug(cls.decoy_key)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @staticmethod
    def _block(html):
        i = html.find('<section class="ff-ia"')
        return None if i < 0 else html[i:html.find("</section>", i)]

    def _page(self, root, slug):
        p = root / "findings" / "firm" / slug / "index.html"
        self.assertTrue(p.is_file(), str(p))
        return p.read_text(encoding="utf-8")

    def test_ko_hit_block(self):
        html = self._page(self.out, self.hit_slug)
        blk = self._block(html)
        self.assertIsNotNone(blk)
        self.assertIn("FDA 수입경보 대상 · 66-40", blk)
        self.assertIn("FDA 수입경보 대상 · 66-41", blk)
        self.assertIn("등재일 2024-04-10", blk)
        self.assertIn("출처: FDA Import Alert 66-40", blk)
        self.assertIn(f'href="{URL_6640}"', blk)
        self.assertIn(f'href="{URL_6641}"', blk)
        self.assertIn("Testpur, INDIA", blk)
        self.assertIn("2026-11-09", blk)
        # 블록은 머리(h1) 위 — "맨 위".
        self.assertLess(html.find('<section class="ff-ia"'), html.find('class="ff-h1"'))

    def test_en_hit_block_no_korean(self):
        html = self._page(self.out / "en", self.hit_slug)
        blk = self._block(html)
        self.assertIsNotNone(blk)
        self.assertIn("FDA Import Alert · 66-40", blk)
        self.assertIn("Listed 2024-04-10", blk)
        self.assertIn("Source: FDA Import Alert 66-41", blk)
        self.assertIsNone(re.search(r"[가-힣]", re.sub(r"<[^>]+>", "", blk)))

    def test_only_the_hit_firm_has_block(self):
        for lang, root in (("ko", self.out), ("en", self.out / "en")):
            with_block = [p.parent.name for p in (root / "findings" / "firm").glob("*/index.html")
                          if '<section class="ff-ia"' in p.read_text(encoding="utf-8")]
            self.assertEqual(with_block, [self.hit_slug], lang)
        self.assertIsNone(self._block(self._page(self.out, self.decoy_slug)))


class AbsentSnapshotTest(unittest.TestCase):
    """스냅샷이 없으면 렌더 경로의 입력이 빈 색인 → 어느 업체에도 행이 없다(빌드는 계속)."""

    def test_absent_file_yields_no_rows(self):
        snap = render.load_import_alerts(pathlib.Path(tempfile.gettempdir()) / "grm_no_such_ia.json")
        idx = render.build_import_alert_index(snap)
        self.assertEqual(render.firm_import_alerts(["Alpha Pharma Co., Ltd."], idx), [])


if __name__ == "__main__":
    unittest.main()
