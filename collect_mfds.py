#!/usr/bin/env python3
"""GRM MFDS Collector — Phase 2b-1.

ENABLE_MFDS=true 일 때 collect_intake.main() 에서 호출된다.

수집 대상 (Phase 2b-1 1차 RSS 확장 — 회수·판매중지/실태조사는 Phase 2c):
  RSS 보드 (mfds.go.kr/www/rss/brd.do?brdId=) → Type or Class
    data0013 안내서/지침     → guidance-industry
    data0011 민원인안내서    → guidance-industry
    data0010 공무원지침서    → guidance-internal
    data0009 입법/행정예고   → legislative-notice (scheduled primary; ogLmPp optional)
    data0008 최근 개정 법령  → regulation-final
    data0005 고시전문        → notice-final
    seohan001 안전성 서한    → safety-letter (Tier 3 floor)
  핵심 원칙: Type은 보드/문서 구조, GMP·품질 여부는 내용 신호(qa_relevance·Signal Tier)로 분리.
  ogLmPp API는 ENABLE_MOLEG_API=true + 인가/IP 환경에서만 호출 (입법예고 optional).

설계 노트:
- 공유 모델/헬퍼는 collect_intake 에서 import 해 중복을 피한다.
  collect_intake 는 collect_mfds 를 main() 내부에서 lazy import 하므로 순환참조가 없다
  (이 모듈 import 시점엔 collect_intake 가 이미 완전히 로드되어 있다).
- HTTP / 429 Retry-After 는 grm_common helper 를 재사용한다.
- 한국어 본문은 단어경계(\\b) 매칭이 부적합하므로, 한국어 키워드는 substring 매칭으로 분기한다.
- 모든 항목: source=MFDS, source_type=Official Regulatory Page, language=KO,
  region_jurisdiction=Korea (MFDS), Evidence 후보는 Routine 이 최종 판정(여기선 미설정).
"""

from __future__ import annotations

import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from grm_common import http_get_xml, log

# collect_intake 의 공유 자산 재사용 (private 헬퍼 포함 — 동일 코드베이스 내부용)
from collect_intake import (
    IntakeItem,
    SOURCE_MFDS,
    SRC_TYPE_OFFICIAL_PAGE,
    compute_relevance,
    compute_signal_tier,
    _rss2_items_from_root,
    _atom_entries_from_root,
    _atom_text,
    _atom_link,
    _rss_text,
    _parse_rss2_date,
    _parse_atom_date,
    _within_window,
    _stable_doc_id,
)


# ── Notion Type or Class 영문 라우팅 키 (한국어 본문은 그대로, 분류 라벨만 영문) ──
TYPE_GMP_GUIDELINE = "gmp-guideline"
TYPE_LEGISLATIVE_NOTICE = "legislative-notice"
TYPE_GMP_INSPECTION = "gmp-inspection"   # Phase 2b-2 예약
# Phase 2b-1 1차 확장 Type (보드 구조 기반)
TYPE_REGULATION_FINAL = "regulation-final"
TYPE_NOTICE_FINAL = "notice-final"
TYPE_GUIDANCE_INDUSTRY = "guidance-industry"
TYPE_GUIDANCE_INTERNAL = "guidance-internal"
TYPE_SAFETY_LETTER = "safety-letter"
LANGUAGE_KO = "KO"
REGION_MFDS = "Korea (MFDS)"

