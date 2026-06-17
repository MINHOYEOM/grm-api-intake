#!/usr/bin/env python3
"""GRM MFDS Administrative Action Collector - Phase 2c.

Collects data.go.kr service 15058457 (MFDS medicinal administrative
actions) as enforcement-proxy intake rows for manufacturing/quality signals.
"""

from __future__ import annotations

import os
import re
import hashlib
import urllib.parse
from datetime import date
from typing import Any

from grm_common import (
    http_get_json,
    log,
    parse_datago_date,
    parse_int_safe,
    text_field,
    datago_normalize_items,
    datago_extract_items,
)
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_API,
    _within_window,
)


ADMIN_API_ENDPOINT = (
    "https://apis.data.go.kr/1471000/MdcinExaathrService04"
    "/getMdcinExaathrList04"
)
DATASET_URL = "https://www.data.go.kr/data/15058457/openapi.do"
# 행정처분 건별 상세(L1 후보) — scaffold 가 raw.ADM_DISPS_SEQ 로 조립하는 것과 동형.
NEDRUG_ADMIN_DETAIL_TPL = (
    "https://nedrug.mfds.go.kr/pbp/CCBAO01/getItem?dispsApplySeq={seq}"
)

TYPE_ADMIN_ACTION = "admin-action"
LANGUAGE_KO = "KO"
REGION_MFDS = "Korea (MFDS)"

PAGE_SIZE = 100
MAX_PAGES = 20

ADMIN_TIER3_TERMS = [
    "gmp",
    "우수의약품제조관리기준",
    "제조관리",
    "품질관리",
    "품질부적합",
    "품질검사",
    "제조업무정지",
    "제조정지",
    "기준서",
    "회수절차",
    "제조기록서",
    "거짓작성",
    "변경 미허가",
    "시험",
    "함량",
    "용출",
    "무균",
    "미생물",
    "불순물",
    "니트로사민",
    "자료",
    "데이터",
    "실태조사",
    "회수",
    "거짓",
    "부정",
]

PHARMA_RESCUE_TERMS = [
    "의약품",
    "마약류",
    "원료의약품",
    "생물학적제제",
    "제제",
    "정제",
    "캡슐",
    "주사",
    "밀리그램",
    "시럽",
]

DRUG_PRODUCT_RESCUE_TERMS = [
    "마약류",
    "원료의약품",
    "생물학적제제",
    "정제",
    "캡슐",
    "주사",
    "밀리그램",
    "시럽",
]

LOW_VALUE_ADMIN_TERMS = [
    "화장품",
    "광고업무정지",
    "광고 업무정지",
    "보건용마스크",
    "황사방역마스크",
    "방역마스크",
    "의료기기",
    "체외진단",
]


def _url_verify_enabled() -> bool:
    """E2 — `ENABLE_MFDS_URL_VERIFY`(기본 off). on 일 때만 후보 L1 을 collect 시점에
    live verify 해 official_url L1 을 승격/강등한다. off 면 수집기 동작·골든 전부 불변."""
    return os.environ.get("ENABLE_MFDS_URL_VERIFY", "").strip().lower() in (
        "1", "true", "yes", "on")


def _verify_admin_l1(seq: str, firm: str) -> tuple[str, str]:
    """후보 L1(`CCBAO01/getItem?dispsApplySeq={seq}`)을 verify_url_live 로 판정.

    반환 (verdict, candidate_url). verdict ∈ {"pass","fail"} — pass 면 scaffold 가
    검증된 L1 으로 단언(⚠️ 없음), fail 이면 L2 인덱스 + ⚠️ fallback 으로 강등한다.
    nedrug getItem 은 무효 seq 도 HTTP 200(오류 셸·~2.6KB)이라 길이·오류마커로 판정
    (verify_url_live 가 200∧오류셸 아님∧길이≥min∧기대어 포함 을 ok 로 본다).
    """
    candidate = NEDRUG_ADMIN_DETAIL_TPL.format(
        seq=urllib.parse.quote(seq, safe=""))
    try:
        from brief_lint import verify_url_live  # lazy — flag off 면 import 도 안 함
        res = verify_url_live(candidate, expect_terms=["행정처분"])
        verdict = "pass" if res.get("ok") else "fail"
    except Exception:  # noqa: BLE001 — verify 불가는 미검증=강등(차단 측 안전)
        verdict = "fail"
    return verdict, candidate


def _mask_service_key(url: str) -> str:
    return re.sub(r"([?&]serviceKey=)[^&]+", r"\1***REDACTED***", url)


def _request_url(params: dict[str, Any]) -> str:
    return ADMIN_API_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _api_query(params: dict[str, Any]) -> str:
    return _mask_service_key(_request_url(params))


