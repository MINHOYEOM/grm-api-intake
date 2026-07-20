"""자료실 수집기 3종(FDA 가이던스·EMA·Health Canada) 오프라인 파싱 테스트.

네트워크를 타지 않는다 — 각 소스 원문 구조를 축약한 픽스처로 선별(keep)·id 규칙·
발행일 파싱을 고정한다. 실제 원문 구조가 바뀌면 라이브 수집기의 하한 가드
(MIN_EXPECTED_*)가 error 를 반환하고, 이 테스트는 파서 계약 회귀를 잡는다.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

import library_collect_ema as ema
import library_collect_fda_guidance as fda
import library_collect_health_canada as hc

CATALOG_DIR = Path(__file__).resolve().parents[1] / "web" / "data" / "library"
PUBLIC_FIELDS = {
    "id", "code", "title_en", "title_ko", "doc_type", "published_date",
    "official_url", "ko_url", "pdf_url",
}
COLLECTORS = (fda, ema, hc)


def _catalog(source: str) -> list[dict]:
    payload = json.loads((CATALOG_DIR / f"{source}.json").read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else payload["items"]


def _fda_row(url_slug: str, title: str, *, center="Center for Drug Evaluation and Research",
             product="Drugs", status="Final", issued="05/03/2023") -> dict:
    href = fda.GUIDANCE_PATH + url_slug
    return {
        "title": f'<a href="{href}">{title}</a>',
        "field_center": center,
        "field_regulated_product_field": product,
        "field_final_guidance_1": status,
        "field_issue_datetime": issued,
    }


EMA_PROCEDURE_HTML = """
<div class="mt-4 ema-file-wrapper"><div data-ema-document-type="regulatory-procedural-guideline">
  <p class="file-title mb-1 fw-bold">Quality systems framework for Good Manufacturing Practice (GMP) inspectorates</p>
  <strong class="label">First published: </strong><span class="value"><time datetime="2024-08-01T10:00:00Z">01/08/2024</time></span>
  <a href="/en/documents/regulatory-procedural-guideline/quality-systems-framework-good-manufacturing-practice-gmp-inspectorates_en.pdf">View</a>
</div></div>
<div class="mt-4 ema-file-wrapper"><div data-ema-document-type="regulatory-procedural-guideline">
  <p class="file-title mb-1 fw-bold">Good distribution practice (GDP) inspection procedure</p>
  <strong class="label">First published: </strong><span class="value"><time datetime="2024-08-01T10:00:00Z">01/08/2024</time></span>
  <a href="/en/documents/regulatory-procedural-guideline/good-distribution-practice-gdp-inspection-procedure_en.pdf">View</a>
</div></div>
<div class="mt-4 ema-file-wrapper"><div data-ema-document-type="template-form">
  <p class="file-title mb-1 fw-bold">Union format for good manufacturing practice (GMP) certificate</p>
  <a href="/en/documents/template-form/union-format-good-manufacturing-practice-gmp-certificate_en.pdf">View</a>