# ── MFDS RSS endpoint (2026-05-29 확인) ──
# feed 패턴: https://www.mfds.go.kr/www/rss/brd.do?brdId={brdId}
MFDS_RSS_BASE = "https://www.mfds.go.kr/www/rss/brd.do"
# (brdId, Type or Class) — Type은 보드 구조 기준. 같은 Type을 공유하는 보드도 허용된다.
# GMP·품질 여부는 Type이 아니라 내용 신호(_mfds_relevance / _mfds_tier)로 판정한다.
MFDS_RSS_BOARDS: list[tuple[str, str]] = [
    ("data0013", TYPE_GUIDANCE_INDUSTRY),    # 안내서/지침
    ("data0011", TYPE_GUIDANCE_INDUSTRY),    # 민원인안내서
    ("data0010", TYPE_GUIDANCE_INTERNAL),    # 공무원지침서
    ("data0009", TYPE_LEGISLATIVE_NOTICE),   # 입법/행정예고 (legislative — ogLmPp optional)
    ("data0008", TYPE_REGULATION_FINAL),     # 최근 개정 법령
    ("data0005", TYPE_NOTICE_FINAL),         # 고시전문
    ("seohan001", TYPE_SAFETY_LETTER),       # 안전성 서한 (watch)
]
_MFDS_RSS_BOARD_MAP = dict(MFDS_RSS_BOARDS)
_MFDS_RSS_RESIDUAL_BOARD_IDS = {"data0013", "data0011", "data0010"}
# 입법예고 RSS 보드 (ogLmPp fallback이 사용)
LEGISLATIVE_RSS_BRDID = "data0009"

# data.go.kr / 법제처 정부입법예고 API (법제처 가이드 type=4, data ID 15058407)
# GitHub Actions 러너는 출발 IP가 동적이므로 scheduled primary는 MFDS RSS다.
# ogLmPp는 ENABLE_MOLEG_API=true로 명시 opt-in된 환경에서만 collect_intake가 key를 전달한다.
DATAGO_LEGISLATIVE_DATASET_ID = "15058407"
DATAGO_LEGISLATIVE_URL = "https://www.lawmaking.go.kr/rest/ogLmPp.xml"
DATAGO_LEGISLATIVE_GUIDE_URL = "https://opinion.lawmaking.go.kr/api/apiGuideInfo?type=4"
DATAGO_MFDS_ORG_CODE = "1471000"  # 식품의약품안전처

# Dublin Core date 네임스페이스 (일부 RSS 가 pubDate 대신 dc:date 사용)
_DC_DATE_TAG = "{http://purl.org/dc/elements/1.1/}date"

# ── 한국어 관련성 키워드 (substring 매칭 — \\b 미사용) ──
# 주의: "식품의약품안전처" 기관명 안의 "의약품"은 의약품 규제 신호가 아니다.
# _content_blob()에서 기관명을 제거한 뒤 매칭한다.
MFDS_GMP_TERMS = [
    "gmp", "우수의약품제조관리기준", "제조 및 품질관리", "제조·품질관리",
    "제조품질관리", "제조관리", "품질관리", "제조소", "제조업",
    "밸리데이션", "데이터 완전성", "자료 완전성",
    "일탈", "변경관리", "시정조치", "무균", "멸균",
    "적합판정", "실태조사", "원료의약품 제조", "완제의약품 제조",
    "품질평가", "품질·비임상", "품질 비임상", "비임상 평가",
    "비교동등성", "비교동등성 평가",
]
MFDS_PHARMA_LEGISLATIVE_TERMS = [
    "의약품", "원료의약품", "완제의약품", "의약외품",
    "생물학적제제", "바이오의약품", "첨단바이오의약품",
    "생균치료제", "혈장분획제제", "세포치료제", "유전자치료제",
    "한약", "약사법", "품목허가", "심사 규정", "허가·심사",
    "의약품 등의 안전에 관한 규칙",
]
MFDS_KO_BOOST = [
    "우수의약품제조관리기준", "데이터 완전성", "자료 완전성",
    "밸리데이션", "무균", "멸균", "적합판정", "불순물", "니트로사민",
]
MFDS_KO_EXCLUDE_TERMS = [
    "의료기기", "체외진단의료기기", "화장품", "건강기능식품",
    "식품", "축산물", "수입식품",
]
MFDS_KO_RESCUE_TERMS = [
    "의약품", "의약외품", "원료의약품", "완제의약품", "생물학적제제",
    "바이오의약품", "첨단바이오의약품", "생균치료제", "혈장분획제제",
    "세포치료제", "유전자치료제", "한약", "약사법",
    "우수의약품제조관리기준", "gmp",
]

