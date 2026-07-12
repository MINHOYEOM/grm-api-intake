#!/usr/bin/env python3
"""[Fix A 2026-07-12] findings evidence_url 품질 계층 회귀·불변식 테스트.

`/findings/` 의 "원본 확인" 링크가 개별 문서가 아니라 목록/데이터셋/API 엔드포인트로
가는 사고가 연속 2건 발생(MFDS 행정처분·회수→data.go.kr 오픈API 데이터셋 안내 페이지 /
MFDS GMP실사→목록 CCBBD03). 이 파일은 방어선 3겹을 검증한다:
  1) grm_findings.evidence_url_quality_error  — 오염 URL 분류기(단일 진실원)
  2) findings_extractors._evidence_url         — 문서급 raw 필드 우선 + 품질 필터
  3) grm_findings.validate_finding             — 최후 거부선
그리고 전 골든 픽스처에 대한 meta-불변식(미래 추출기가 오염 URL 을 내면 즉시 실패).
"""
from __future__ import annotations

import glob
import json
import os
import unittest

import findings_extractors as extractors
import grm_findings as gf

GOLDEN = os.path.join(os.path.dirname(__file__), "golden")


def _base_finding(evidence_url: str) -> dict:
    """validate_finding 통과 최소 레코드(evidence_url 만 변주)."""
    return {
        "schema_version": gf.FINDING_SCHEMA_VERSION,
        "taxonomy_version": "grm-finding-taxonomy/v3",
        "finding_id": "finding-" + "0" * 24,
        "raw_signal_id": "rawsig-" + "0" * 24,
        "source": "MFDS",
        "agency": "MFDS",
        "document_type": "recall-quality",
        "document_id": "recall-abc",
        "published_date": "2026-07-01",
        "firm_name": "테스트제약",
        "category_code": "",
        "finding_text": "테스트 위반 사유",
        "finding_language": "KO",
        "evidence_level": "A",
        "evidence_url": evidence_url,
        "extraction_method": "deterministic",
        "review_status": "accepted",
    }


class EvidenceUrlClassifierTest(unittest.TestCase):
    def test_valid_document_urls_pass(self) -> None:
        for url in (
            "https://www.fda.gov/media/192439/download",
            "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq=2026005294",
            "https://nedrug.mfds.go.kr/pbp/CCBAI01",
            "https://nedrug.mfds.go.kr/cmn/edms/down/1PyJBfLEtwC",
            "https://www.fda.gov/inspections.../warning-letters/acme-660124",
        ):
            self.assertEqual(gf.evidence_url_quality_error(url), "", url)

    def test_service_key_url_rejected(self) -> None:
        url = ("https://apis.data.go.kr/1471000/MdcinRtrvlSleStpgeInfoService04/"
               "getMdcinRtrvlSleStpgelList03?serviceKey=SECRET123&pageNo=1")
        # serviceKey 패턴이 먼저 매칭(키 유출 신호 우선)
        self.assertEqual(gf.evidence_url_quality_error(url), "api-key-url")

    def test_api_endpoint_rejected(self) -> None:
        self.assertEqual(
            gf.evidence_url_quality_error("https://apis.data.go.kr/1471000/svc/list"),
            "api-endpoint")

    def test_dataset_page_rejected(self) -> None:
        self.assertEqual(
            gf.evidence_url_quality_error("https://www.data.go.kr/data/15059114/openapi.do"),
            "dataset-page")
        self.assertEqual(
            gf.evidence_url_quality_error("https://data.go.kr/data/15058457/openapi.do"),
            "dataset-page")

    def test_non_http_rejected(self) -> None:
        self.assertEqual(gf.evidence_url_quality_error("ftp://x/y"), "non-http")
        self.assertEqual(gf.evidence_url_quality_error("nedrug.mfds.go.kr/pbp"), "non-http")

    def test_empty_is_not_a_quality_error(self) -> None:
        # 빈 값은 required 검증의 몫 — 분류기는 '' 반환(중복 에러 방지).
        self.assertEqual(gf.evidence_url_quality_error(""), "")
        self.assertEqual(gf.evidence_url_quality_error(None), "")


