#!/usr/bin/env python3
"""GRM EU GMP Non-Compliance Report (EudraGMDP) Collector.

ENABLE_EU_GMP_NCR=true 또는 --sources eu_gmp_ncr 일 때 collect_intake.main() 에서
호출된다. EU/EEA 각국 규제당국(NCA)이 EudraGMDP 에 공개하는 **업체별 GMP 비준수
보고서(NCR)** 를 FDA Warning Letter·483 과 동일하게 GMP News 카드 + Findings DB 로
편입한다.

채널 (재현공학 검증 2026-07-22, `eudragmdp_client.py`):
  EudraGMDP GMP Non-Compliance 검색(Struts `.do`, 100% requests·서버렌더):
    https://eudragmdp.ema.europa.eu/inspections/gmpc/searchGMPNonCompliance.do
  - 리스트: report_no·doc_ref·업체·사이트·국가·실사종료일·발행일 (서버렌더 표)
  - 상세(Drilldown): 발행기관(NCA)·제품범위·비준수 운영항목·위반내용(Nature)·조치(Action)
  - PDF(generateGMPCPDF.do): 공식 Statement of Non-Compliance 원문

설계:
  - **document_id = doc_ref** (EudraGMDP 문서참조번호·고유). report_no 는 1 NCR 이
    다중 사이트로 여러 행에 반복될 수 있어(실측 3-way) dedup 키로 부적합.
  - published_date = issue_date(발행일). firm = site_name(업체). 지연공개형이라 main 이
    enforcement 윈도우(enf_start)를 전달(성긴 소스 — 4년 61건).
  - **출처 durability**: drilldown/PDF 는 세션상태 의존이라 URL 저장 불가 → 수집 시점에
    PDF 를 받아 Supabase Storage 공개버킷에 아카이브하고 그 공개 URL 을 official_url 로
    싣는다. 아카이브 실패 시 EudraGMDP 검색 페이지로 정직하게 폴백(card_scaffold
    `_official_eu_gmp_ncr` 가 처리).
  - **실패는 침묵 0 금지**: 세션/검색 자체 실패 → error 반환. 단, 개별 레코드의
    상세/PDF 실패는 건너뛰고 계속(대량 실패면 error 승격).

Supabase Storage:
  버킷 `eudragmdp-ncr`(public). 업로드는 SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
  (intake 워크플로가 이미 보유)로 REST PUT. 자격증명 부재(로컬)면 아카이브 생략 +
  폴백 링크(수집·카드·findings 자체는 정상).
"""

from __future__ import annotations

import os
import urllib.request
import urllib.error
from datetime import date
from typing import Any

from grm_common import log
from collect_intake import (
    IntakeItem,
    SOURCE_EU_GMP_NCR,
    SRC_TYPE_OFFICIAL_PAGE,
    _within_window,
)
from eudragmdp_client import (
    EudraGMDPClient,
    EudraGMDPError,
    NCRRecord,
    SEARCH_URL,
)

LANGUAGE_EN = "EN"
REGION_EU = "EU/EEA (EudraGMDP)"
TYPE_EU_GMP_NCR = "gmp-non-compliance"

# Supabase Storage — 아카이브 대상 공개 버킷.
STORAGE_BUCKET = "eudragmdp-ncr"
STORAGE_OBJECT_PREFIX = "ncr"          # 오브젝트 경로: ncr/<doc_ref>.pdf
STORAGE_UPLOAD_TIMEOUT = 60

# 개별 레코드 상세/PDF 실패가 이 비율을 넘으면 구조 변경 의심 → error 승격.
_MAX_RECORD_FAILURE_RATIO = 0.5


def _storage_public_url(base_url: str, object_path: str) -> str:
    return f"{base_url.rstrip('/')}/storage/v1/object/public/{STORAGE_BUCKET}/{object_path}"


def _archive_pdf(pdf_bytes: bytes, doc_ref: str) -> tuple[str, str | None]:
    """PDF 를 Supabase Storage 에 upsert 업로드. (public_url, error).

    자격증명 부재 → ("", None)(로컬 폴백·정상). 업로드 실패 → ("", err).
    """
    base_url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base_url or not service_key:
        return "", None
    object_path = f"{STORAGE_OBJECT_PREFIX}/{doc_ref}.pdf"
    upload_url = f"{base_url.rstrip('/')}/storage/v1/object/{STORAGE_BUCKET}/{object_path}"
    req = urllib.request.Request(
        upload_url, data=pdf_bytes, method="POST",
        headers={
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/pdf",
            "x-upsert": "true",             # 멱등 재업로드(경로 동일 → 덮어씀)
            "Cache-Control": "public, max-age=31536000, immutable",
        })
    try:
        with urllib.request.urlopen(req, timeout=STORAGE_UPLOAD_TIMEOUT) as resp:
            if resp.getcode() not in (200, 201):
                return "", f"storage upload HTTP {resp.getcode()} for {object_path}"
    except urllib.error.HTTPError as e:
        return "", f"storage upload HTTPError {e.code} for {object_path}: {e.read()[:200]!r}"
    except Exception as e:  # noqa: BLE001
        return "", f"storage upload failed for {object_path}: {e!r}"
    return _storage_public_url(base_url, object_path), None


def _signal_tier(rec: NCRRecord) -> str:
    """조치 문언 기반 신호 등급. 회수/정지/공급금지 = 고신호."""
    action = (rec.action or "").lower()
    if any(t in action for t in ("recall", "suspension", "suspend", "prohibition",
                                 "withdrawal", "revocation", "voiding")):
        return "Tier 3"
    return "Tier 2"