_SEQ_RE = re.compile(r"[?&]seq=(\d+)")
_DATAGO_FIELD_NAMES = {
    "ogLmPpSeq", "lsNm", "lsClsNm", "asndOfiNm", "pntcNo", "pntcDt",
    "stYd", "edYd", "FileName", "FileDownLink", "readCnt",
    "mappingLbicId", "announceType",
}
_DATAGO_DATE_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})")


def _doc_id(brd_id: str, link: str, title: str, date_iso: str) -> str:
    """MFDS 게시물 seq 우선(보드별 namespace), 없으면 콘텐츠 기반 안정 ID."""
    m = _SEQ_RE.search(link or "")
    if m:
        return f"{brd_id}-{m.group(1)}"
    return _stable_doc_id(SOURCE_MFDS, title, link, date_iso)


def _content_blob(title: str, body: str) -> str:
    blob = " ".join(p for p in (title, body) if p)
    # 기관명 제거: "식품의약품안전처 공고" 같은 일반 문구가 의약품 신호로 오탐되는 것 방지.
    return blob.replace("식품의약품안전처", "").replace("식약처", "")


def _mfds_signal_counts(title: str, body: str) -> tuple[int, int, int, int, int, str]:
    """관련성/Tier 판정용 신호 카운트.

    반환: (gmp_hits, pharma_hits, ko_boost, exclude_hits, rescue_hits, eng)
    """
    blob = _content_blob(title, body)
    gmp_hits = sum(1 for kw in MFDS_GMP_TERMS if kw.lower() in blob.lower())
    pharma_hits = sum(1 for kw in MFDS_PHARMA_LEGISLATIVE_TERMS if kw in blob)
    ko_boost = sum(1 for kw in MFDS_KO_BOOST if kw in blob)
    exclude_hits = sum(1 for kw in MFDS_KO_EXCLUDE_TERMS if kw in blob)
    rescue_hits = sum(1 for kw in MFDS_KO_RESCUE_TERMS if kw.lower() in blob.lower())
    eng = compute_relevance(title, body)  # 영어 단어경계 매칭
    return gmp_hits, pharma_hits, ko_boost, exclude_hits, rescue_hits, eng


def _mfds_relevance(title: str, body: str) -> str:
    """통합 QA 관련성 휴리스틱 (Type 무관 — Type은 보드 구조, GMP는 내용 신호).

    수집 게이트: 의약품/GMP 관련성이 전무하고 영어로도 무관하면 Pending(drop).
    최종 판정은 Routine 에 위임 — 여기서는 보수적으로 태깅한다.
    """
    gmp_hits, pharma_hits, ko_boost, exclude_hits, rescue_hits, eng = _mfds_signal_counts(title, body)
    # 식품/화장품/의료기기 일반 항목은 제외하되, 의약품·GMP 신호가 같이 있으면 구제한다.
    if exclude_hits > 0 and rescue_hits == 0 and gmp_hits == 0:
        return "Pending"
    if pharma_hits == 0 and gmp_hits == 0 and eng not in ("Likely", "Possible"):
        return "Pending"
    if eng == "Likely" or gmp_hits >= 1 or ko_boost >= 1:
        return "Likely"
    return "Possible"


_TIER_RANK = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3}
_RANK_TIER = {1: "Tier 1", 2: "Tier 2", 3: "Tier 3"}