class ValidateFindingEvidenceUrlTest(unittest.TestCase):
    def test_valid_url_passes(self) -> None:
        self.assertEqual(
            gf.validate_finding(_base_finding("https://nedrug.mfds.go.kr/pbp/CCBAI01")), [])

    def test_dataset_page_is_rejected(self) -> None:
        errors = gf.validate_finding(
            _base_finding("https://www.data.go.kr/data/15059114/openapi.do"))
        self.assertTrue(any("evidence_url dataset-page" in e for e in errors), errors)

    def test_service_key_is_rejected(self) -> None:
        errors = gf.validate_finding(
            _base_finding("https://apis.data.go.kr/x?serviceKey=SECRET"))
        self.assertTrue(any("evidence_url api-key-url" in e for e in errors), errors)
        # 최후 방어선이 실제로 finding 을 무효화하는지(POST 차단)
        self.assertNotEqual(errors, [])


class EvidenceUrlPriorityTest(unittest.TestCase):
    def test_mfds_gmp_reproduces_live_list_page_bug(self) -> None:
        # [사고2 재현→수리] 라이브 raw 구조: official_url=목록(CCBBD03)·source_url=PDF·
        # raw.download_url=PDF. 옛 _evidence_url 은 official_url(목록)을 최우선 반환해 사고가
        # 났다. 수리 후에는 문서급 raw 필드(download_url)가 최우선이라 PDF 가 나와야 한다.
        pdf = "https://nedrug.mfds.go.kr/cmn/edms/down/1LiveDocId"
        row = {
            "source": "MFDS", "type_or_class": "gmp-inspection",
            "document_id": "gmp-live-0001", "date": "2026-07-01",
            "firm": "라이브제약", "headline": "정기 GMP 실태조사", "language": "KO",
            "official_url": "https://nedrug.mfds.go.kr/pbp/CCBBD03",  # 목록(오염원)
            "source_url": pdf,                                        # 결과 PDF
        }
        raw = {
            "attachment_deficiency_assessment": "present",
            "attachment_deficiency_excerpt": "무균공정 배지충전 검증 자료 미흡.",
            "download_url": pdf,
            "manufacturer": "라이브제약",
        }
        raw_signal = gf.raw_signal_from_row(row, raw)
        findings = extractors.findings_from_raw_signal(raw_signal)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["evidence_url"], pdf)
        self.assertNotIn("CCBBD03", findings[0]["evidence_url"])

    def test_contaminated_fallback_not_silently_promoted(self) -> None:
        # 추출기가 문서급 URL 을 못 찾고, raw_signal.official_url=data.go.kr 데이터셋·
        # source_url=serviceKey API 뿐이면 → evidence_url='' → validate_finding required
        # 에러(침묵 승격 대신 표면화). finding_from_raw_signal 의 _choose_evidence_url 검증.
        record = gf.finding_from_raw_signal(
            {
                "schema_version": gf.RAW_SIGNAL_SCHEMA_VERSION,
                "source": "MFDS", "document_id": "x", "published_date": "2026-07-01",
                "firm_name": "제약", "raw_json": "{}", "row_json": "{}",
                "official_url": "https://www.data.go.kr/data/15059114/openapi.do",
                "source_url": "https://apis.data.go.kr/x?serviceKey=SECRET",
            },
            finding_text="사유", evidence_url="", ordinal=1, finding_language="KO",
        )
        self.assertEqual(record["evidence_url"], "")
        self.assertIn("findings.evidence_url required", gf.validate_finding(record))


class GoldenEvidenceUrlInvariantTest(unittest.TestCase):
    def test_all_golden_fixtures_yield_document_grade_evidence_url(self) -> None:
        # meta-불변식(재발 방지 핵심): 어떤 추출기를 추가하든, 전 골든 픽스처 산출 finding 의
        # evidence_url 은 오염 클래스(목록 데이터셋/API/serviceKey/비http)면 안 된다.
        checked = 0
        for path in sorted(glob.glob(os.path.join(GOLDEN, "*.input.json"))):
            with open(path, encoding="utf-8") as f:
                fx = json.load(f)
            if "row" not in fx or "raw" not in fx:
                continue
            raw_signal = gf.raw_signal_from_row(fx["row"], fx["raw"])
            for finding in extractors.findings_from_raw_signal(raw_signal):
                url = finding["evidence_url"]
                reason = gf.evidence_url_quality_error(url)
                self.assertEqual(
                    reason, "",
                    f"{os.path.basename(path)}: 오염 evidence_url({reason}): {url}")
                checked += 1
        self.assertGreater(checked, 0)  # 최소 몇 개 픽스처는 finding 을 내야 유효한 검사


if __name__ == "__main__":
    unittest.main()