</div></div>
"""

EMA_LISTING_HTML = """
<ul>
<li><a href="/en/manufacture-finished-dosage-form-human-scientific-guideline" title="x">Manufacture of the finished dosage form</a></li>
<li><a href="/en/ich-q9-quality-risk-management-scientific-guideline" title="x">ICH Q9 Quality risk management</a></li>
<li><a href="/en/manufacture-finished-dosage-form-human-scientific-guideline" title="dup">Duplicate link</a></li>
</ul>
"""

EMA_GUIDELINE_PAGE_HTML = """
<meta property="article:modified_time" content="2019-10-01T12:00:00+0200">
<div class="ema-file-wrapper"><strong class="label">First published: </strong>
<span class="value"><time datetime="2019-03-08T14:36:00Z">08/03/2019</time></span></div>
<div class="ema-file-wrapper"><strong class="label">First published: </strong>
<span class="value"><time datetime="2016-04-13T12:00:00Z">13/04/2016</time></span></div>
"""

HC_INDEX_HTML = """
<ul>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/good-manufacturing-practices/guidance-documents/gmp-guidelines-0001.html">Good manufacturing practices guide for drug products (GUI-0001)</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/establishment-licences/directives-guidance-documents-policies/gmp-guidelines-summary-0001.html">GUI-0001 summary</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/good-manufacturing-practices/guidance-documents/guidelines-temperature-control-drug-products-storage-transportation-0069-summary.html">Guidelines for environmental control of drugs during storage and transportation (GUIDE-0069)</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/good-manufacturing-practices/guidance-documents/annex-11-computerized-systems-0050.html">Annex 11 to the good manufacturing practices guide: Computerized Systems</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/establishment-licences/directives-guidance-documents-policies/medical-device-licensing-0016.html">Guidance on medical device establishment licensing (GUI-0016)</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/good-pharmacovigilance-practices-0102.html">Good Pharmacovigilance Practices (GVP) Guidelines (GUI-0102)</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/compliance-enforcement/good-manufacturing-practices/audit-report-form-0211.html">Good Manufacturing Practices - Audit Report Form (FRM-0211)</a></li>
<li><a href="/en/health-canada/services/drugs-health-products/no-code-here.html">Inspections</a></li>
<li><a href="/en/revenue-agency/other-department.html">Not health canada (GUI-0001)</a></li>
</ul>
"""

HC_DOC_PAGE_HTML = """
<meta name="dcterms.title" content="Good manufacturing practices guide for drug products (GUI-0001) - Summary">
<meta name="dcterms.issued" content="2018-02-28">
<meta name="dcterms.modified" content="2026-06-29">
<meta name="dcterms.type" content="guidance">
"""

HC_FORM_PAGE_HTML = """
<meta name="dcterms.title" content="Good Manufacturing Practices - Audit Report Form (FRM-0211)">
<meta name="dcterms.type" content="forms">
"""

HC_GUIDE_VARIANT_PAGE_HTML = """
<meta name="dcterms.title" content="Guidelines for environmental control of drugs during storage and transportation (GUIDE-0069)">
<meta name="dcterms.type" content="guidance">
"""

HC_URL_CODED_PAGE_HTML = """
<meta name="dcterms.title" content="Guidelines for Temperature Control of Drug Products during Storage and Transportation (GUI-0069)">
<meta name="dcterms.issued" content="2011-01-21">
<meta name="dcterms.type" content="guidance">
"""


class PluginContractTest(unittest.TestCase):
    def test_every_collector_exposes_the_plugin_contract(self):
        for module in COLLECTORS:
            with self.subTest(module=module.__name__):
                self.assertTrue(module.LIBRARY_SOURCE)
                self.assertTrue(callable(module.collect_library_items))
                self.assertTrue((CATALOG_DIR / f"{module.LIBRARY_SOURCE}.json").exists())

    def test_anchor_maps_cover_every_curated_id_of_their_catalog(self):
        for module in (fda, ema):
            with self.subTest(module=module.__name__):
                curated = {item["id"] for item in _catalog(module.LIBRARY_SOURCE)}
                self.assertEqual(curated - set(module._ID_ANCHORS.values()), set())

    def test_health_canada_id_rule_reproduces_every_curated_id(self):
        for item in _catalog("health_canada"):
            built = hc.build_item(item["code"], item["official_url"], item["title_en"], "")
            self.assertEqual(built["id"], item["id"])


class FdaGuidanceTest(unittest.TestCase):
    def test_keeps_quality_scope_and_drops_out_of_scope_rows(self):
        rows = [
            _fda_row("q9r1-quality-risk-management", "Q9(R1) Quality Risk Management"),
            _fda_row("sterile-drug-products-produced-aseptic-processing-current-good-manufacturing-practice",
                     "Sterile Drug Products Produced by Aseptic Processing"),
            _fda_row("m13a-bioequivalence", "M13A Bioequivalence for Immediate-Release Forms"),
            _fda_row("q4b-annex-8-sterility-test", "Q4B Annex 8: Sterility Test General Chapter"),
            _fda_row("food-cgmp", "Current Good Manufacturing Practice for Food",
                     center="Human Foods Program", product="Food &amp; Beverages"),
            _fda_row("device-quality-system", "Quality System Regulation for Devices",
                     center="Center for Devices and Radiological Health", product="Medical Devices"),
        ]
        items, scanned = fda.build_items(rows)
        self.assertEqual(scanned, len(rows))
        self.assertEqual([item["title_en"] for item in items],
                         ["Q9(R1) Quality Risk Management",
                          "Sterile Drug Products Produced by Aseptic Processing"])

    def test_keeps_row_with_empty_regulated_product_field(self):
        row = _fda_row("testing-glycerin-propylene-glycol-maltitol-solution-hydrogenated-starch-hydrolysate-sorbitol",
                       "Testing of Glycerin and Other High-Risk Drug Components for Diethylene Glycol",
                       product="")
        items, _ = fda.build_items([row])
        self.assertEqual(items[0]["id"], "fda-testing-high-risk-components-deg-eg")

    def test_derives_code_date_doc_type_and_absolute_url(self):
        items, _ = fda.build_items([
            _fda_row("q9r1-quality-risk-management",
                     "Q9(R1) Quality Risk Management: Guidance for Industry",
                     status="Draft", issued="09/05/2024")])
        item = items[0]
        self.assertEqual(item["code"], "Q9(R1)")
        self.assertEqual(item["title_en"], "Q9(R1) Quality Risk Management")
        self.assertEqual(item["doc_type"], "Draft guidance")
        self.assertEqual(item["published_date"], "2024-09-05")
        self.assertTrue(item["official_url"].startswith("https://www.fda.gov/regulatory-information/"))
        self.assertEqual(set(item) - PUBLIC_FIELDS, set())

    def test_new_document_gets_stable_hash_id_and_duplicates_collapse(self):
        row = _fda_row("brand-new-cgmp-guidance", "Brand New CGMP Guidance")
        first, _ = fda.build_items([row, dict(row)])
        second, _ = fda.build_items([row])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.assertTrue(first[0]["id"].startswith("fda-"))

    def test_bad_issue_date_is_dropped_not_guessed(self):
        items, _ = fda.build_items([
            _fda_row("q9r1-quality-risk-management", "Q9(R1) Quality Risk Management",
                     issued="not-a-date")])
        self.assertNotIn("published_date", items[0])


class EmaTest(unittest.TestCase):
    def test_procedure_parser_keeps_gmp_procedures_only(self):
        items = ema.parse_procedures(EMA_PROCEDURE_HTML)
        self.assertEqual([item["id"] for item in items], ["ema-gmp-001"])
        item = items[0]
        self.assertEqual(item["published_date"], "2024-08-01")
        self.assertEqual(item["doc_type"], "regulatory-procedural-guideline")
        self.assertEqual(item["official_url"], item["pdf_url"])
        self.assertTrue(item["official_url"].startswith(ema.SITE_BASE))
        self.assertEqual(set(item) - PUBLIC_FIELDS, set())

    def test_guideline_links_are_deduped_and_anchored(self):
        links = ema.parse_guideline_links(EMA_LISTING_HTML)
        self.assertEqual(len(links), 2)
        item = ema.build_guideline_item(*links[0], EMA_GUIDELINE_PAGE_HTML)
        self.assertEqual(item["id"], "ema-gmp-017")
        self.assertEqual(item["doc_type"], "scientific-guideline")
        self.assertEqual(item["published_date"], "2019-03-08")

    def test_first_published_prefers_first_block_and_tolerates_absence(self):
        self.assertEqual(ema.first_published(EMA_GUIDELINE_PAGE_HTML), "2019-03-08")
        self.assertEqual(ema.first_published("<p>no dates here</p>"), "")

    def test_new_guideline_gets_stable_hash_id(self):
        url = ema.SITE_BASE + "/en/brand-new-topic-scientific-guideline"
        first = ema.build_guideline_item(url, "Brand new topic", "")
        second = ema.build_guideline_item(url, "Brand new topic", "")
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(first["id"].startswith("ema-"))
        self.assertNotIn("published_date", first)


class HealthCanadaTest(unittest.TestCase):
    def test_index_parser_keeps_gui_coded_drug_documents_only(self):
        entries = hc.parse_index(HC_INDEX_HTML)
        self.assertEqual(sorted({code for code, _, _, _ in entries}),
                         ["GUI-0001", "GUI-0050", "GUI-0069", "GUI-0211"])

    def test_guide_prefix_variant_counts_as_an_explicit_label_code(self):
        by_code = hc.select_documents(hc.parse_index(HC_INDEX_HTML))
        self.assertTrue(by_code["GUI-0069"][2])
        self.assertIn("0069-summary.html", by_code["GUI-0069"][0])
        self.assertTrue(hc.keep_document("GUI-0069", HC_GUIDE_VARIANT_PAGE_HTML, True))

    def test_code_is_recovered_from_url_when_label_has_none(self):
        by_code = hc.select_documents(hc.parse_index(HC_INDEX_HTML))
        self.assertFalse(by_code["GUI-0050"][2])
        self.assertTrue(by_code["GUI-0001"][2])

    def test_duplicate_codes_resolve_to_the_gmp_path_url(self):
        by_code = hc.select_documents(hc.parse_index(HC_INDEX_HTML))
        self.assertIn(hc.GMP_PATH_MARKER, by_code["GUI-0001"][0])

    def test_other_code_families_guessed_from_the_url_are_rejected(self):
        # 실측 결함: FRM-0211 서식이 URL 끝 "-0211.html" 때문에 GUI-0211 로 오인됐다.
        self.assertFalse(hc.keep_document("GUI-0211", HC_FORM_PAGE_HTML, False))
        self.assertTrue(hc.keep_document("GUI-0069", HC_URL_CODED_PAGE_HTML, False))

    def test_non_guidance_document_types_are_rejected(self):
        policy_page = HC_DOC_PAGE_HTML.replace('content="guidance"', 'content="policies"')
        self.assertFalse(hc.keep_document("GUI-0001", policy_page, True))
        multi_page = HC_DOC_PAGE_HTML.replace('content="guidance"',
                                              'content="guidance;recommendations"')
        self.assertTrue(hc.keep_document("GUI-0001", multi_page, True))
        self.assertEqual(
            hc.build_item("GUI-0001", "https://www.canada.ca/x.html", "l", multi_page)["doc_type"],
            "guidance")

    def test_unreadable_page_keeps_only_label_anchored_codes(self):
        self.assertTrue(hc.keep_document("GUI-0001", "", True))
        self.assertFalse(hc.keep_document("GUI-0069", "", False))

    def test_document_page_metadata_drives_title_date_and_type(self):
        item = hc.build_item("GUI-0001", "https://www.canada.ca/x.html", "list label",
                             HC_DOC_PAGE_HTML)
        self.assertEqual(item["id"], "health-canada-gui-0001")
        self.assertEqual(item["code"], "GUI-0001")
        self.assertEqual(item["title_en"], "Good manufacturing practices guide for drug products")
        self.assertEqual(item["published_date"], "2018-02-28")
        self.assertEqual(item["doc_type"], "guidance")
        self.assertEqual(set(item) - PUBLIC_FIELDS, set())

    def test_falls_back_to_list_label_when_document_page_is_unavailable(self):
        item = hc.build_item("GUI-0069", "https://www.canada.ca/y.html",
                             "Temperature control guide (GUI-0069)", "")
        self.assertEqual(item["title_en"], "Temperature control guide")
        self.assertEqual(item["doc_type"], hc.DEFAULT_DOC_TYPE)
        self.assertNotIn("published_date", item)


class OfflineFailureTest(unittest.TestCase):
    """수집 실패는 빈 리스트가 아니라 error 로 표면화된다(계약)."""

    def test_collectors_return_error_when_the_network_call_fails(self):
        def boom(*args, **kwargs):
            raise RuntimeError("network down")

        for module, attr in ((fda, "http_get_json"), (ema, "http_get_html"),
                             (hc, "http_get_html")):
            with self.subTest(module=module.__name__):
                original = getattr(module, attr)
                setattr(module, attr, boom)
                try:
                    items, error = module.collect_library_items(date(2026, 7, 20))
                finally:
                    setattr(module, attr, original)
                self.assertEqual(items, [])
                self.assertTrue(error)


if __name__ == "__main__":
    unittest.main()