def _mfds_tier(type_or_class: str, relevance: str, title: str, body: str) -> str:
    """Signal Tier: 기본 compute_signal_tier 에 MFDS Type/내용 floor 적용.

    - safety-letter 또는 고신호 키워드(무균·데이터완전성·적합판정·불순물 등) → Tier 3 floor
    - GMP 일반 신호 → Tier 2 floor
    """
    base = compute_signal_tier(SOURCE_MFDS, type_or_class, relevance, "N/A", title, body)
    rank = _TIER_RANK.get(base, 1)
    gmp_hits, _pharma, ko_boost, _exclude, _rescue, _eng = _mfds_signal_counts(title, body)
    if type_or_class == TYPE_SAFETY_LETTER or ko_boost >= 1:
        rank = max(rank, 3)
    elif type_or_class in (TYPE_REGULATION_FINAL, TYPE_NOTICE_FINAL) and relevance != "Pending":
        rank = max(rank, 2)
    elif gmp_hits >= 1:
        rank = max(rank, 2)
    return _RANK_TIER[rank]


def _build_item(*, brd_id: str, type_or_class: str, feed_url: str,
                title: str, link: str, date_iso: str, body: str,
                raw: dict[str, Any]) -> IntakeItem:
    title = (title or "").strip()
    link = (link or "").strip()
    body = (body or "").strip()
    relevance = _mfds_relevance(title, body)
    # P0 개선: 안전성 서한은 정의상 즉시성 안전 신호다. 관련성 게이트(Pending)로
    # 떨어뜨리지 않고 최소 Possible로 올려, 아래 _mfds_tier의 Tier 3 floor까지 보낸다.
    if relevance == "Pending" and type_or_class == TYPE_SAFETY_LETTER:
        relevance = "Possible"
    tier = _mfds_tier(type_or_class, relevance, title, body)
    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=_doc_id(brd_id, link, title, date_iso),
        date_iso=date_iso,
        headline=title,
        official_url=link,
        type_or_class=type_or_class,
        body=body,
        api_query=feed_url,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=tier,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
        raw_payload=raw,
    )


def _law_date_param(d: date) -> str:
    """법제처 ogLmPp API 날짜 파라미터 형식: YYYY. M. D.

    공식 예제의 ``YYYY.+M.+D.``는 query string에서 공백이 ``+``로 인코딩된 형태다.
    """
    return f"{d.year}. {d.month}. {d.day}."