_parse_int = parse_int_safe
_text = text_field
_parse_api_date = parse_datago_date


def _item_date(raw: dict[str, Any]) -> str:
    return _parse_api_date(_text(raw, "LAST_SETTLE_DATE"))


def _extract_items(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int, int, str]:
    return datago_extract_items(data, default_page_size=PAGE_SIZE)


def _document_id(raw: dict[str, Any]) -> str:
    seq = _text(raw, "ADM_DISPS_SEQ")
    if seq:
        return f"admin-{seq}"
    fallback = "|".join(
        [
            _text(raw, "ENTP_NAME"),
            _text(raw, "ITEM_NAME"),
            _text(raw, "LAST_SETTLE_DATE"),
            _text(raw, "ADM_DISPS_NAME"),
        ]
    )
    digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:12]
    return f"admin-{digest}"


def _tier_text(raw: dict[str, Any]) -> str:
    return "\n".join(
        [
            _text(raw, "ITEM_NAME"),
            _text(raw, "EXPOSE_CONT"),
            _text(raw, "ADM_DISPS_NAME"),
            _text(raw, "BEF_APPLY_LAW"),
        ]
    ).lower()


def _pharma_text(raw: dict[str, Any]) -> str:
    # rescue 검사용 haystack. 바 '정제'(tablet) 가 '정제수'(purified water·비의약품)를
    # 부분매칭하지 않도록 '정제수'를 선제거한다(compute_modality 의 haystack.replace
    # ("정제수","") 와 동형 — 다른 rescue term(주사·캡슐 등)에는 영향 없음).
    return "\n".join(
        [
            _text(raw, "ITEM_NAME"),
            _text(raw, "EXPOSE_CONT"),
            _text(raw, "ADM_DISPS_NAME"),
        ]
    ).lower().replace("정제수", "")


def _has_actionable_signal(raw: dict[str, Any]) -> bool:
    text = _tier_text(raw)
    return any(term.lower() in text for term in ADMIN_TIER3_TERMS)


def _has_pharma_signal(raw: dict[str, Any]) -> bool:
    text = _pharma_text(raw)
    return any(term.lower() in text for term in PHARMA_RESCUE_TERMS)


def _has_drug_product_signal(raw: dict[str, Any]) -> bool:
    text = _pharma_text(raw)
    return any(term.lower() in text for term in DRUG_PRODUCT_RESCUE_TERMS)


def _is_collectable(raw: dict[str, Any]) -> bool:
    text = _tier_text(raw)
    pharma_signal = _has_pharma_signal(raw)
    action_signal = _has_actionable_signal(raw)
    low_value = any(term.lower() in text for term in LOW_VALUE_ADMIN_TERMS)
    if low_value and not (action_signal and _has_drug_product_signal(raw)):
        return False
    return action_signal or pharma_signal


def _signal_tier(raw: dict[str, Any]) -> str:
    return "Tier 3" if _has_actionable_signal(raw) else "Tier 2"


def _body(raw: dict[str, Any]) -> str:
    parts = [
        _text(raw, "EXPOSE_CONT"),
        f"처분명: {_text(raw, 'ADM_DISPS_NAME')}" if _text(raw, "ADM_DISPS_NAME") else "",
        f"적용법령: {_text(raw, 'BEF_APPLY_LAW')}" if _text(raw, "BEF_APPLY_LAW") else "",
        f"최종처분일자: {_text(raw, 'LAST_SETTLE_DATE')}" if _text(raw, "LAST_SETTLE_DATE") else "",
        f"공개종료일자: {_text(raw, 'RLS_END_DATE')}" if _text(raw, "RLS_END_DATE") else "",
        f"업체주소: {_text(raw, 'ADDR')}" if _text(raw, "ADDR") else "",
        f"업체번호: {_text(raw, 'ENTP_NO')}" if _text(raw, "ENTP_NO") else "",
        f"사업자등록번호: {_text(raw, 'BIZRNO')}" if _text(raw, "BIZRNO") else "",
        f"품목기준코드: {_text(raw, 'ITEM_SEQ')}" if _text(raw, "ITEM_SEQ") else "",
    ]
    return "\n".join(part for part in parts if part)


