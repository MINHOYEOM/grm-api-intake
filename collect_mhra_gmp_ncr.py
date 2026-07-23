#!/usr/bin/env python3
"""GRM MHRA GMDP Statement of Non-Compliance (SoNC) Collector.

ENABLE_MHRA_GMP_NCR=true 또는 --sources mhra_gmp_ncr 일 때 collect_intake.main()
에서 호출된다. 영국 MHRA 가 GMDP 등록부에 공개하는 **업체별 GMP 비준수 성명서
(Statement of Non-Compliance)** 를 FDA Warning Letter·483·EudraGMDP NCR 과 동일하게
GMP News 카드 + Findings DB 로 편입한다. EudraGMDP(EU) 수집기의 영국판 쌍둥이.

채널 (재현공학 검증 2026-07-23, `mhra_gmdp_client.py`):
  MHRA GMP 등록부 비준수 필터(Drupal Facets · **세션 불요** · URL 파라미터만):
    https://cms.mhra.gov.uk/mhra/gmp?f[0]=gmp_compliance:Non Compliant
  - 리스트: 문서번호(report_no)·slug·사이트 국가·실사일 (서버렌더 표)
  - 상세(GET /mhra/gmp/<slug>): 제조소·주소·발행기관·규제근거·제품유형·제조운영·
    제한사항·비준수 내용(Nature)·당국 조치(Withdrawal/MA action/Recall/Prohibition)·
    발행일(서명일) — **전부 서버렌더 HTML, 세션 독립**

EudraGMDP 대비 단순화(핵심):
  - **Supabase Storage PDF 아카이브 없음**: EudraGMDP 는 drilldown/PDF 가 세션상태
    의존이라 URL 저장 불가 → PDF 를 받아 공개버킷에 아카이브해야 했다. MHRA 상세
    페이지는 평범한 GET 으로 성명서 원문을 렌더하므로 **그 detail URL 자체가 영속
    official_url**. 아카이브 계층·자격증명 분기 전부 제거.
  - **세션 로직 없음**: Struts POST/cookiejar/페이지-활성 불변식 불필요.

설계:
  - **document_id = report_no**(문서번호·고유 표기). 파싱 실패 시 slug 폴백(dedup 무결성).
  - published_date = issue_date(성명서 서명일). 부재 시 실사일 폴백. firm = manufacturer.
    지연공개형 성긴 소스(7년 6건)라 main 이 enforcement 윈도우(enf_start)를 전달.
  - **실패는 침묵 0 금지**: 리스트/네트워크 실패 → error 반환. 개별 레코드 상세 실패는
    건너뛰고 계속(실패율 > 50% 면 구조 변경 의심 error 승격).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from grm_common import log
from collect_intake import (
    IntakeItem,
    SOURCE_MHRA_GMP_NCR,
    SRC_TYPE_OFFICIAL_PAGE,
    _within_window,
)
from mhra_gmdp_client import (
    MHRAGmpNCRClient,
    MHRAGmpError,
    MHRARecord,
    SEARCH_URL,
)

LANGUAGE_EN = "EN"
REGION_UK = "UK (MHRA)"
TYPE_MHRA_GMP_NCR = "gmp-non-compliance"

# 개별 레코드 상세 실패가 이 비율을 넘으면 상세 레이아웃 변경 의심 → error 승격.
_MAX_RECORD_FAILURE_RATIO = 0.5


def _signal_tier(rec: MHRARecord) -> str:
    """조치 문언 기반 신호 등급. 회수/정지/공급금지/철회 = 고신호(EU NCR 과 동일 규칙)."""
    action = (rec.action or "").lower()
    if any(t in action for t in ("recall", "suspension", "suspend", "prohibition",
                                 "withdrawal", "withdrawn", "revocation", "voiding")):
        return "Tier 3"
    return "Tier 2"


def _to_item(rec: MHRARecord) -> IntakeItem:
    firm = rec.manufacturer or rec.report_no
    country = rec.site_country or rec.country or ""
    document_id = rec.report_no or rec.slug
    date_iso = rec.issue_date or rec.inspection_date or ""
    headline = f"{firm} — GMP Non-Compliance" + (f" ({country})" if country else "")

    # compute_modality 가 제품군을 인식하도록 body 에 텍스트 주입(제품유형·운영·위반내용).
    body_parts = [
        f"발행기관: {rec.authority}" if rec.authority else "",
        f"제품유형: {rec.product_type}" if rec.product_type else "",
        f"비준수 운영: {rec.operations}" if rec.operations else "",
        f"제한사항: {rec.restriction}" if rec.restriction else "",
        f"위반내용(Nature): {rec.nature}" if rec.nature else "",
        f"조치(Action): {rec.action}" if rec.action else "",
    ]

    raw_payload: dict[str, Any] = {
        "api": "MHRA GMDP Non-Compliance",
        "report_no": rec.report_no,
        "slug": rec.slug,
        "site_name": rec.manufacturer or "",
        "site_address": rec.site_address or "",
        "country": country,                          # 사이트 소재국
        "authority_country": rec.authority or "",    # 발행기관(항상 MHRA/UK)
        "regulatory_basis": rec.regulatory_basis or "",
        "product_scope": rec.product_type or "",     # 제품유형 = EU NCR 의 product_scope 대응
        "inspection_end_date": rec.inspection_date or "",
        "issue_date": rec.issue_date or "",
        "ncr_operations": rec.operations or "",
        "ncr_nature": rec.nature or "",              # findings 추출기 게이트 키
        "ncr_action": rec.action or "",
        "ncr_additional": rec.restriction or "",     # 제한사항 = EU NCR 의 additional 대응
        "mhra_search_url": SEARCH_URL,
        "mhra_detail_url": rec.detail_url,           # 영속 공식 원문 URL
        # compute_modality 용 정규화 필드.
        "product_type": rec.product_type or "",
        "product_description": rec.operations or "",
    }

    return IntakeItem(
        source=SOURCE_MHRA_GMP_NCR,
        document_id=document_id,
        date_iso=date_iso,
        headline=headline[:240],
        official_url=rec.detail_url,                  # 세션 독립 상세 페이지 = 영속 원문
        type_or_class=TYPE_MHRA_GMP_NCR,
        firm=firm,
        body="\n".join(p for p in body_parts if p),
        api_query=SEARCH_URL,                         # info 링크(MHRA 비준수 검색)
        source_url=SEARCH_URL,
        qa_relevance="Likely",                        # GMP 비준수는 전부 관련
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=_signal_tier(rec),
        raw_payload=raw_payload,
        language=LANGUAGE_EN,
        region_jurisdiction=REGION_UK,
        site_country=country,
        evidence_candidate="A",                       # 위반내용 원문 인용 가능 → Evidence A
    )


def collect_mhra_gmp_ncr(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """MHRA GMP 비준수(SoNC) 수집. (items, error_msg).

    - 리스트/네트워크 자체 실패 → error(0건 침묵 금지).
    - 윈도우 내 0건 → 정상(빈 리스트, error 없음 — 성긴 소스).
    - 개별 레코드 상세 실패 → 건너뜀. 실패율 > 50% → 구조 변경 의심 error.
    """
    from_date, to_date = start.isoformat(), end.isoformat()
    log("INFO", f"MHRA GMP NCR 수집: {SEARCH_URL} [{from_date}~{to_date}]")

    client = MHRAGmpNCRClient()
    items: list[IntakeItem] = []
    seen: set[str] = set()
    seen_count = 0
    failures: list[str] = []

    try:
        records = client.list_noncompliant()
    except MHRAGmpError as e:
        return [], f"MHRA GMP NCR 수집 실패(리스트/네트워크): {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"MHRA GMP NCR 수집 중 예외: {e!r}"

    for rec in records:
        key = rec.report_no or rec.slug
        if key in seen:
            continue
        seen.add(key)
        seen_count += 1
        try:
            client.fetch_detail(rec)
        except MHRAGmpError as e:
            failures.append(f"{rec.report_no or rec.slug}: {e}")
            continue
        except Exception as e:  # noqa: BLE001
            failures.append(f"{rec.report_no or rec.slug}: {e!r}")
            continue
        date_iso = rec.issue_date or rec.inspection_date or ""
        if not _within_window(date_iso, start, end):
            continue
        items.append(_to_item(rec))

    # 개별 레코드 대량 실패 = 상세 레이아웃 변경 의심 → error 승격(침묵 금지).
    if seen_count and len(failures) / seen_count > _MAX_RECORD_FAILURE_RATIO:
        return [], (f"MHRA GMP NCR 상세 파싱 실패율 과다({len(failures)}/{seen_count}) "
                    f"— 상세 레이아웃 변경 의심(수동 확인 필요). 예: {failures[0] if failures else ''}")

    if failures:
        log("WARN", f"MHRA GMP NCR 개별 실패 {len(failures)}/{seen_count}건 건너뜀: "
                    + "; ".join(failures[:3]))
    log("INFO", f"MHRA GMP NCR 수집 완료: {len(items)}건 (스캔 {seen_count}건)")
    return items, None
