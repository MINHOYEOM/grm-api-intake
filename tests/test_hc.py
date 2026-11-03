"""Health Canada 수집기(collect_hc) 회귀 — P7(generic→Biologic)·P8(firm 교정).

opendata 피드는 브랜드명만 주므로(Hizentra 류), 상세 페이지에서 끌어온 유효성분
(Strength)을 compute_modality 가 보는 텍스트(body)에 주입해야 생물주사제가 Biologic
으로 잡힌다. 또 Organization("Drugs and health products")은 회사가 아니므로 firm 으로
쓰지 않는다(실제 회사 라벨이 있을 때만 firm, 없으면 빈 값 → 카드 '원문 미기재').

상세 페이지 fetch 는 단위테스트에서 detail_fetcher 스텁으로 주입한다(네트워크 없음).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_hc as hc
import collect_intake as ci
from datetime import date

START, END = date(2026, 1, 1), date(2026, 12, 31)


def _rec(**over):
    base = {
        "NID": "82142",
        "Title": "Hizentra: out of specification",
        "URL": "/en/alert-recall/hizentra-oos",
        "Organization": "Drugs and health products",
        "Product": "Hizentra",
        "Issue": "Product quality",
        "Category": "Drugs",
        "Recall class": "Type II",
        "Last updated": "2026-06-03",
    }
    base.update(over)
    return base


def _modality(item):
    return ci.compute_modality(item.raw_payload, item.headline, item.body,
                               item.type_or_class, item.firm)


class TestP7GenericToBiologic(unittest.TestCase):
    def test_brand_only_biologic_via_detail_strength(self):
        # 피드는 브랜드명(Hizentra)뿐 → 상세 Strength(IMMUNOGLOBULIN (HUMAN))로 Biologic.
        det = lambda u: {"product name": "Hizentra", "dosage form": "Solution",
                         "strength": "IMMUNOGLOBULIN (HUMAN) 200 mg/mL"}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertIn("IMMUNOGLOBULIN (HUMAN)", item.body)   # body 에 유효성분 주입됨
        self.assertEqual(_modality(item), ci.MODALITY_BIOLOGIC)

    def test_brand_only_biologic_via_curated_dict_when_detail_missing(self):
        # GAP-2: 상세 fetch 실패({})로 유효성분 텍스트가 없어도, 큐레이티드 브랜드 사전
        # (MODALITY_BIOLOGIC_BRANDS: 'hizentra')이 백업으로 Biologic 을 보장한다.
        # (종전 graceful 폴백은 Chemical 이었으나 GAP-2가 의도적으로 교정 — 사전 주석 참조)
        item = hc._to_item(_rec(), START, END, detail_fetcher=lambda u: {})
        self.assertNotIn("유효성분", item.body)            # 상세 유효성분 주입은 여전히 없음
        self.assertEqual(_modality(item), ci.MODALITY_BIOLOGIC)

    def test_no_fetcher_brand_only_biologic_via_curated_dict(self):
        # detail_fetcher 없음 → 피드 단독. 브랜드 'Hizentra' 가 사전으로 Biologic (GAP-2 백업).
        item = hc._to_item(_rec(), START, END)
        self.assertEqual(_modality(item), ci.MODALITY_BIOLOGIC)

    def test_feed_immune_globulin_space_form_is_biologic(self):
        # 피드 Product 에 'Immune globulin'(공백형) — 상세 없이도 Biologic (용어 보강).
        rec = _rec(Product="Octagam 10% (Immune globulin intravenous, Human)",
                   Title="Octagam 10%: quality issue")
        item = hc._to_item(rec, START, END)
        self.assertEqual(_modality(item), ci.MODALITY_BIOLOGIC)


class TestA4DosageFormSurfacesToItem(unittest.TestCase):
    """A4 회귀: 파서 키('dosage form', 공백)가 _to_item 까지 표면화돼야 한다.

    파서는 `label.strip().lower()` 로 키를 만들어 'Dosage Form' → 'dosage form'(공백).
    읽기측이 'dosage_form'(밑줄)으로 읽던 버그로 '제형:' 가 항상 공란이었다. 기존
    테스트는 _parse_detail_html 만 격리 검사해 못 잡았으므로 _to_item 경유로 단언한다.
    """

    def test_dosage_form_reaches_body_and_raw_payload(self):
        det = lambda u: {"dosage form": "Solution",
                         "strength": "IMMUNOGLOBULIN (HUMAN) 200 mg/mL"}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertIn("제형: Solution", item.body)
        self.assertEqual(item.raw_payload["dosage_form_detail"], "Solution")


class TestP7ChemicalNotMisclassified(unittest.TestCase):
    """반례: 화학 제품이 Biologic 으로 오분류되지 않아야 한다."""

    def test_chemical_tablet_with_detail_stays_chemical(self):
        det = lambda u: {"strength": "Valsartan 50 mg", "dosage form": "Tablet"}
        rec = _rec(Product="Drug X 50 mg tablets", Title="Drug X recall",
                   Issue="nitrosamine impurity")
        item = hc._to_item(rec, START, END, detail_fetcher=det)
        self.assertEqual(_modality(item), ci.MODALITY_CHEMICAL)

    def test_injection_is_not_misclassified_as_biologic(self):
        # [2026-09-21] 주사제는 **제형** 축이라 제품군을 가르지 못한다. 구 구현은 이걸
        # Chemical 근거로 썼지만(그래서 무균 주사제 전반이 합성으로 갔다), 이 클래스가
        # 지키려는 성질은 "화학 제품이 Biologic 으로 오분류되지 않는다" 이다 —
        # 그 성질은 그대로 유지되고, 근거가 없으므로 판정을 보류한다.
        det = lambda u: {"strength": "Heparin sodium 25000 unit/500 mL",
                         "dosage form": "Solution for injection"}
        rec = _rec(Product="Heparin Sodium in 5% Dextrose injection",
                   Title="Heparin Sodium: out of specification")
        item = hc._to_item(rec, START, END, detail_fetcher=det)
        self.assertNotEqual(_modality(item), ci.MODALITY_BIOLOGIC)
        self.assertEqual(_modality(item), ci.MODALITY_UNKNOWN)


class TestP8FirmMapping(unittest.TestCase):
    """Organization('Drugs and health products')을 firm 으로 쓰지 않는다."""

    def test_organization_dept_is_not_used_as_firm(self):
        item = hc._to_item(_rec(), START, END, detail_fetcher=lambda u: {})
        self.assertEqual(item.firm, "")                   # 부서명 → firm 으로 누수 금지
        self.assertNotIn("company", item.raw_payload)

    def test_real_company_label_becomes_firm(self):
        # 상세에 실제 회사 라벨이 있으면 firm 으로 채운다.
        det = lambda u: {"company": "BD Canada", "strength": "Chlorhexidine 2%"}
        rec = _rec(Product="ChloraPrep 1 mL", Title="BD Canada ChloraPrep recall")
        item = hc._to_item(rec, START, END, detail_fetcher=det)
        self.assertEqual(item.firm, "BD Canada")
        self.assertEqual(item.raw_payload.get("company"), "BD Canada")

    def test_brand_label_not_preferred_over_real_company_label(self):
        # 'Brand' 셀은 제품 브랜드일 수 있어 실제 회사 라벨보다 우선하지 않는다(판단 유지).
        det = lambda u: {"brand": "Hizentra", "company": "CSL Behring Canada Inc.",
                         "strength": "IMMUNOGLOBULIN (HUMAN) 200 mg/mL"}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertEqual(item.firm, "CSL Behring Canada Inc.")

    def test_brand_is_not_a_company_fallback(self):
        # [2026-11-03] Brand(s) 칸에 소비자 브랜드(Equate·Aleve·Claritin…)가 든 페이지가 있다 —
        # 07-20 폴백이 그 6장을 업체로 발행했다. 회사 칸이 없으면 업체는 비고(→ 카드 "미확인"),
        # 브랜드는 업체가 아니라 별도 값(raw_payload["brand"])으로 남는다.
        det = lambda u: {"brand": "Equate", "product name": "Equate Antacid"}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertEqual(item.firm, "")
        self.assertNotIn("company", item.raw_payload)
        self.assertEqual(item.raw_payload["brand"], "Equate")

    def test_companies_block_beats_brand(self):
        det = lambda u: {"brand": "Equate", "_companies": "Guardian Drug Company Inc."}
        item = hc._to_item(_rec(), START, END, detail_fetcher=det)
        self.assertEqual(item.firm, "Guardian Drug Company Inc.")
        self.assertEqual(item.raw_payload["company"], "Guardian Drug Company Inc.")
        self.assertEqual(item.raw_payload["brand"], "Equate")


class TestPublishedDate(unittest.TestCase):
    """발행일 = 페이지의 "Original published date". 정정일은 별도 값, 정정만 뜬 옛 회수는 건너뛴다."""

    def test_first_published_is_the_card_date(self):
        det = lambda u: {"_first_published": "2026-06-01"}
        item = hc._to_item(_rec(**{"Last updated": "2026-06-03"}), START, END, detail_fetcher=det)
        self.assertEqual(item.date_iso, "2026-06-01")
        self.assertEqual(item.raw_payload["first_published"], "2026-06-01")
        self.assertEqual(item.raw_payload["correction_date"], "2026-06-03")

    def test_no_correction_date_when_same_day(self):
        det = lambda u: {"_first_published": "2026-06-03"}
        item = hc._to_item(_rec(**{"Last updated": "2026-06-03"}), START, END, detail_fetcher=det)
        self.assertEqual(item.date_iso, "2026-06-03")
        self.assertNotIn("correction_date", item.raw_payload)

    def test_feed_date_when_page_has_no_original_date(self):
        item = hc._to_item(_rec(), START, END, detail_fetcher=lambda u: {})
        self.assertEqual(item.date_iso, "2026-06-03")
        self.assertNotIn("first_published", item.raw_payload)

    def test_correction_of_a_recall_older_than_window_is_skipped(self):
        # 같은 NID 정정 — 최초 게시는 이전 윈도우에서 이미 카드가 됐다(PMS-Fampridine 중복 사례).
        det = lambda u: {"_first_published": "2025-12-20"}
        item = hc._to_item(_rec(**{"Last updated": "2026-06-03"}), START, END, detail_fetcher=det)
        self.assertIsNone(item)

    def test_window_still_judged_on_last_updated(self):
        # 피드 날짜가 윈도우 밖이면 상세를 열기 전에 탈락(네트워크 절약 유지).
        calls = []
        det = lambda u: calls.append(u) or {}
        item = hc._to_item(_rec(**{"Last updated": "2027-02-01"}), START, END, detail_fetcher=det)
        self.assertIsNone(item)
        self.assertEqual(calls, [])


class TestDetailParser(unittest.TestCase):
    """_parse_detail_html / _detail_company 순수 파싱."""

    def test_parse_data_label_cells(self):
        html = ('<table><tr>'
                '<td class="x" data-label="Product Name">Hizentra</td>'
                '<td data-label="Strength">IMMUNOGLOBULIN (HUMAN) 200 mg/mL</td>'
                '<td data-label="Dosage Form">Solution</td>'
                '</tr></table>')
        d = hc._parse_detail_html(html)
        self.assertEqual(d["strength"], "IMMUNOGLOBULIN (HUMAN) 200 mg/mL")
        self.assertEqual(d["dosage form"], "Solution")
        self.assertEqual(hc._detail_company(d), "")        # 회사 라벨 없음

    def test_company_label_detected(self):
        self.assertEqual(hc._detail_company({"manufacturer": "Acme Pharma"}),
                         "Acme Pharma")

    def test_empty_html_returns_empty(self):
        self.assertEqual(hc._parse_detail_html(""), {})

    # ── [2026-11-03] _detail_company: Companies 칸 > 회사 라벨 > 빈 값(Brand 는 업체가 아니다) ──
    def test_brand_is_never_the_company(self):
        self.assertEqual(hc._detail_company({"brand": "Apotex Inc."}), "")
        self.assertEqual(hc._detail_brand({"brand": "Aleve"}), "Aleve")

    def test_company_label_still_works_without_brand(self):
        self.assertEqual(
            hc._detail_company({"brand": "Retail Brand", "manufacturer": "Acme Pharma"}),
            "Acme Pharma")

    def test_companies_block_wins_over_label(self):
        self.assertEqual(
            hc._detail_company({"_companies": "Bayer Inc.", "manufacturer": "Acme Pharma"}),
            "Bayer Inc.")

    def test_neither_company_nor_brand_label_returns_empty(self):
        self.assertEqual(hc._detail_company({"product name": "X"}), "")

    # ── "Companies" 칸·"Original published date" 파싱(실제 페이지 모양) ──
    @staticmethod
    def _page(companies_inner, original="2026-10-30"):
        return (
            '<div class="view-content"><div><strong class="views-label">Original published date: </strong>'
            f'<time datetime="{original}T15:57:44-04:00">{original}</time></div></div>'
            '<div class="field field--name-companies"><div class="field--label">Companies</div>'
            f'<div class="field field--name-field-companies field--item">{companies_inner}</div></div>'
            '<div class="field field--name-node-id"><div class="field--label">Published by</div>')

    def test_companies_name_and_address_in_one_paragraph(self):
        html = self._page('<table><tbody><tr><td><p>Apotex Inc. 150 Signet Drive Toronto ON, M9L 1T9</p></td></tr></tbody></table>')
        d = hc._parse_detail_html(html)
        self.assertEqual(d["_companies"], "Apotex Inc.")
        self.assertEqual(d["_first_published"], "2026-10-30")

    def test_companies_first_paragraph_is_the_name(self):
        html = self._page('<meta name="Generator" content="Microsoft Excel 15"><style></style><table><tr><td>'
                          '<p>Guardian Drug Company Inc.    </p><p>2 Charles Court, </p>'
                          '<p>Dayton, New Jersey </p></td></tr></table>')
        self.assertEqual(hc._parse_detail_html(html)["_companies"], "Guardian Drug Company Inc.")

    def test_recalling_firm_prefix_is_stripped_and_bare_prefix_skipped(self):
        a = self._page("<p>Recalling Firm Dr. Reddy's Laboratories Canada Inc. 5580 Explorer Dr</p>")
        self.assertEqual(hc._parse_detail_html(a)["_companies"], "Dr. Reddy's Laboratories Canada Inc.")
        b = self._page("<p>Recalling Firm:</p><p>ATTITUDE</p>")
        self.assertEqual(hc._parse_detail_html(b)["_companies"], "ATTITUDE")

    def test_company_name_that_starts_with_digits_is_kept(self):
        html = self._page("<p>9055-7588 Quebec Inc. (Attitude) 900 5605 De Gaspe</p>")
        self.assertEqual(hc._parse_detail_html(html)["_companies"], "9055-7588 Quebec Inc. (Attitude)")

    def test_empty_companies_block_yields_no_company(self):
        html = self._page("")
        d = hc._parse_detail_html(html)
        self.assertNotIn("_companies", d)
        self.assertEqual(hc._detail_company(d), "")

    def test_page_without_companies_block_has_no_company_key(self):
        d = hc._parse_detail_html('<td data-label="Brand">Servier Canada Inc.</td>')
        self.assertNotIn("_companies", d)
        self.assertEqual(hc._detail_company(d), "")
        self.assertEqual(hc._detail_brand(d), "Servier Canada Inc.")


class TestDetailFetchStats(unittest.TestCase):
    """상세 보강 집계 카운터(_fetch_recall_detail stats) — 대량 실패 표면화 회귀.

    네트워크 없이 requests.get 스텁으로 성공/실패를 만든다(sleep 은 no-op 로 치환).
    stats 카운터가 collect_hc 의 run 말미 요약(실패 N/시도 M)을 뒷받침한다.
    """

    _URL = "https://recalls-rappels.canada.ca/en/alert-recall/x"

    def test_failure_increments_attempted_and_failed(self):
        stats = {"attempted": 0, "failed": 0}
        with mock.patch("collect_hc.time.sleep", lambda *_a: None), \
             mock.patch("collect_hc.requests.get", side_effect=RuntimeError("boom")):
            out = hc._fetch_recall_detail(self._URL, stats)
        self.assertEqual(out, {})                          # 폴백은 여전히 빈 dict
        self.assertEqual(stats, {"attempted": 1, "failed": 1})

    def test_success_counts_attempt_only(self):
        class _Resp:
            content = b'<table><tr><td data-label="Strength">X 10 mg</td></tr></table>'

            def raise_for_status(self):
                pass

        stats = {"attempted": 0, "failed": 0}
        with mock.patch("collect_hc.time.sleep", lambda *_a: None), \
             mock.patch("collect_hc.requests.get", return_value=_Resp()):
            out = hc._fetch_recall_detail(self._URL, stats)
        self.assertEqual(out.get("strength"), "X 10 mg")   # 파싱 성공
        self.assertEqual(stats, {"attempted": 1, "failed": 0})

    def test_skipped_url_is_not_an_attempt(self):
        # 항목 링크 없음(url == HC_BASE) → 시도 아님 → 집계 제외.
        stats = {"attempted": 0, "failed": 0}
        out = hc._fetch_recall_detail(hc.HC_BASE, stats)
        self.assertEqual(out, {})
        self.assertEqual(stats, {"attempted": 0, "failed": 0})

    def test_none_stats_is_backward_compatible(self):
        # stats 미전달(직접 호출·기존 계약) 시 예외 없이 {} 폴백.
        with mock.patch("collect_hc.time.sleep", lambda *_a: None), \
             mock.patch("collect_hc.requests.get", side_effect=RuntimeError("boom")):
            self.assertEqual(hc._fetch_recall_detail(self._URL), {})


if __name__ == "__main__":
    unittest.main()
