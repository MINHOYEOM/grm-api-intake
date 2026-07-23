# -*- coding: utf-8 -*-
"""collect_mhra_gmp_ncr 수집기 회귀.

MHRAGmpNCRClient 는 실네트워크 클라이언트라 unittest 에서는
`collect_mhra_gmp_ncr.MHRAGmpNCRClient` 자체를 페이크로 치환한다(네트워크 없음).
EudraGMDP 형제 테스트와 동형이나 PDF/Storage 아카이브 계층이 없어(MHRA 상세 페이지가
영속 official_url) 아카이브 관련 클래스는 없고, 대신 official_url=상세 페이지를 검증한다.
"""
import os
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import collect_mhra_gmp_ncr as mod
from mhra_gmdp_client import MHRAGmpError, MHRARecord

START, END = date(2019, 1, 1), date(2026, 12, 31)


def _rec(**over):
    base = dict(
        report_no="Insp GMP 10001/0001-0001",
        slug="insp-gmp-100010001-0001",
        detail_url="https://cms.mhra.gov.uk/mhra/gmp/insp-gmp-100010001-0001",
        country="INDIA",
        inspection_date="2026-03-10",
    )
    base.update(over)
    return MHRARecord(**base)


class _FakeClient:
    """MHRAGmpNCRClient 대역."""

    def __init__(self, records=None, detail_map=None, fail_slugs=None,
                 list_error=None):
        self._records = records or []
        self.detail_map = detail_map or {}
        self.fail_slugs = fail_slugs or set()
        self._list_error = list_error

    def list_noncompliant(self):
        if self._list_error is not None:
            raise self._list_error
        return list(self._records)

    def fetch_detail(self, rec):
        if rec.slug in self.fail_slugs:
            raise MHRAGmpError(f"detail failed for {rec.slug}")
        info = self.detail_map.get(rec.slug, {})
        rec.manufacturer = info.get("manufacturer", "Acme Pharma Ltd")
        rec.site_country = info.get("site_country", rec.country)
        rec.authority = ("Medicines and Healthcare products Regulatory Agency "
                         "(United Kingdom)")
        rec.product_type = info.get("product_type", "Human Medicinal Products")
        rec.operations = info.get("operations", "[ 1.1 ] Sterile Products")
        rec.restriction = info.get("restriction", "")
        rec.nature = info.get("nature", "Critical finding regarding sterility assurance.")
        rec.action = info.get("action", "Statement of non-compliance issued.")
        rec.issue_date = info.get("issue_date", "2026-04-01")
        rec.detail_ok = True
        return rec


def _patched(client):
    return mock.patch("collect_mhra_gmp_ncr.MHRAGmpNCRClient", return_value=client)


class TestHappyPath(unittest.TestCase):
    def test_two_records(self):
        rec1 = _rec(slug="s1", report_no="Insp GMP A", detail_url="https://cms.mhra.gov.uk/mhra/gmp/s1")
        rec2 = _rec(slug="s2", report_no="Insp GMP B", detail_url="https://cms.mhra.gov.uk/mhra/gmp/s2",
                    country="CHINA")
        detail_map = {
            "s1": {"manufacturer": "Acme Pharma Ltd", "issue_date": "2026-04-01",
                   "action": "Statement of non-compliance issued."},
            "s2": {"manufacturer": "Beta Biologics", "issue_date": "2026-05-01",
                   "action": "Recall of batches initiated by the NCA."},
        }
        client = _FakeClient(records=[rec1, rec2], detail_map=detail_map)
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 2)
        by_id = {i.document_id: i for i in items}
        self.assertEqual(set(by_id), {"Insp GMP A", "Insp GMP B"})

        a = by_id["Insp GMP A"]
        self.assertEqual(a.source, "MHRA GMP NCR")
        self.assertEqual(a.document_id, "Insp GMP A")          # report_no, NOT slug
        self.assertEqual(a.date_iso, "2026-04-01")             # issue_date
        self.assertEqual(a.firm, "Acme Pharma Ltd")
        self.assertEqual(a.official_url, "https://cms.mhra.gov.uk/mhra/gmp/s1")  # 상세 페이지
        self.assertEqual(a.evidence_candidate, "A")
        self.assertTrue(a.raw_payload["ncr_nature"])
        self.assertEqual(a.raw_payload["mhra_detail_url"], "https://cms.mhra.gov.uk/mhra/gmp/s1")
        self.assertEqual(a.region_jurisdiction, "UK (MHRA)")
        self.assertEqual(a.signal_tier, "Tier 2")              # no recall/suspension wording

        b = by_id["Insp GMP B"]
        self.assertEqual(b.signal_tier, "Tier 3")              # action contains "recall"