def _to_item(raw: dict[str, Any], api_query_url: str) -> IntakeItem | None:
    firm = _text(raw, "ENTP_NAME")
    subject = _text(raw, "ITEM_NAME") or _text(raw, "ADM_DISPS_NAME")
    date_iso = _item_date(raw)
    if not subject or not date_iso:
        return None
    if not _is_collectable(raw):
        return None

    headline = f"[행정처분] {subject}"
    if firm:
        headline = f"{headline} — {firm}"
    signal_tier = _signal_tier(raw)

    # P0 개선: 듀얼 링크 추적성 보강. 항목별 공식 URL이 없어 official_url은 데이터셋(L2)을
    # 유지하되, 품목기준코드(ITEM_SEQ)가 있으면 nedrug 품목 상세 '후보(미검증)' URL을 raw에 남긴다.
    raw_payload: dict[str, Any] = {
        "api": "data.go.kr 15058457",
        "endpoint": ADMIN_API_ENDPOINT,
        **raw,
    }
    item_seq = _text(raw, "ITEM_SEQ")
    if item_seq:
        raw_payload["nedrug_item_candidate_url"] = (
            "https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail"
            f"?itemSeq={urllib.parse.quote(item_seq, safe='')}"
        )
        raw_payload["nedrug_item_candidate_note"] = "Routine 검증 후 인용 (미검증 후보 URL)"

    # E2(resolve & verify, ENABLE_MFDS_URL_VERIFY=on 일 때만): 행정처분 건별 L1 후보를
    # collect 시점에 실제 검증해 scaffold 가 검증된 L1(pass) 또는 L2 인덱스+⚠️(fail)로
    # 조립하게 한다. flag off(기본) 면 키를 남기지 않아 scaffold 가 현행대로 seq→L1 을
    # 단언한다(수집기 동작·golden 불변). K3 관찰·collector 불가침 보호용 게이트.
    adm_seq = _text(raw, "ADM_DISPS_SEQ")
    if _url_verify_enabled() and adm_seq:
        verdict, candidate = _verify_admin_l1(adm_seq, firm)
        raw_payload["admin_l1_verify"] = verdict
        raw_payload["admin_l1_candidate_url"] = candidate

    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=_document_id(raw),
        date_iso=date_iso,
        headline=headline,
        official_url=DATASET_URL,
        type_or_class=TYPE_ADMIN_ACTION,
        firm=firm,
        body=_body(raw),
        api_query=api_query_url,
        qa_relevance="Likely" if signal_tier == "Tier 3" else "Possible",
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier=signal_tier,
        raw_payload=raw_payload,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
    )


def collect_mfds_admin_actions(
    start: date,
    end: date,
    service_key: str,
) -> tuple[list[IntakeItem], str | None]:
    """Collect MFDS administrative action records."""
    if not service_key:
        return [], "DATA_GO_KR_SERVICE_KEY 환경변수 필요"

    items: list[IntakeItem] = []
    seen_ids: set[str] = set()
    tier3_count = 0
    tier2_count = 0
    filtered_count = 0
    page_no = 1
    total_count = 0

    while page_no <= MAX_PAGES:
        params = {
            "serviceKey": service_key,
            "pageNo": page_no,
            "numOfRows": PAGE_SIZE,
            "type": "json",
            "order": "Y",
        }
        masked_url = _api_query(params)
        try:
            data = http_get_json(ADMIN_API_ENDPOINT, params=params, timeout=30, retries=2)
            raw_items, response_page, num_rows, total_count, status = _extract_items(data)
            if not status.startswith("00:"):
                raise RuntimeError(f"API status {status}")
        except Exception as e:  # noqa: BLE001
            msg = f"MFDS admin-action API page={page_no} 실패: {e}"
            if items:
                log("WARN", msg)
                return items, None
            return [], msg

        if not raw_items:
            break

        for raw in raw_items:
            date_iso = _item_date(raw)
            if not _within_window(date_iso, start, end):
                continue
            item = _to_item(raw, masked_url)
            if item is None:
                filtered_count += 1
                continue
            if item.document_id in seen_ids:
                continue
            seen_ids.add(item.document_id)
            items.append(item)
            if item.signal_tier == "Tier 3":
                tier3_count += 1
            else:
                tier2_count += 1

        if total_count and response_page * num_rows >= total_count:
            break
        page_no += 1

    # P2 개선: page cap 도달을 WARN-only가 아니라 truncated 에러로 승격 (loud failure).
    truncated_msg: str | None = None
    if page_no > MAX_PAGES:
        truncated_msg = (f"MFDS admin-action API max_pages={MAX_PAGES} 도달 — truncated "
                         f"(수집 {len(items)}건, totalCount={total_count}, 이후 항목 누락 가능)")
        log("WARN", truncated_msg)

    log(
        "INFO",
        "MFDS admin-action 수집 완료: "
        f"{len(items)}건 (Tier 3={tier3_count}, Tier 2={tier2_count}, "
        f"filtered={filtered_count}, totalCount={total_count})",
    )
    return items, truncated_msg
