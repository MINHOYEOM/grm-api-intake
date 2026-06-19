"""MFDS API recovery collectors — KR-egress-free official API mappings."""

from __future__ import annotations

import os
import sys
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import card_scaffold as cs
import collect_mfds_gmp_cert as gc
import collect_mfds_law as law
import collect_mfds_safety_letter as sl


def _datago_page(item: dict, *, total: int = 1, page: int = 1, rows: int = 100) -> dict:
    return {
        "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE"},
        "body": {
            "pageNo": page,
            "numOfRows": rows,
            "totalCount": total,
            "items": {"item": item},
        },
    }


class MfdsLawCollectorTest(unittest.TestCase):
    def test_key_required(self) -> None:
        items, err = law.collect_mfds_law(date(2026, 6, 1), date(2026, 6, 30), "")
        self.assertEqual(items, [])
        self.assertIn("DATA_GO_KR_SERVICE_KEY", err or "")

    def test_admrul_xml_maps_to_notice_final(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_http_get_xml(url, timeout=None, retries=None, headers=None):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            calls.append((qs.get("target", [""])[0], qs.get("query", [""])[0]))
            if qs.get("target", [""])[0] != "admrul":
                return ET.fromstring("<LawSearch><resultCode>00</resultCode><totalCnt>0</totalCnt></LawSearch>")
            return ET.fromstring(
                """
                <LawSearch>
                  <resultCode>00</resultCode>
                  <resultMsg>success</resultMsg>
                  <totalCnt>1</totalCnt>
                  <law id="1">
                    <행정규칙일련번호>2000000123456</행정규칙일련번호>
                    <행정규칙명>의약품 제조 및 품질관리에 관한 규정</행정규칙명>
                    <행정규칙종류>고시</행정규칙종류>
                    <발령일자>20260617</발령일자>
                    <시행일자>20260618</시행일자>
                    <제개정구분명>일부개정</제개정구분명>
                    <소관부처코드>1471000</소관부처코드>
                    <소관부처명>식품의약품안전처</소관부처명>
                    <행정규칙상세링크>/DRF/lawService.do?target=admrul&amp;ID=2000000123456</행정규칙상세링크>
                  </law>
                </LawSearch>
                """
            )

        orig = law.http_get_xml
        law.http_get_xml = fake_http_get_xml
        try:
            items, err = law.collect_mfds_law(
                date(2026, 6, 1), date(2026, 6, 30), "dummy")
        finally:
            law.http_get_xml = orig

        self.assertIsNone(err)
        self.assertTrue(any(target == "admrul" for target, _query in calls))
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.document_id, "admrul-2000000123456")
        self.assertEqual(item.type_or_class, "notice-final")
        self.assertEqual(item.date_iso, "2026-06-17")
        self.assertEqual(item.source_type, "Official API")
        self.assertIn("식품의약품안전처", item.body)
        self.assertIn("serviceKey=***REDACTED***", item.api_query)

    def test_admrul_body_enrich_uses_law_go_kr_oc(self) -> None:
        body_calls: list[str] = []

        def fake_http_get_xml(url, timeout=None, retries=None, headers=None):
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            if parsed.netloc == "www.law.go.kr":
                body_calls.append(url)
                self.assertEqual(qs.get("OC"), ["oc-secret"])
                self.assertEqual(qs.get("target"), ["admrul"])
                self.assertEqual(qs.get("ID"), ["2000000999999"])
                return ET.fromstring(
                    """
                    <AdmrulService>
                      <조문단위>
                        <조문제목>목적</조문제목>
                        <조문내용>의약품 제조 및 품질관리에 관한 세부 기준을 정한다.</조문내용>
                      </조문단위>
                    </AdmrulService>
                    """
                )
            if qs.get("target", [""])[0] == "admrul" and qs.get("query", [""])[0] == "식품의약품안전처":
                return ET.fromstring(
                    """
                    <LawSearch>
                      <resultCode>00</resultCode>
                      <totalCnt>1</totalCnt>
                      <law>
                        <행정규칙일련번호>2000000999999</행정규칙일련번호>
                        <행정규칙명>의약품 제조 및 품질관리에 관한 규정</행정규칙명>
                        <행정규칙종류>고시</행정규칙종류>
                        <발령일자>20260617</발령일자>
                        <소관부처코드>1471000</소관부처코드>
                        <소관부처명>식품의약품안전처</소관부처명>
                      </law>
                    </LawSearch>
                    """
                )
            return ET.fromstring("<LawSearch><resultCode>00</resultCode><totalCnt>0</totalCnt></LawSearch>")

        orig = law.http_get_xml
        law.http_get_xml = fake_http_get_xml
        try:
            items, err = law.collect_mfds_law(
                date(2026, 6, 1), date(2026, 6, 30), "dummy", law_go_kr_oc="oc-secret")
        finally:
            law.http_get_xml = orig

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(body_calls), 1)
        self.assertIn("본문 발췌", items[0].body)
        self.assertIn("세부 기준", items[0].body)
        self.assertIn("law_go_kr_body_excerpt", items[0].raw_payload)
        self.assertIn("OC=***REDACTED***", items[0].raw_payload["law_go_kr_body_query"])