def _mask_oc(url: str) -> str:
    return re.sub(r"([?&]OC=)[^&]+", r"\1***REDACTED***", url)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(el: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(el):
        if _local_name(child.tag) in wanted:
            return (child.text or "").strip()
    return ""


def _node_fields(el: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for child in list(el):
        name = _local_name(child.tag)
        if name in _DATAGO_FIELD_NAMES:
            fields[name] = (child.text or "").strip()
    return fields


def _find_datago_item_nodes(root: ET.Element) -> list[ET.Element]:
    """ogLmPp XML에서 항목 노드를 루트명에 의존하지 않고 찾는다."""
    nodes: list[ET.Element] = []
    seen: set[int] = set()
    for el in root.iter():
        child_names = {_local_name(child.tag) for child in list(el)}
        if {"ogLmPpSeq", "lsNm"}.issubset(child_names):
            ident = id(el)
            if ident not in seen:
                nodes.append(el)
                seen.add(ident)
    return nodes


def _datago_retmsg(root: ET.Element) -> str:
    for el in root.iter():
        if _local_name(el.tag) == "retMsg":
            return (el.text or "").strip()
    return ""


def _parse_moleg_date(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    m = _DATAGO_DATE_RE.search(raw)
    if not m:
        return ""
    year, month, day = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return ""


def _datago_detail_url(fields: dict[str, str]) -> str:
    seq = fields.get("ogLmPpSeq", "").strip()
    mapping = fields.get("mappingLbicId", "").strip()
    announce_type = fields.get("announceType", "").strip()
    if not seq:
        return fields.get("FileDownLink", "").strip()
    params = {}
    if mapping:
        params["mappingLbicId"] = mapping
    if announce_type:
        params["announceType"] = announce_type
    query = urllib.parse.urlencode(params)
    return f"https://opinion.lawmaking.go.kr/gcom/ogLmPp/{seq}" + (f"?{query}" if query else "")


def _datago_item_to_intake(fields: dict[str, str],
                           *, api_query_url: str, start: date, end: date) -> IntakeItem | None:
    title = fields.get("lsNm", "").strip()
    agency = fields.get("asndOfiNm", "").strip()
    date_iso = _parse_moleg_date(fields.get("pntcDt", "")) or _parse_moleg_date(fields.get("stYd", ""))
    if not title or not _within_window(date_iso, start, end):
        return None
    if agency and "식품의약품안전처" not in agency:
        return None

    body_parts = [
        f"소관부처: {agency}" if agency else "",
        f"법령종류: {fields.get('lsClsNm', '').strip()}" if fields.get("lsClsNm") else "",
        f"공고번호: {fields.get('pntcNo', '').strip()}" if fields.get("pntcNo") else "",
        f"공고일자: {fields.get('pntcDt', '').strip()}" if fields.get("pntcDt") else "",
        f"예고기간: {fields.get('stYd', '').strip()} ~ {fields.get('edYd', '').strip()}"
        if fields.get("stYd") or fields.get("edYd") else "",
        f"첨부: {fields.get('FileName', '').strip()}" if fields.get("FileName") else "",
    ]
    body = "\n".join(part for part in body_parts if part)
    relevance = _mfds_relevance(title, body)
    if relevance == "Pending":
        return None

    seq = fields.get("ogLmPpSeq", "").strip()
    official_url = _datago_detail_url(fields)
    document_id = f"ogLmPp-{seq}" if seq else _stable_doc_id(SOURCE_MFDS, title, official_url, date_iso)
    tier = _mfds_tier(TYPE_LEGISLATIVE_NOTICE, relevance, title, body)
    return IntakeItem(
        source=SOURCE_MFDS,
        document_id=document_id,
        date_iso=date_iso,
        headline=title,
        official_url=official_url,
        type_or_class=TYPE_LEGISLATIVE_NOTICE,
        body=body,
        api_query=api_query_url,
        qa_relevance=relevance,
        osd_relevance="N/A",
        source_type=SRC_TYPE_OFFICIAL_PAGE,
        signal_tier=tier,
        language=LANGUAGE_KO,
        region_jurisdiction=REGION_MFDS,
        raw_payload={
            "api": "ogLmPp",
            "dataset_id": DATAGO_LEGISLATIVE_DATASET_ID,
            "guide": DATAGO_LEGISLATIVE_GUIDE_URL,
            **fields,
        },
    )


def _rss2_fields(el: Any) -> tuple[str, str, str, str]:
    """RSS 2.0 <item> → (title, link, date_iso, description)."""
    title = _rss_text(el.find("title"))
    link_el = el.find("link")
    link = _rss_text(link_el)
    if not link and link_el is not None:
        # 일부 피드는 <link> 를 CDATA/tail 로 둔다
        link = (link_el.tail or "").strip()
    pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
    if not pub_raw:
        pub_raw = _rss_text(el.find(_DC_DATE_TAG))
    date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
    desc = _rss_text(el.find("description"))
    return title, link, date_iso, desc


def _atom_fields(e: Any) -> tuple[str, str, str, str]:
    """Atom <entry> → (title, link, date_iso, summary/content)."""
    title = _atom_text(e, "title")
    link = _atom_link(e)
    pub_raw = _atom_text(e, "updated") or _atom_text(e, "published")
    date_iso = _parse_atom_date(pub_raw) if pub_raw else ""
    desc = _atom_text(e, "summary") or _atom_text(e, "content")
    return title, link, date_iso, desc


def _collect_rss_feed(type_or_class: str, brd_id: str,
                      start: date, end: date) -> list[IntakeItem]:
    """단일 MFDS RSS 피드 수집. RSS 2.0 / Atom 양형식 처리.

    실패 시 예외를 던진다 — 호출부가 graceful degradation 을 결정한다.
    """
    feed_url = f"{MFDS_RSS_BASE}?brdId={brd_id}"
    log("INFO", f"MFDS RSS 수집: {type_or_class} ({feed_url})")
    root = http_get_xml(feed_url)

    rss_items = _rss2_items_from_root(root)
    use_atom = not rss_items
    nodes = _atom_entries_from_root(root) if use_atom else rss_items

    items: list[IntakeItem] = []
    skipped_no_date = 0
    skipped_out_of_scope = 0
    for node in nodes:
        title, link, date_iso, desc = (_atom_fields(node) if use_atom
                                       else _rss2_fields(node))
        if not _within_window(date_iso, start, end):
            if not date_iso:
                skipped_no_date += 1
            continue
        relevance = _mfds_relevance(title, desc)
        # P0 개선: 안전성 서한 보드는 Pending이어도 드롭하지 않는다(_build_item에서 floor 처리).
        if relevance == "Pending" and type_or_class != TYPE_SAFETY_LETTER:
            skipped_out_of_scope += 1
            continue
        items.append(_build_item(
            brd_id=brd_id, type_or_class=type_or_class, feed_url=feed_url,
            title=title, link=link, date_iso=date_iso, body=desc,
            raw={"brdId": brd_id, "title": title, "link": link,
                 "date": date_iso, "description": desc,
                 "format": "atom" if use_atom else "rss2"},
        ))
    log("INFO", f"MFDS RSS '{type_or_class}' 완료: {len(items)}건 "
                f"(범위밖 skip={skipped_out_of_scope}, 무날짜 skip={skipped_no_date})")
    return items


def _configured_rss_boards() -> list[tuple[str, str]]:
    """Return MFDS RSS boards after optional residual-only selection.

    Defaults to all boards for no-regression. For KR-egress runs where data.go.kr
    APIs are the source of truth, set ``MFDS_RSS_BOARD_MODE=residual`` to fetch
    only guidance/internal-guidance boards. Use ``MFDS_RSS_BOARD_IDS`` for an
    explicit comma/space separated board allowlist.
    """
    raw_ids = os.environ.get("MFDS_RSS_BOARD_IDS", "").strip()
    if raw_ids:
        selected: list[tuple[str, str]] = []
        invalid: list[str] = []
        for brd_id in re.split(r"[\s,]+", raw_ids):
            if not brd_id:
                continue
            type_or_class = _MFDS_RSS_BOARD_MAP.get(brd_id)
            if type_or_class:
                selected.append((brd_id, type_or_class))
            else:
                invalid.append(brd_id)
        if invalid:
            log("WARN", f"MFDS_RSS_BOARD_IDS 무시된 보드: {', '.join(invalid)}")
        if selected:
            return selected
        log("WARN", "MFDS_RSS_BOARD_IDS 유효 보드 0건 — 기본 전체 보드 사용")

    mode = os.environ.get("MFDS_RSS_BOARD_MODE", "all").strip().lower()
    if mode == "residual":
        return [
            (brd_id, type_or_class)
            for brd_id, type_or_class in MFDS_RSS_BOARDS
            if brd_id in _MFDS_RSS_RESIDUAL_BOARD_IDS
        ]
    if mode and mode != "all":
        log("WARN", f"MFDS_RSS_BOARD_MODE={mode!r} 미지원 — 기본 전체 보드 사용")
    return list(MFDS_RSS_BOARDS)


def _collect_legislative_datago(start: date, end: date, key: str) -> list[IntakeItem] | None:
    """법제처 ogLmPp 정부입법예고 API optional 경로.

    공식 가이드(type=4) 기준 필드:
    ogLmPpSeq, lsNm, lsClsNm, asndOfiNm, pntcNo, pntcDt, stYd, edYd,
    FileName, FileDownLink, readCnt, mappingLbicId, announceType.

    API가 HTTP 200 + <retMsg>401</retMsg> 형태로 권한 오류를 반환할 수 있으므로,
    이 경우는 None을 반환해 RSS fallback에 위임한다.
    """
    params = {
        "OC": key,
        "cptOfiOrgCd": DATAGO_MFDS_ORG_CODE,
        "stYdFmt": _law_date_param(start),
        "edYdFmt": _law_date_param(end),
    }
    url = DATAGO_LEGISLATIVE_URL + "?" + urllib.parse.urlencode(params)
    masked_url = _mask_oc(url)
    log("INFO", f"법제처 ogLmPp 입법예고 API 호출: {masked_url}")

    root = http_get_xml(url)
    retmsg = _datago_retmsg(root)
    if retmsg:
        log("WARN", f"법제처 ogLmPp API retMsg={retmsg} - RSS fallback 사용")
        return None

    items: list[IntakeItem] = []
    skipped = 0
    nodes = _find_datago_item_nodes(root)
    for node in nodes:
        item = _datago_item_to_intake(
            _node_fields(node),
            api_query_url=masked_url,
            start=start,
            end=end,
        )
        if item is None:
            skipped += 1
            continue
        items.append(item)

    log("INFO", f"법제처 ogLmPp 입법예고 API 완료: {len(items)}건 (skip={skipped})")
    return items


def _collect_legislative(start: date, end: date,
                         key: str | None) -> tuple[list[IntakeItem], str | None]:
    """입법예고: RSS scheduled primary, ogLmPp는 opt-in key 전달 시 먼저 시도."""
    if key:
        try:
            api_items = _collect_legislative_datago(start, end, key)
            if api_items is not None:
                return api_items, None
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 RSS 로 안전 강등
            log("WARN", f"data.go.kr 입법예고 API 실패 → RSS fallback: {e}")
    try:
        rss_items = _collect_rss_feed(
            TYPE_LEGISLATIVE_NOTICE, LEGISLATIVE_RSS_BRDID, start, end)
        return rss_items, None
    except Exception as e:  # noqa: BLE001
        return [], f"MFDS 입법예고 RSS 실패: {e}"


def collect_mfds(start: date, end: date,
                 data_go_kr_key: str | None = None) -> tuple[list[IntakeItem], str | None]:
    """Phase 2b-1 MFDS 수집 진입점.

    반환: (items, error_msg).
    error_msg 는 **모든 하위 소스가 실패해 한 건도 못 모았을 때만** 채운다
    (collect_intake.main 이 enable_mfds + mfds_error 시 exit 1 하므로).
    부분 실패는 WARN 로그만 남기고 graceful degradation.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []

    # ① 비-입법 RSS 보드 일괄 (지침/안내서/개정법령/고시/안전성서한)
    #    입법예고(data0009)는 ②에서 ogLmPp optional 포함해 별도 처리.
    rss_boards = _configured_rss_boards()
    selected_board_ids = {brd_id for brd_id, _type_or_class in rss_boards}
    for brd_id, type_or_class in rss_boards:
        if brd_id == LEGISLATIVE_RSS_BRDID:
            continue
        try:
            items += _collect_rss_feed(type_or_class, brd_id, start, end)
        except Exception as e:  # noqa: BLE001
            msg = f"MFDS RSS({brd_id}/{type_or_class}) 실패: {e}"
            log("WARN", msg)
            errors.append(msg)

    # ② 입법예고 (RSS primary; ogLmPp optional when key is passed)
    if LEGISLATIVE_RSS_BRDID in selected_board_ids:
        leg_items, leg_err = _collect_legislative(start, end, data_go_kr_key)
        items += leg_items
        if leg_err:
            errors.append(leg_err)

    if errors and not items:
        # 전체 실패 → workflow fail 유도
        return [], "; ".join(errors)

    log("INFO", f"MFDS 수집 완료: {len(items)}건 (부분오류={len(errors)})")
    return items, None
