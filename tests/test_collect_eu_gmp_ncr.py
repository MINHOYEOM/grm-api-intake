# -*- coding: utf-8 -*-
"""collect_eu_gmp_ncr 수집기 회귀.

EudraGMDPClient 는 세션상태 의존 실네트워크 클라이언트라 unittest 에서는
`collect_eu_gmp_ncr.EudraGMDPClient` 자체를 페이크로 치환한다(네트워크 없음).
PDF 아카이브(_archive_pdf)는 urllib.request.urlopen 을 패치해 검증한다.
"""
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_eu_gmp_ncr as mod
from eudragmdp_client import EudraGMDPError, NCRRecord

START, END = date(2026, 1, 1), date(2026, 12, 31)

# SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY 는 로컬 shell 에 이미 설정돼 있을 수 있으므로
# 아카이브 관련 검증이 아닌 모든 테스트는 명시적으로 두 키를 지운 환경에서 돈다.
_NO_CREDS_ENV = {k: v for k, v in os.environ.items()
                 if k not in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}


def _rec(**over):
    base = dict(
        report_no="NCR-2026-001",
        doc_ref="EEA-DOC-0001",
        mia_number="MIA-DE-000123",
        site_name="Acme Pharma GmbH",
        site_address="Industriestrasse 1",
        oms_location="OMS-DE-0001",
        city="Berlin",
        postcode="10115",
        country="Germany",
        inspection_end_date="2026-05-10",
        issue_date="2026-06-15",
    )
    base.update(over)
    return NCRRecord(**base)


class _FakeClient:
    """EudraGMDPClient 대역. iter_pages 는 제너레이터(yield)라 지연평가된다."""

    def __init__(self, pages=None, detail_map=None, fail_docs=None,
                 pdf_bytes=b"%PDF-fake", session_error=None):
        self._pages = pages or []
        self.detail_map = detail_map or {}
        self.fail_docs = fail_docs or set()
        self.pdf_bytes = pdf_bytes
        self._session_error = session_error

    def iter_pages(self, from_date, to_date):
        if self._session_error is not None:
            raise self._session_error
        for idx, rows in self._pages:
            yield idx, rows

    def fetch_detail(self, rec):
        if rec.doc_ref in self.fail_docs:
            raise EudraGMDPError(f"drilldown failed for {rec.doc_ref}")
        info = self.detail_map.get(rec.doc_ref, {})
        rec.nature = info.get("nature", "Failure to comply with GMP principle 1")
        rec.action = info.get("action", "Statement of non-compliance issued")
        rec.authority_country = info.get("authority_country", "Germany")
        rec.product_scope = info.get("product_scope", "Human Medicinal Products")
        rec.operations = info.get("operations", "1 NON-COMPLIANT MANUFACTURING OPERATIONS")
        rec.detail_ok = True
        return rec

    def fetch_pdf(self, rec):
        rec.pdf_bytes = self.pdf_bytes
        return self.pdf_bytes


class _FakeHTTPResp:
    """urllib.request.urlopen(...) 의 컨텍스트매니저 대역."""

    def __init__(self, code=200):
        self._code = code

    def getcode(self):
        return self._code

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patched(client):
    """collect_eu_gmp_ncr.EudraGMDPClient() 호출이 주어진 페이크를 반환하도록 패치."""
    return mock.patch("collect_eu_gmp_ncr.EudraGMDPClient", return_value=client)


class TestHappyPath(unittest.TestCase):
    def test_two_records_across_pages(self):
        rec1 = _rec(doc_ref="EEA-DOC-0001", report_no="NCR-2026-001",
                     site_name="Acme Pharma GmbH", issue_date="2026-06-15")
        rec2 = _rec(doc_ref="EEA-DOC-0002", report_no="NCR-2026-002",
                     site_name="Beta Biologics S.p.A.", issue_date="2026-07-01",
                     country="Italy")
        detail_map = {
            "EEA-DOC-0001": {"action": "Statement of non-compliance issued."},
            "EEA-DOC-0002": {"action": "Product recall initiated by the NCA."},
        }
        client = _FakeClient(pages=[(0, [rec1]), (1, [rec2])], detail_map=detail_map)
        with mock.patch.dict(os.environ, _NO_CREDS_ENV, clear=True), _patched(client):
            items, err = mod.collect_eu_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        by_ref = {i.document_id: i for i in items}
        self.assertEqual(set(by_ref), {"EEA-DOC-0001", "EEA-DOC-0002"})

        item1 = by_ref["EEA-DOC-0001"]
        self.assertEqual(item1.source, "EU GMP NCR (EudraGMDP)")
        self.assertEqual(item1.document_id, "EEA-DOC-0001")   # doc_ref, NOT report_no
        self.assertNotEqual(item1.document_id, rec1.report_no)
        self.assertEqual(item1.date_iso, "2026-06-15")        # issue_date
        self.assertEqual(item1.firm, "Acme Pharma GmbH")      # site_name
        self.assertIn("ncr_nature", item1.raw_payload)
        self.assertTrue(item1.raw_payload["ncr_nature"])
        self.assertEqual(item1.raw_payload["doc_ref"], "EEA-DOC-0001")
        self.assertEqual(item1.evidence_candidate, "A")
        self.assertIn(item1.signal_tier, {"Tier 2", "Tier 3"})
        self.assertEqual(item1.signal_tier, "Tier 2")          # no recall/suspension wording

        item2 = by_ref["EEA-DOC-0002"]
        self.assertEqual(item2.signal_tier, "Tier 3")           # action contains "recall"