class MfdsSafetyLetterCollectorTest(unittest.TestCase):
    def test_key_required(self) -> None:
        items, err = sl.collect_mfds_safety_letters(
            date(2026, 6, 1), date(2026, 6, 30), "")
        self.assertEqual(items, [])
        self.assertIn("DATA_GO_KR_SERVICE_KEY", err or "")

    def test_safety_letter_maps_from_datago_json(self) -> None:
        raw = {
            "SAFT_LETT_NO": "77",
            "TITLE": "OO성분 의약품 안전성서한",
            "PBANC_NO": "안전-2026-1",
            "PBANC_DIVS_NM": "안전성 정보",
            "PBANC_YMD": "2026-06-12",
            "SUMRY_CONT": "사용상 주의 필요",
            "PBANC_CONT": "상세 안전성 정보",
            "ACTN_MTTR_CONT": "허가사항 변경 예정",
            "CHRG_DEP": "의약품안전평가과",
            "ATTACH_FILE_URL": "https://example.mfds.go.kr/safety.pdf",
        }

        def fake_http_get_json(endpoint, params=None, timeout=None, retries=None):
            return _datago_page(raw, page=params["pageNo"], rows=sl.PAGE_SIZE)

        orig = sl.http_get_json
        sl.http_get_json = fake_http_get_json
        try:
            items, err = sl.collect_mfds_safety_letters(
                date(2026, 6, 1), date(2026, 6, 30), "dummy")
        finally:
            sl.http_get_json = orig

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].document_id, "safety-77")
        self.assertEqual(items[0].type_or_class, "safety-letter")
        self.assertEqual(items[0].signal_tier, "Tier 3")
        self.assertEqual(items[0].official_url, raw["ATTACH_FILE_URL"])
        self.assertIn("허가사항 변경 예정", items[0].body)


class MfdsGmpCertCollectorTest(unittest.TestCase):
    def test_key_required(self) -> None:
        items, err = gc.collect_mfds_gmp_certs(
            date(2026, 6, 1), date(2026, 6, 30), "")
        self.assertEqual(items, [])
        self.assertIn("DATA_GO_KR_SERVICE_KEY", err or "")

    def test_gmp_certificate_maps_status_table_row(self) -> None:
        raw = {
            "BSSH_NM": "명인제약(주)",
            "FCTR_ADDR": "경기도 화성시 팔탄면 노하길 361-12",
            "KGMP_BGMP_NAME": "완제의약품",
            "GMP_INGR_MM_GROUP_NAME": "내용고형제(정제, 캡슐제)",
            "VLD_PRD_YMD": "2026-12-31",
        }

        def fake_http_get_json(endpoint, params=None, timeout=None, retries=None):
            return _datago_page(raw, page=params["pageNo"], rows=gc.PAGE_SIZE)

        orig = gc.http_get_json
        gc.http_get_json = fake_http_get_json
        try:
            items, err = gc.collect_mfds_gmp_certs(
                date(2026, 6, 1), date(2026, 6, 30), "dummy")
        finally:
            gc.http_get_json = orig

        self.assertIsNone(err)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].document_id.startswith("gmpcert-"))
        self.assertEqual(items[0].type_or_class, "gmp-certificate")
        self.assertEqual(items[0].signal_tier, "Tier 1")
        self.assertEqual(items[0].date_iso, "2026-12-31")
        self.assertIn("내용고형제", items[0].body)

    def test_card_scaffold_resolves_gmp_certificate_kind(self) -> None:
        row = {
            "source": "MFDS",
            "type_or_class": "gmp-certificate",
            "document_id": "gmpcert-abc",
            "date": "2026-12-31",
            "headline": "[GMP적합판정] 명인제약",
            "signal_tier": "Tier 1",
        }
        self.assertEqual(cs.resolve_kind(row), "gmp-certificate")
        card = cs.build_card_scaffold(row, {"BSSH_NM": "명인제약(주)"})
        self.assertIn("GMP적합판정", card.markdown)


if __name__ == "__main__":
    unittest.main()