class TestDedupByReportNo(unittest.TestCase):
    def test_same_report_no_dedups_to_one(self):
        rec_a = _rec(slug="dup1", report_no="Insp GMP DUP",
                     detail_url="https://cms.mhra.gov.uk/mhra/gmp/dup1")
        rec_b = _rec(slug="dup2", report_no="Insp GMP DUP",
                     detail_url="https://cms.mhra.gov.uk/mhra/gmp/dup2")
        client = _FakeClient(records=[rec_a, rec_b])
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "Insp GMP DUP")


class TestPerRecordResilience(unittest.TestCase):
    def test_one_of_two_detail_failures_skips_only_that_record(self):
        rec_ok = _rec(slug="ok", report_no="Insp GMP OK",
                      detail_url="https://cms.mhra.gov.uk/mhra/gmp/ok")
        rec_bad = _rec(slug="bad", report_no="Insp GMP BAD",
                       detail_url="https://cms.mhra.gov.uk/mhra/gmp/bad")
        client = _FakeClient(records=[rec_ok, rec_bad], fail_slugs={"bad"})
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        # 실패율 1/2 = 50% → _MAX_RECORD_FAILURE_RATIO(0.5) 초과 아님 → 정상 처리.
        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "Insp GMP OK")


class TestHighFailureRatioPromotesError(unittest.TestCase):
    def test_more_than_half_failing_returns_error(self):
        recs = [
            _rec(slug="ok", report_no="Insp GMP OK"),
            _rec(slug="bad1", report_no="Insp GMP BAD1"),
            _rec(slug="bad2", report_no="Insp GMP BAD2"),
        ]
        client = _FakeClient(records=recs, fail_slugs={"bad1", "bad2"})
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        # 실패율 2/3 ≈ 66.7% > 50% → 구조 변경 의심 error 승격, items 빈 리스트.
        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("실패율", err)


class TestListFailure(unittest.TestCase):
    def test_list_error_returns_empty_and_error(self):
        client = _FakeClient(list_error=MHRAGmpError("filter page HTTP 500"))
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        self.assertEqual(items, [])
        self.assertIsNotNone(err)
        self.assertIn("리스트/네트워크", err)


class TestWindowFilter(unittest.TestCase):
    def test_out_of_window_record_dropped(self):
        rec_old = _rec(slug="old", report_no="Insp GMP OLD")
        client = _FakeClient(records=[rec_old],
                             detail_map={"old": {"issue_date": "2010-01-01"}})
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(items, [])          # issue_date 2010 < START(2019) → 제외


class TestOfficialUrlNoArchive(unittest.TestCase):
    def test_official_url_is_durable_detail_page(self):
        rec = _rec(slug="durable", report_no="Insp GMP DUR",
                   detail_url="https://cms.mhra.gov.uk/mhra/gmp/durable")
        client = _FakeClient(records=[rec])
        with _patched(client):
            items, err = mod.collect_mhra_gmp_ncr(START, END)

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        item = items[0]
        # EU NCR 과 달리 PDF 아카이브 없음 — 상세 페이지 URL 이 그대로 영속 official.
        self.assertEqual(item.official_url, "https://cms.mhra.gov.uk/mhra/gmp/durable")
        self.assertNotIn("pdf_archived_url", item.raw_payload)


if __name__ == "__main__":
    unittest.main()
