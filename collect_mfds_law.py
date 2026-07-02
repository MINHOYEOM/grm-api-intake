#!/usr/bin/env python3
"""GRM MFDS law/admrul collector.

Collects MFDS-administered statutes and administrative rules through the
data.go.kr 1170000 lawSearchList gateway. This is the KR-egress-free
replacement path for the blocked MFDS RSS notice/law boards.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from grm_common import http_get_xml, log, mask_service_key, parse_int_safe
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_API,
    _stable_doc_id,
    _within_window,
)
from collect_mfds import (
    LANGUAGE_KO,
    REGION_MFDS,
    TYPE_NOTICE_FINAL,
    TYPE_REGULATION_FINAL,
    _mfds_relevance,
    _mfds_tier,
)


LAW_SEARCH_ENDPOINT = "https://apis.data.go.kr/1170000/law/lawSearchList.do"
LAW_SERVICE_ENDPOINT = "https://www.law.go.kr/DRF/lawService.do"
DATASET_URL = "https://www.data.go.kr/data/15000115/openapi.do"
MFDS_ORG_CODE = "1471000"

PAGE_SIZE = 100
MAX_PAGES_PER_QUERY = 5
BODY_EXCERPT_LIMIT = 4000

LAW_QUERY_TERMS = [
    "식품의약품안전처",
    "의약품",
    "약사법",
    "마약류",
    "원료의약품",
    "의약외품",
    "생물학적제제",
    "첨단바이오의약품",
    "한약",
    "GMP",
    "우수의약품제조관리기준",
]

TARGET_META = {
    "admrul": {
        "type_or_class": TYPE_NOTICE_FINAL,
        "id_keys": ("행정규칙일련번호", "일련번호", "ID", "행정규칙ID"),
        "title_keys": ("행정규칙명", "행정규칙명한글"),
        "date_keys": ("발령일자", "공포일자", "시행일자"),
        "detail_keys": ("행정규칙상세링크", "상세링크", "법령상세링크"),
        "doc_prefix": "admrul",
        "api_label": "law.go.kr admrul",
    },
    "law": {
        "type_or_class": TYPE_REGULATION_FINAL,
        "id_keys": ("법령일련번호", "ID", "법령ID"),
        "title_keys": ("법령명한글", "법령명", "법령명약칭"),
        "date_keys": ("공포일자", "시행일자"),
        "detail_keys": ("법령상세링크", "상세링크"),
        "doc_prefix": "law",
        "api_label": "law.go.kr law",
    },
}


def _mask_oc(url: str) -> str:
    return re.sub(r"([?&]OC=)[^&]+", r"\1***REDACTED***", url)


def _request_url(params: dict[str, Any]) -> str:
    return LAW_SEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _api_query(params: dict[str, Any]) -> str:
    return mask_service_key(_request_url(params))


def _law_service_url(target: str, fields: dict[str, str], oc: str) -> str:
    meta = TARGET_META[target]
    seq = _first(fields, *meta["id_keys"])
    params = {
        "OC": oc,
        "target": target,
        "ID": seq,
        "type": "XML",
    }
    return LAW_SERVICE_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _node_fields(el: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in el.attrib.items():
        fields[key] = str(value or "").strip()
    for child in list(el):
        name = _local_name(child.tag)
        fields[name] = (child.text or "").strip()
    return fields


def _first(fields: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return ""


def _parse_law_date(raw: str) -> str:
    raw = (raw or "").strip()
    if re.fullmatch(r"\d{8}", raw):
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])).isoformat()
        except ValueError:
            return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            return date.fromisoformat(raw).isoformat()
        except ValueError:
            return ""
    return ""


def _result_status(root: ET.Element) -> tuple[str, str]:
    code = ""
    msg = ""
    for el in root.iter():
        name = _local_name(el.tag)
        text = (el.text or "").strip()
        if name == "resultCode":
            code = text
        elif name == "resultMsg":
            msg = text
    return code, msg


def _total_count(root: ET.Element) -> int:
    for el in root.iter():
        if _local_name(el.tag) in ("totalCnt", "totalCount"):
            return parse_int_safe((el.text or "").strip(), 0)
    return 0


def _find_result_nodes(root: ET.Element, target: str) -> list[ET.Element]:
    meta = TARGET_META[target]
    title_keys = set(meta["title_keys"])
    id_keys = set(meta["id_keys"])
    nodes: list[ET.Element] = []
    seen: set[int] = set()
    for el in root.iter():
        child_names = {_local_name(child.tag) for child in list(el)}
        if child_names & title_keys and (child_names & id_keys or _local_name(el.tag).lower() == "law"):
            ident = id(el)
            if ident not in seen:
                nodes.append(el)
                seen.add(ident)
    return nodes


def _is_mfds(fields: dict[str, str]) -> bool:
    org_code = _first(fields, "소관부처코드", "소관부처기관코드")
    org_name = _first(fields, "소관부처명", "소관부처")
    return org_code == MFDS_ORG_CODE or "식품의약품안전처" in org_name


def _absolute_detail_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.replace("http://www.law.go.kr", "https://www.law.go.kr")
    if raw.startswith("/"):
        return "https://www.law.go.kr" + raw
    return raw


def _document_id(target: str, fields: dict[str, str], title: str, date_iso: str) -> str:
    meta = TARGET_META[target]
    seq = _first(fields, *meta["id_keys"])
    if seq:
        return f"{meta['doc_prefix']}-{seq}"
    digest = hashlib.sha1(
        "|".join([target, title, date_iso, _first(fields, "발령번호", "공포번호")]).encode("utf-8")
    ).hexdigest()[:12]
    return f"{meta['doc_prefix']}-{digest}"


def _body(target: str, fields: dict[str, str]) -> str:
    parts = [
        f"소관부처: {_first(fields, '소관부처명', '소관부처')}" if _first(fields, "소관부처명", "소관부처") else "",
        f"소관부처코드: {_first(fields, '소관부처코드', '소관부처기관코드')}" if _first(fields, "소관부처코드", "소관부처기관코드") else "",
        f"제개정구분: {_first(fields, '제개정구분명', '제개정구분')}" if _first(fields, "제개정구분명", "제개정구분") else "",
        f"종류: {_first(fields, '행정규칙종류', '법령구분명')}" if _first(fields, "행정규칙종류", "법령구분명") else "",
        f"발령번호: {_first(fields, '발령번호', '공포번호')}" if _first(fields, "발령번호", "공포번호") else "",
        f"발령/공포일자: {_first(fields, '발령일자', '공포일자')}" if _first(fields, "발령일자", "공포일자") else "",
        f"시행일자: {_first(fields, '시행일자')}" if _first(fields, "시행일자") else "",
        f"현행여부: {_first(fields, '현행여부')}" if _first(fields, "현행여부") else "",
    ]
    detail = _absolute_detail_url(_first(fields, *TARGET_META[target]["detail_keys"]))
    if detail:
        parts.append(f"상세링크: {detail}")
    return "\n".join(part for part in parts if part)


_BODY_TAG_HINTS = (
    "조문내용",
    "조문제목",
    "항내용",
    "호내용",
    "목내용",
    "부칙내용",
    "별표내용",
    "본문",
    "전문",
    "개정문",
    "제정이유",
    "제개정이유",
)
_BODY_FALLBACK_EXCLUDE = {
    "resultCode",
    "resultMsg",
    "처리결과",
    "행정규칙일련번호",
    "법령일련번호",
    "ID",
    "소관부처코드",
    "소관부처기관코드",
    "소관부처명",
    "시행일자",
    "발령일자",
    "공포일자",
}


def _clean_body_line(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def _extract_body_excerpt(root: ET.Element) -> str:
    hinted: list[str] = []
    fallback: list[str] = []
    seen: set[str] = set()
    for el in root.iter():
        name = _local_name(el.tag)
        text = _clean_body_line(el.text or "")
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        if any(hint in name for hint in _BODY_TAG_HINTS):
            hinted.append(text)
        elif not list(el) and name not in _BODY_FALLBACK_EXCLUDE:
            fallback.append(text)

    out: list[str] = []
    total_len = 0
    for line in hinted or fallback:
        if total_len + len(line) + 1 > BODY_EXCERPT_LIMIT:
            remaining = max(BODY_EXCERPT_LIMIT - total_len - 4, 0)
            if remaining:
                out.append(line[:remaining].rstrip() + "...")
            break
        out.append(line)
        total_len += len(line) + 1
    return "\n".join(out).strip()


def _fetch_body_excerpt(
    target: str,
    fields: dict[str, str],
    law_go_kr_oc: str,
) -> tuple[str, str, str]:
    """Fetch law.go.kr full text when OC is configured.

    For now this enriches MFDS administrative rules (고시/훈령/예규). The list
    collector remains authoritative even when this optional fetch fails.
    """
    oc = (law_go_kr_oc or "").strip()
    if target != "admrul" or not oc or not _first(fields, *TARGET_META[target]["id_keys"]):
        return "", "", ""

    url = _law_service_url(target, fields, oc)
    masked_url = _mask_oc(url)
    try:
        root = http_get_xml(url, timeout=30, retries=2)
        return _extract_body_excerpt(root), masked_url, ""
    except Exception as e:  # noqa: BLE001
        return "", masked_url, str(e)


def _to_item(
    target: str,
    fields: dict[str, str],
    api_query_url: str,
    *,
    start: date | None = None,
    end: date | None = None,
    law_go_kr_oc: str = "",
    seen_ids: set[str] | None = None,
) -> IntakeItem | None:
    meta = TARGET_META[target]
    title = _first(fields, *meta["title_keys"])
    date_iso = ""
    for key in meta["date_keys"]:
        date_iso = _parse_law_date(_first(fields, key))
        if date_iso:
            break
    if not title or not date_iso:
        return None
    if start is not None and end is not None and not _within_window(date_iso, start, end):
        return None
    if not _is_mfds(fields):
        return None

    document_id = _document_id(target, fields, title, date_iso)
    # dedup 을 본문 fetch **이전**으로: 같은 admrul 이 여러 query term 에 걸쳐 반복돼도
    # _fetch_body_excerpt(네트워크)를 문서당 1회만 수행(옛 코드는 마지막에 dedup → 최대 11회 낭비).
    if seen_ids is not None:
        if document_id in seen_ids:
            return None
        seen_ids.add(document_id)
    body = _body(target, fields)
    body_excerpt, body_query_url, body_error = _fetch_body_excerpt(target, fields, law_go_kr_oc)
    if body_excerpt:
        body = "\n".join(part for part in (body, f"본문 발췌:\n{body_excerpt}") if part)
    elif body_error:
        log("WARN", f"law.go.kr 본문 fetch 실패 doc={document_id}: {body_error}")
    relevance = _mfds_relevance(title, body)
    if relevance == "Pending":
        return None
    type_or_class = str(meta["type_or_class"])
    official_url = _absolute_detail_url(_first(fields, *meta["detail_keys"])) or DATASET_URL
    raw_payload = {
        "api": meta["api_label"],
        "endpoint": LAW_SEARCH_ENDPOINT,
        "target": target,
        **fields,
    }
    if body_query_url:
        raw_payload["law_go_kr_body_query"] = body_query_url
        raw_payload["law_go_kr_body_fetch_ok"] = bool(body_excerpt)
    if body_excerpt:
        raw_payload["law_go_kr_body_excerpt"] = body_excerpt
    if body_error:
        raw_payload["law_go_kr_body_error"] = body_error
    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=document_id,
        date_iso=date_iso,
        headline=title,
        official_url=official_url,
        type_or_class=type_or_class,
        body=body,
        api_query=api_query_url,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_API,
        signal_tier=_mfds_tier(type_or_class, relevance, title, body),
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
        raw_payload=raw_payload,
    )


def _collect_target_query(
    *,
    target: str,
    query: str,
    start: date,
    end: date,
    service_key: str,
    law_go_kr_oc: str = "",
    seen_ids: set[str] | None = None,
) -> tuple[list[IntakeItem], str | None]:
    items: list[IntakeItem] = []
    page_no = 1
    total_count = 0
    while page_no <= MAX_PAGES_PER_QUERY:
        params = {
            "serviceKey": service_key,
            "target": target,
            "query": query,
            "numOfRows": PAGE_SIZE,
            "pageNo": page_no,
        }
        masked_url = _api_query(params)
        try:
            root = http_get_xml(_request_url(params), timeout=30, retries=2)
            result_code, result_msg = _result_status(root)
            if result_code and result_code != "00":
                raise RuntimeError(f"API status {result_code}:{result_msg}")
            total_count = _total_count(root)
            nodes = _find_result_nodes(root, target)
        except Exception as e:  # noqa: BLE001
            return [], f"MFDS law API target={target} query={query!r} page={page_no} 실패: {e}"

        if not nodes:
            break
        for node in nodes:
            fields = _node_fields(node)
            item = _to_item(
                target,
                fields,
                masked_url,
                start=start,
                end=end,
                law_go_kr_oc=law_go_kr_oc,
                seen_ids=seen_ids,
            )
            if item is None:
                continue
            items.append(item)
        if total_count and page_no * PAGE_SIZE >= total_count:
            break
        page_no += 1
    return items, None


def collect_mfds_law(
    start: date,
    end: date,
    service_key: str,
    law_go_kr_oc: str = "",
) -> tuple[list[IntakeItem], str | None]:
    """Collect MFDS law and administrative-rule signals."""
    if not service_key:
        return [], "DATA_GO_KR_SERVICE_KEY 환경변수 필요"

    items: list[IntakeItem] = []
    errors: list[str] = []
    # seen_ids 를 target/query 루프 전체에서 공유해 _to_item 이 본문 fetch 이전에 dedup 하게 한다
    # (중복 admrul 의 반복 본문 fetch 제거 — 이전엔 이 루프 끝에서 dedup 했다).
    seen_ids: set[str] = set()
    for target in ("admrul", "law"):
        for query in LAW_QUERY_TERMS:
            got, err = _collect_target_query(
                target=target,
                query=query,
                start=start,
                end=end,
                service_key=service_key,
                law_go_kr_oc=law_go_kr_oc,
                seen_ids=seen_ids,
            )
            if err:
                errors.append(err)
                log("WARN", err)
                continue
            items.extend(got)  # _to_item 이 seen_ids 로 이미 dedup 완료

    if errors and not items:
        return [], "; ".join(errors[:3])
    if errors:
        # 부분 실패(items 확보)를 error 반환 대신 consolidated WARN 으로 표면화(침묵 제거).
        log("WARN", f"MFDS law/admrul API 부분 실패 {len(errors)}건 (items {len(items)}건 확보): "
                    + "; ".join(errors[:3]))
    log("INFO", f"MFDS law/admrul API 수집 완료: {len(items)}건 (부분오류={len(errors)})")
    return items, None