class TestDedupByDocRef(unittest.TestCase):
    def test_same_doc_ref_different_report_no_dedups_to_one(self):
        rec_a = _rec(doc_ref="EEA-DOC-DUP", report_no="NCR-2026-010",
                     site_name="Site A", issue_date="2026-03-01")
        rec_b = _rec(doc_ref="EEA-DOC-DUP", report_no="NCR-2026-011",
                     site_name="Site A (second row)", issue_date="2026-03-01")
        client = _FakeClient(pages=[(0, [rec_a, rec_b])])
        with mock.patch.dict(os.environ, _NO_CREDS_ENV, clear=True), _patched(client):
            items, err = mod.collect_eu_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "EEA-DOC-DUP")


class TestPerRecordResilience(unittest.TestCase):
    def test_one_of_two_detail_failures_skips_only_that_record(self):
        rec_ok = _rec(doc_ref="EEA-DOC-OK", report_no="NCR-2026-020",
                      issue_date="2026-04-10")
        rec_bad = _rec(doc_ref="EEA-DOC-BAD", report_no="NCR-2026-021",
                       issue_date="2026-04-11")
        client = _FakeClient(pages=[(0, [rec_ok, rec_bad])], fail_docs={"EEA-DOC-BAD"})
        with mock.patch.dict(os.environ, _NO_CREDS_ENV, clear=True), _patched(client):
            items, err = mod.collect_eu_gmp_ncr(START, END)

        # 실패율 1/2 = 50% → _MAX_RECORD_FAILURE_RATIO(0.5) 초과 아님 → 정상 처리.
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "EEA-DOC-OK")


class TestHighFailureRatioPromotesError(unittest.TestCase):
    def test_more_than_half_failing_returns_error(self):
        rec_ok = _rec(doc_ref="EEA-DOC-OK", report_no="NCR-2026-030",
                      issue_date="2026-05-01")
        rec_bad1 = _rec(doc_ref="EEA-DOC-BAD1", report_no="NCR-2026-031",
                        issue_date="2026-05-02")
        rec_bad2 = _rec(doc_ref="EEA-DOC-BAD2", report_no="NCR-2026-032",
                        issue_date="2026-05-03")
        client = _FakeClient(pages=[(0, [rec_ok, rec_bad1, rec_bad2])],
                              fail_docs={"EEA-DOC-BAD1", "EEA-DOC-BAD2"})
        with mock.patch.dict(os.environ, _NO_CREDS_ENV, clear=True), _patched(client):
            items, err = mod.collect_eu_gmp_ncr(START, END)

        # 실패율 2/3 ≈ 66.7% > 50% → 구조 변경 의심 error 승격, items 는 빈 리스트.
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("실패율", err)


class TestSessionFailure(unittest.TestCase):
    def test_iter_pages_error_returns_empty_and_error(self):
        client = _FakeClient(session_error=EudraGMDPError("search POST failed: HTTP 500"))
        with mock.patch.dict(os.environ, _NO_CREDS_ENV, clear=True), _patched(client):
            items, err = mod.collect_eu_gmp_ncr(START, END)

        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("세션/검색", err)


class TestPdfArchive(unittest.TestCase):
    def test_archived_with_creds_and_mocked_upload(self):
        rec = _rec(doc_ref="EEA-DOC-ARCH", report_no="NCR-2026-040",
                   issue_date="2026-06-01")
        client = _FakeClient(pages=[(0, [rec])])
        env = dict(_NO_CREDS_ENV)
        env["SUPABASE_URL"] = "https://example.supabase.co"
        env["SUPABASE_SERVICE_ROLE_KEY"] = "service-role-test-key"

        with mock.patch.dict(os.environ, env, clear=True), _patched(client), \
             mock.patch("collect_eu_gmp_ncr.urllib.request.urlopen",
                        return_value=_FakeHTTPResp(200)) as m_urlopen:
            items, err = mod.collect_eu_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        item = items[0]
        expected_url = ("https://example.supabase.co/storage/v1/object/public/"
                        "eudragmdp-ncr/ncr/EEA-DOC-ARCH.pdf")
        self.assertEqual(item.official_url, expected_url)
        self.assertTrue(item.raw_payload["pdf_archived"])
        self.assertEqual(item.raw_payload["pdf_archived_url"], expected_url)
        m_urlopen.assert_called_once()

    def test_no_creds_skips_archive_and_leaves_fallback(self):
        rec = _rec(doc_ref="EEA-DOC-NOARCH", report_no="NCR-2026-041",
                   issue_date="2026-06-02")
        client = _FakeClient(pages=[(0, [rec])])

        with mock.patch.dict(os.environ, _NO_CREDS_ENV, clear=True), _patched(client), \
             mock.patch("collect_eu_gmp_ncr.urllib.request.urlopen") as m_urlopen:
            items, err = mod.collect_eu_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertFalse(item.raw_payload["pdf_archived"])
        self.assertEqual(item.official_url, "")   # card_scaffold 폴백에 위임
        m_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