def _to_item(rec: NCRRecord, pdf_url: str, pdf_error: str | None) -> IntakeItem:
    firm = rec.site_name
    country = rec.country
    authority = rec.authority_country or ""
    headline = f"{firm} — GMP Non-Compliance" + (f" ({country})" if country else "")

    # compute_modality 가 제품군을 인식하도록 body 에 텍스트 주입(제품범위·위반내용).
    body_parts = [
        f"발행기관(NCA): {authority}" if authority else "",
        f"제품범위: {rec.product_scope}" if rec.product_scope else "",
        f"비준수 운영: {rec.operations}" if rec.operations else "",
        f"위반내용(Nature): {rec.nature}" if rec.nature else "",
        f"조치(Action): {rec.action}" if rec.action else "",
    ]

    raw_payload: dict[str, Any] = {
        "api": "EudraGMDP GMP Non-Compliance",
        "report_no": rec.report_no,
        "doc_ref": rec.doc_ref,
        "mia_number": rec.mia_number,
        "site_name": rec.site_name,
        "site_address": rec.site_address,
        "oms_location": rec.oms_location,
        "city": rec.city,
        "postcode": rec.postcode,
        "country": country,                      # 사이트 소재국
        "authority_country": authority,          # 발행 NCA 국가
        "product_scope": rec.product_scope or "",
        "inspection_end_date": rec.inspection_end_date,
        "issue_date": rec.issue_date,
        "ncr_operations": rec.operations or "",
        "ncr_nature": rec.nature or "",          # findings 추출기 게이트 키
        "ncr_action": rec.action or "",
        "ncr_additional": rec.additional or "",
        "eudragmdp_search_url": SEARCH_URL,
        "pdf_archived_url": pdf_url,             # 공개 PDF(있으면 official_url)
        "pdf_archived": bool(pdf_url),
        # compute_modality 용 정규화 필드.
        "product_type": rec.product_scope or "",
        "product_description": rec.operations or "",
    }
    if pdf_error:
        raw_payload["pdf_archive_error"] = pdf_error

    return IntakeItem(
        source=SOURCE_EU_GMP_NCR,
        document_id=rec.doc_ref,
        date_iso=rec.issue_date,
        headline=headline[:240],
        official_url=pdf_url,                     # 아카이브 PDF(폴백은 card_scaffold 가 처리)
        type_or_class=TYPE_EU_GMP_NCR,
        firm=firm,
        body="\n".join(p for p in body_parts if p),
        api_query=SEARCH_URL,                     # info 링크(EudraGMDP 검색)
        source_url=SEARCH_URL,
        qa_relevance="Likely",                    # GMP 비준수는 전부 관련
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=_signal_tier(rec),
        raw_payload=raw_payload,
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_EU,
        site_country=country,
        evidence_candidate="A",                   # 위반내용 원문 인용 가능 → Evidence A
    )


def collect_eu_gmp_ncr(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """EudraGMDP GMP 비준수(NCR) 수집. (items, error_msg).

    - 세션/검색 자체 실패 → error(0건 침묵 금지).
    - 윈도우 내 0건 → 정상(빈 리스트, error 없음 — 성긴 소스).
    - 개별 레코드 상세/PDF 실패 → 건너뜀. 실패율 > 50% → 구조 변경 의심 error.
    """
    from_date, to_date = start.isoformat(), end.isoformat()
    log("INFO", f"EU GMP NCR 수집: {SEARCH_URL} [{from_date}~{to_date}]")

    client = EudraGMDPClient()
    items: list[IntakeItem] = []
    seen: set[str] = set()
    seen_count = 0
    failures: list[str] = []
    archive_failures = 0

    try:
        pages = client.iter_pages(from_date, to_date)
        for page_idx, rows in pages:
            for rec in rows:
                if rec.doc_ref in seen:
                    continue
                seen.add(rec.doc_ref)
                seen_count += 1
                rec.page_index = page_idx
                try:
                    client.fetch_detail(rec)      # page_idx 활성 상태에서만 해석됨
                    client.fetch_pdf(rec)
                except EudraGMDPError as e:
                    failures.append(f"{rec.report_no}(ref={rec.doc_ref}): {e}")
                    continue
                pdf_url, pdf_err = _archive_pdf(rec.pdf_bytes or b"", rec.doc_ref)
                if pdf_err:
                    archive_failures += 1
                    log("WARN", f"EU GMP NCR PDF 아카이브 실패 {rec.report_no}: {pdf_err}")
                if not _within_window(rec.issue_date, start, end):
                    # 서버 필터가 발행일 기준이라 통상 창 안이지만, 방어적으로 재확인.
                    continue
                items.append(_to_item(rec, pdf_url, pdf_err))
    except EudraGMDPError as e:
        return [], f"EU GMP NCR 수집 실패(세션/검색): {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"EU GMP NCR 수집 중 예외: {e!r}"

    # 개별 레코드 대량 실패 = 상세 레이아웃 변경 의심 → error 승격(침묵 금지).
    if seen_count and len(failures) / seen_count > _MAX_RECORD_FAILURE_RATIO:
        return [], (f"EU GMP NCR 상세 파싱 실패율 과다({len(failures)}/{seen_count}) "
                    f"— 상세 레이아웃 변경 의심(수동 확인 필요). 예: {failures[0] if failures else ''}")

    if failures:
        log("WARN", f"EU GMP NCR 개별 실패 {len(failures)}/{seen_count}건 건너뜀: "
                    + "; ".join(failures[:3]))
    if archive_failures:
        log("WARN", f"EU GMP NCR PDF 아카이브 실패 {archive_failures}건 — 해당 카드는 "
                    "EudraGMDP 검색 링크로 폴백(수집·findings 는 정상)")
    log("INFO", f"EU GMP NCR 수집 완료: {len(items)}건 (스캔 {seen_count}건)")
    return items, None
