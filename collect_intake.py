#!/usr/bin/env python3
"""
GRM API Intake Collector — v15.1 Phase 2

Federal Register API + OpenFDA Drug Enforcement API + RSS 피드 (EMA · MHRA · PIC/S · ECA)
+ FDA Warning Letters 를 호출해 지난 7일 (KST 기준) 항목을 수집하고,
Notion "GRM API Intake" 데이터베이스에 raw 필드를 저장한다.

설계 원칙:
1. KST 기준 7일 윈도우 — 모든 날짜는 Asia/Seoul 로 계산
2. raw API 필드 보존 — Evidence A 조건 충족을 위해 원문 JSON 도 페이지 본문에 보관
3. graceful degradation — 한 소스 실패해도 다른 소스 계속 진행
4. 중복 제거 — source::document_id 키로 Run Date 내 중복 skip
5. QA relevance 1 차 휴리스틱만 부여, 최종 판정은 Routine 위임
6. Source Type 분류 — Official API / Official Regulatory Page / Official Regulator Blog
                      / Expert Secondary

환경 변수 (GitHub Secrets):
- NOTION_TOKEN       : Notion Integration token (secret_…)
- NOTION_DATABASE_ID : "GRM API Intake" DB ID
- OPENFDA_API_KEY    : OpenFDA 무료 API key (선택, 없으면 no-key 사용)

CLI 옵션:
- --dry-run : Notion 호출 없이 stdout 에 요약만 출력
- --window-days N : 기본 7. 백필 테스트 시 변경
- --sources : 수집 소스 선택 (기본: all). 예: --sources fr recall ema mhra pics eca wl
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import unescape as _html_unescape
from html.parser import HTMLParser
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

from grm_common import (
    HTTPClientError,
    env_flag,
    http_get_json,
    http_get_xml,
    log,
    retry_after_seconds,
)
# K2: 결정론 카드 골격 조립기 (같은 폴더 평면 모듈)
from card_scaffold import (
    assemble_web_brief,
    build_card_scaffold,
    compute_render_plan,
    merge_recall_cards,
)

# ── [리팩토링 배치3 Phase1] health 판정 층을 grm_health 로 분리(verbatim 이동). 아래 재수출로
#    기존 참조 경로(collect_intake._evaluate_health 등)를 전부 보존한다(하위호환·테스트 무수정). ──
from grm_health import (
    HealthCheckResult,
    HealthFinding,
    _GLOBAL_PUBLIC_SOURCE_CODES,
    _MFDS_FEATURE_SOURCE_CODES,
    _MFDS_PUBLIC_ENDPOINT_SOURCE_CODES,
    _PUBLIC_ENDPOINT_403_SOURCE_CODES,
    _TRANSIENT_ELIGIBLE_SOURCE_CODES,
    _TRANSIENT_ERROR_MARKERS,
    _evaluate_health,
    _is_transient_source_error,
    _source_health_rows,
    _write_health_json,
    _write_health_summary,
)

# ── [리팩토링 배치3 Phase2] 분류 판정 순수함수 층을 grm_taxonomy 로 분리(verbatim 이동).
#    재수출로 기존 참조 경로(collect_intake.compute_modality 등) 전부 보존. compute_signal_tier
#    는 SOURCE_* 의존으로 잔류하며 여기 재수출된 _kw_match/_kw_any 를 사용한다. ──
from grm_taxonomy import (
    FDA_WL_DRUG_ONLY_KEYWORDS,
    FDA_WL_LOW_VALUE_KEYWORDS,
    FDA_WL_OFFICE_CONTEXTUAL,
    FDA_WL_OFFICE_EXCLUDE,
    FDA_WL_OFFICE_KEEP,
    MODALITY_BIOLOGIC,
    MODALITY_BIOLOGIC_BRANDS,
    MODALITY_BIOLOGIC_TERMS,
    MODALITY_CHEMICAL,
    MODALITY_DRUG_PRODUCT_TERMS,
    MODALITY_KOREAN_FORM_TERMS,
    MODALITY_OTHER,
    MODALITY_PRODUCT_NAME_KEYS,
    MODALITY_VET_EXCLUDE_TERMS,
    OSD_SOLID_TERMS,
    QA_CATEGORY_KEYWORDS,
    QA_DEVICE_DRUG_GUARD,
    QA_DEVICE_EXCLUDE_TERMS,
    QA_EXCLUDE_KEYWORDS,
    QA_HARD_EXCLUDE_TERMS,
    QA_LIKELY_BOOST,
    QA_MIN_MATCH,
    _KOREAN_FORM_SUFFIX_RE,
    _as_lower_set,
    _fda_wl_office_gate,
    _is_low_value_fda_warning_letter,
    _kw_any,
    _kw_match,
    _phrase_any,
    compute_modality,
    compute_osd_relevance,
    compute_relevance,
)

# ── [배치5] Phase0 공용 relocate(SOURCE_*·truncate·chunk_text·_env_int·chunk상수) — grm_common 재수출(하위호환·테스트·위성 무수정) ──
from grm_common import (
    NOTION_RICH_TEXT_CHUNK,
    SOURCE_BRAVE,
    SOURCE_ECA,
    SOURCE_EMA,
    SOURCE_EPR,
    SOURCE_FDA_483,
    SOURCE_FDA_WL,
    SOURCE_FR,
    SOURCE_HANDOFF,
    SOURCE_HC,
    SOURCE_ICH,
    SOURCE_MFDS,
    SOURCE_MHRA,
    SOURCE_PICS,
    SOURCE_RAPS,
    SOURCE_RECALL,
    SOURCE_WHO,
    _env_int,
    chunk_text,
    truncate,
)

# ── [배치5] Phase1 Notion 클라이언트 층 분리 — grm_notion 재수출(하위호환·테스트·위성 무수정) ──
from grm_notion import (
    MODALITY_OPTIONS,
    NOTION_API_VERSION,
    NOTION_BLOCK_CHILDREN_URL_TPL,
    NOTION_CODE_BLOCK_CHUNK,
    NOTION_DB_QUERY_URL_TPL,
    NOTION_PAGES_URL,
    NOTION_PAGE_URL_TPL,
    NotionDedupeQueryError,
    NotionHandoffError,
    PROP_API_QUERY,
    PROP_BODY,
    PROP_COLLECTED_AT,
    PROP_COMMENTS_CLOSE,
    PROP_DATE,
    PROP_DISTRIBUTION,
    PROP_DOC_ID,
    PROP_EVIDENCE_CANDIDATE,
    PROP_FIRM,
    PROP_HANDOFF_REF,
    PROP_HEADLINE,
    PROP_LANGUAGE,
    PROP_MODALITY,
    PROP_NAME,
    PROP_OFFICIAL_URL,
    PROP_OSD_RELEVANCE,
    PROP_QA_RELEVANCE,
    PROP_RAW_EXCERPT,
    PROP_REGION_JURISDICTION,
    PROP_RUN_DATE,
    PROP_SEARCH_QUERY,
    PROP_SIGNAL_TIER,
    PROP_SITE_COUNTRY,
    PROP_SOURCE,
    PROP_SOURCE_TYPE,
    PROP_SOURCE_URL,
    PROP_STATUS,
    PROP_TYPE_CLASS,
    _date_iso,
    _datetime_iso,
    _rich_text,
    _select,
    _url,
    build_notion_children,
    build_notion_properties,
    notion_api_request,
    notion_create_page,
    notion_headers,
    notion_query_existing_doc_ids,
    notion_verify_handoff_ref_property,
    notion_verify_modality_property,
)


# ─────────────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────────────

KST = ZoneInfo("Asia/Seoul")

FR_API_BASE = "https://www.federalregister.gov/api/v1/documents.json"
OPENFDA_API_BASE = "https://api.fda.gov/drug/enforcement.json"

# FDA Recalls/Enforcement L2 (OpenFDA 는 항목별 사용자 친화 URL 이 없음)
FDA_RECALLS_L2 = "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts"

# Notion 속성 이름 (스키마 가이드와 1:1 대응 — 변경 시 양쪽 모두 수정)
PROP_SELF_CHECK = "Self-Check Required"  # retained in Notion, no longer written by collectors

# Phase 2a 신규 Notion 필드

# v15.1 Phase 2 — RSS / HTML 소스
TYPE_ROUTINE_HANDOFF = "routine-handoff"
HANDOFF_SCHEMA_VERSION = "grm-routine-handoff/v1"
HANDOFF_SCHEMA_VERSION_V2 = "grm-routine-handoff/v2"  # K2 단계 D (additive)
# PL-10b/B1 근본해결: row 가 어느 handoff 에 포함됐는지 결정론적 표시(rich_text).
# 값 = handoff_id("routine-handoff::YYYY-MM-DD") — page id 가 아니라 handoff_id 를 쓰는
# 이유: ① 같은 날 재-emit 시 page 탐색 없이 자격 판정 가능 ② 사람이 읽을 수 있음
# ③ handoff page 재생성(삭제 후 upsert)에도 참조가 살아남음. Notion 속성은 사람이
# 사전 생성(ENABLE_HANDOFF_IDEMPOTENCY_V2 preflight 가 부재 시 v1 폴백).

# ── Phase 2a: Search / Scrape 소스 ──────────────────────────────────────────

# Source Type 분류 값 (Notion Select 옵션과 1:1 대응)
SRC_TYPE_OFFICIAL_API     = "Official API"              # FR, OpenFDA Recall, EMA RSS
SRC_TYPE_OFFICIAL_PAGE    = "Official Regulatory Page"  # PIC/S, FDA WL
SRC_TYPE_OFFICIAL_BLOG    = "Official Regulator Blog"   # MHRA Inspectorate
SRC_TYPE_EXPERT_SECONDARY = "Expert Secondary"          # ECA Academy
SRC_TYPE_SEARCH_RESULT    = "Search Result"             # Phase 2a: Brave Search
SRC_TYPE_OFFICIAL_SCRAPE  = "Official Page Scrape"      # Phase 2a: scrape 수집

# 소스별 Source Type 매핑
SOURCE_TYPE_MAP: dict[str, str] = {
    # 기존 7개
    SOURCE_FR:      SRC_TYPE_OFFICIAL_API,
    SOURCE_RECALL:  SRC_TYPE_OFFICIAL_API,
    SOURCE_EMA:     SRC_TYPE_OFFICIAL_API,
    SOURCE_MHRA:    SRC_TYPE_OFFICIAL_BLOG,
    SOURCE_PICS:    SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_ECA:     SRC_TYPE_EXPERT_SECONDARY,
    SOURCE_FDA_WL:  SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_MFDS:    SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_ICH:     SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_WHO:     SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_HC:      SRC_TYPE_OFFICIAL_API,
    SOURCE_FDA_483: SRC_TYPE_OFFICIAL_PAGE,
    SOURCE_HANDOFF: SRC_TYPE_OFFICIAL_PAGE,
    # Phase 2a 신규
    SOURCE_BRAVE:   SRC_TYPE_SEARCH_RESULT,
    SOURCE_RAPS:    SRC_TYPE_EXPERT_SECONDARY,
    SOURCE_EPR:     SRC_TYPE_EXPERT_SECONDARY,
}

# RSS / HTML 엔드포인트 (v15.1 추가)
EMA_RSS_FEEDS: dict[str, str] = {
    # 올바른 EMA RSS URL 형식: https://www.ema.europa.eu/en/{feed}.xml
    # (https://www.ema.europa.eu/en/news-events/rss-feeds 에서 확인)
    "scientific-guidelines":  "https://www.ema.europa.eu/en/scientific-guidelines.xml",
    "inspections":            "https://www.ema.europa.eu/en/inspections.xml",
    "news":                   "https://www.ema.europa.eu/en/news.xml",
    "regulatory-guidelines":  "https://www.ema.europa.eu/en/regulatory-and-procedural-guideline.xml",
}
MHRA_RSS_URL = "https://mhrainspectorate.blog.gov.uk/feed/"   # Atom 형식
PICS_RSS_URL = "https://picscheme.org/rss/general_en.rss"
ECA_RSS_URL  = "https://app.gxp-services.net/eca_newsfeed.xml"
FDA_WL_URL   = (
    "https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations"
    "/compliance-actions-and-activities/warning-letters"
)
FDA_WL_SEARCH_API = (
    "https://api.fda.gov/other/warning_letters.json"   # 미공개 엔드포인트 — 폴백용
)

# WHY-1 #2: FDA Warning Letter 본문 위반 excerpt (flag 게이트 ENABLE_WL_BODY, 기본 off).
# 목록 메타(subject)만 잡던 것을 편지 본문의 위반 서술 구간까지 추출해 카드 "왜"를 살린다.
# in-window WL 은 부서 게이트 후 소수(주 한 줌) → fetch 부담 낮음. 실패는 graceful(키 미기록).
WL_BODY_MAX_CHARS = 1500
WL_BODY_FETCH_TIMEOUT = 20
# [WL 심층분석 fan-out 2026-07-01] 본문 전문(全文) 확보 (flag 게이트 ENABLE_WL_BODY_FULL,
# 기본 off). 기존 WL_BODY_MAX_CHARS(1500자) excerpt 는 카드 "왜"용 짧은 발췌를 그대로 유지하고,
# 이 상수는 카드 1건에 대한 별도 fan-out 심층분석(조항별 위반·구제조치·행정리스크 5섹션,
# docs/prompts/GRM_Prompt_DeepWL_v1.md)의 입력 컨텍스트용 전문을 담는다. 수집(Python·GitHub
# Actions) 단계 비용은 HTTP GET 1회뿐이라 상한을 넉넉히 잡아도 부담이 없다(LLM 비용은 fan-out
# 단계에서 카드별로 격리 — collect_intake 는 저장만 한다). 실패는 graceful(키 미기록).
WL_BODY_FULL_MAX_CHARS = 20000
# 표지/머리말 보일러플레이트를 건너뛰고 위반 서술부터 자르기 위한 영문 앵커. 대소문자 무시(re.I).
# 2-tier 선별(2026-06-18): 위반 서술을 직접 가리키는 1차 앵커가 본문에 있으면 그 가장 이른
# 위치를 쓰고, 없을 때만 일반 머리말 폴백 앵커로 내려간다. (종전엔 전 앵커 통합 최이른 위치라
# "this warning letter/violations/cgmp/adulterated" 같은 일반어가 요약 머리말에서 먼저 걸려
# excerpt 앞부분이 보일러플레이트로 시작하던 문제 교정 — CGMP/기록검토/Telehealth 본문 모두.)
_WL_BODY_ANCHORS_PRIMARY = (
    r"during\s+(?:our|an|the)\s+inspection",
    r"(?:significant|specific)\s+violations\s+were\s+observed\s+including",
    r"observed\s+(?:specific\s+)?violations\s+including",
    r"we\s+(?:found|observed)\s+that",
    r"fda\s+observed\s+that",
    r"fda\s+review\s+violations",
    r"specifically,",
)
_WL_BODY_ANCHORS_FALLBACK = (
    r"this\s+warning\s+letter",
    r"\bviolations?\b",
    r"current\s+good\s+manufacturing\s+practice",
    r"\bcgmp\b",
    r"\badulterated\b",
)
# 하위호환: 통합 앵커 집합도 노출(외부 참조 안전망). 추출 선별은 PRIMARY→FALLBACK 순.
_WL_BODY_ANCHORS = _WL_BODY_ANCHORS_PRIMARY + _WL_BODY_ANCHORS_FALLBACK

# Signal Tier 자동 분류 키워드 (lowercase, 단어 경계 매칭 — _kw_match 사용)
# Tier 3 = 최고 신호 (CGMP 강제조치 · nitrosamine · 핵심 ICH), Tier 2 = 일반 GMP/품질 신호
SIGNAL_TIER3_KEYWORDS = [
    "cgmp", "current good manufacturing practice",
    "warning letter", "consent decree", "import alert",
    "annex 1", "ich q12", "ich q13",
    "nitrosamine", "ndma", "ndea", "n-nitroso",
    # 무균·바이오 고신호 (제품군 확장)
    "sterility failure", "non-sterility", "lack of sterility assurance",
    "viral contamination",
]
SIGNAL_TIER2_KEYWORDS = [
    "gmp", "manufacturing practice", "data integrity", "alcoa",
    "process validation", "cleaning validation", "dissolution",
    "out of specification", "oos", "stability", "capa", "deviation",
    "sterile", "aseptic", "supplier qualification", "recall", "class ii",
    # 무균·바이오 GMP/품질 신호 (제품군 확장)
    "media fill", "container closure integrity", "ccit", "biosimilar",
    "comparability", "immunogenicity", "lyophilized", "bioburden",
    "cold chain", "visible particulate",
]

# 무균·바이오 '치명적' 단일 신호 — 1개만 출현해도 Tier 3 floor 적용 (제품군 확장)
# (SIGNAL_TIER3_KEYWORDS 는 2개 매칭을 요구하므로, 단독 출현 시 누락되는 문제 보완)
STERILE_BIO_TIER3_FLOOR = [
    "sterility failure", "non-sterility", "lack of sterility assurance",
    "viral contamination", "media fill failure",
]

# OSD (경구 고형제) Relevance 분류 기준 (v15.1 추가)
OSD_ROUTES = {"oral"}
OSD_FORMS = {
    "tablet", "capsule", "extended-release tablet", "er tablet",
    "delayed-release tablet", "chewable tablet", "orally disintegrating tablet",
    "powder for oral solution", "oral solution", "oral suspension",
}

# ── 제품군(Modality) 분류 (제품군 확장) ───────────────────────────────────────
# 원료(active) 성격을 기준으로 한 '큰 틀' 제품군 태그. 특정 제품이 아닌 클래스 단위.
#   Chemical  = 화학합성(케미컬)의약품   Biologic = 생물의약품   Other = 기타·판별 곤란
# OSD Relevance(경구 고형제 전용)를 제품군 단위로 일반화한 것. 발행 섹션 그룹핑에 사용.
# Notion 의 'Modality' select 속성에 기록(ENABLE_MODALITY_TAG=true 일 때).

FR_PER_PAGE = 100  # API 최대치
OPENFDA_LIMIT = 100  # no-key 한도, key 있어도 안전치
OPENFDA_MAX_TOTAL = 200  # 안전 상한 (의약품 리콜 주간 통상 < 50)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IntakeItem:
    source: str  # SOURCE_FR | SOURCE_RECALL | SOURCE_EMA | SOURCE_MHRA | SOURCE_PICS | SOURCE_ECA | SOURCE_FDA_WL
    document_id: str
    date_iso: str  # YYYY-MM-DD
    headline: str
    official_url: str
    type_or_class: str = ""
    firm: str = ""
    body: str = ""
    distribution: str = ""
    comments_close_iso: str = ""
    api_query: str = ""
    qa_relevance: str = "Pending"
    osd_relevance: str = "N/A"   # "Direct" | "Indirect" | "N/A" — Recall 전용, 기타 N/A
    source_type: str = SRC_TYPE_OFFICIAL_API  # Source Type 분류 (v15.1)
    signal_tier: str = "Tier 1"  # "Tier 1" | "Tier 2" | "Tier 3" — compute_signal_tier 자동 분류
    raw_payload: dict[str, Any] = field(default_factory=dict)
    # ── Phase 2a 신규 필드 ────────────────────────────────────────────────────
    # 모두 default="" — 기존 FR/Recall 등 코드는 건드리지 않아도 됨
    source_url: str = ""           # 실제 수집/발견 페이지 URL (Search/Scrape 전용)
    raw_excerpt: str = ""          # ≤200자 snippet/excerpt
    search_query: str = ""         # Brave Search 실행 쿼리 (Search 전용)
    evidence_candidate: str = ""   # "A"/"B"/"C"/"D" — 수집기 후보, Routine 최종 판정
    language: str = ""             # KO / EN 등. MFDS Phase 2b부터 사용
    region_jurisdiction: str = ""  # 예: Korea (MFDS)
    site_country: str = ""          # 제조소 소재국. Region/Jurisdiction은 관할기관으로 유지.


@dataclass
class CollectionStats:
    # ── Phase 1 (Official API) ───────────────────────────────────────────────
    fr_fetched: int = 0
    fr_inserted: int = 0
    fr_skipped_dup: int = 0
    fr_insert_failed: int = 0       # Notion 삽입 최종 실패 건수
    fr_truncated: bool = False      # FR pagination 안전 상한 초과 여부
    fr_error: bool = False
    fr_error_msg: str = ""
    recall_fetched: int = 0
    recall_inserted: int = 0
    recall_skipped_dup: int = 0
    recall_insert_failed: int = 0   # Notion 삽입 최종 실패 건수
    recall_truncated: bool = False  # OPENFDA_MAX_TOTAL 상한 초과 여부 (v15.1)
    recall_error: bool = False
    recall_error_msg: str = ""
    # ── Phase 2 (RSS / HTML) ────────────────────────────────────────────────
    ema_fetched: int = 0
    ema_inserted: int = 0
    ema_skipped_dup: int = 0
    ema_insert_failed: int = 0
    ema_error: bool = False
    ema_error_msg: str = ""
    mhra_fetched: int = 0
    mhra_inserted: int = 0
    mhra_skipped_dup: int = 0
    mhra_insert_failed: int = 0
    mhra_error: bool = False
    mhra_error_msg: str = ""
    pics_fetched: int = 0
    pics_inserted: int = 0
    pics_skipped_dup: int = 0
    pics_insert_failed: int = 0
    pics_error: bool = False
    pics_error_msg: str = ""
    eca_fetched: int = 0
    eca_inserted: int = 0
    eca_skipped_dup: int = 0
    eca_insert_failed: int = 0
    eca_error: bool = False
    eca_error_msg: str = ""
    wl_fetched: int = 0
    wl_inserted: int = 0
    wl_skipped_dup: int = 0
    wl_insert_failed: int = 0
    wl_error: bool = False
    wl_error_msg: str = ""
    # WHY-1 #2 P1: WL 본문 excerpt 관측 — 실패는 graceful(메타 카드 유지)이라 error 가
    # 아니며, _evaluate_health 가 warning 으로만 표면화한다(flag off 면 0 → 무발생).
    wl_body_attempted: int = 0
    wl_body_failed: int = 0
    # ── Phase 2a: Search ────────────────────────────────────────────────────
    search_fetched: int = 0
    search_inserted: int = 0
    search_skipped_dup: int = 0
    search_insert_failed: int = 0
    search_error: bool = False
    search_error_msg: str = ""
    # ── Phase 2b: MFDS ─────────────────────────────────────────────────────
    mfds_fetched: int = 0
    mfds_inserted: int = 0
    mfds_skipped_dup: int = 0
    mfds_insert_failed: int = 0
    mfds_error: bool = False
    mfds_error_msg: str = ""
    mfds_law_fetched: int = 0
    mfds_law_inserted: int = 0
    mfds_law_skipped_dup: int = 0
    mfds_law_insert_failed: int = 0
    mfds_law_error: bool = False
    mfds_law_error_msg: str = ""
    # ── Phase 2c: MFDS Recall / Self-Check ────────────────────────────────
    mfds_recall_fetched: int = 0
    mfds_recall_inserted: int = 0
    mfds_recall_skipped_dup: int = 0
    mfds_recall_insert_failed: int = 0
    mfds_recall_error: bool = False
    mfds_recall_error_msg: str = ""
    mfds_admin_fetched: int = 0
    mfds_admin_inserted: int = 0
    mfds_admin_skipped_dup: int = 0
    mfds_admin_insert_failed: int = 0
    mfds_admin_error: bool = False
    mfds_admin_error_msg: str = ""
    mfds_gmp_cert_fetched: int = 0
    mfds_gmp_cert_inserted: int = 0
    mfds_gmp_cert_skipped_dup: int = 0
    mfds_gmp_cert_insert_failed: int = 0
    mfds_gmp_cert_error: bool = False
    mfds_gmp_cert_error_msg: str = ""
    mfds_safety_letter_fetched: int = 0
    mfds_safety_letter_inserted: int = 0
    mfds_safety_letter_skipped_dup: int = 0
    mfds_safety_letter_insert_failed: int = 0
    mfds_safety_letter_error: bool = False
    mfds_safety_letter_error_msg: str = ""
    mfds_gmp_inspection_fetched: int = 0
    mfds_gmp_inspection_inserted: int = 0
    mfds_gmp_inspection_skipped_dup: int = 0
    mfds_gmp_inspection_insert_failed: int = 0
    mfds_gmp_inspection_error: bool = False
    mfds_gmp_inspection_error_msg: str = ""
    mfds_gmp_inspection_parse_status: dict[str, int] = field(default_factory=dict)
    mfds_gmp_inspection_deficiency: dict[str, int] = field(default_factory=dict)
    mfds_gmp_inspection_manual_review: int = 0
    mfds_gmp_inspection_page_warnings: list[str] = field(default_factory=list)
    # [상세보기 결정론 승격 2026-07-02] 지적 표 추출 관측 — collect_mfds_gmp_inspection.LAST_HEALTH
    # ["deficiency_table"] 집계분(WHOPIR excerpt health 동형). 실패/degrade 는 warning 표면화용.
    gmp_deficiency_table_enabled: bool = False
    gmp_deficiency_table_attempted: int = 0
    gmp_deficiency_table_extracted: int = 0
    gmp_deficiency_table_failed: int = 0
    gmp_deficiency_table_warnings: list[str] = field(default_factory=list)
    # ── P1: ICH (직접 모니터링) ────────────────────────────────────────────
    ich_fetched: int = 0
    ich_inserted: int = 0
    ich_skipped_dup: int = 0
    ich_insert_failed: int = 0
    ich_error: bool = False
    ich_error_msg: str = ""
    # ── P1: WHO Prequalification ───────────────────────────────────────────
    who_fetched: int = 0
    who_inserted: int = 0
    who_skipped_dup: int = 0
    who_insert_failed: int = 0
    who_error: bool = False
    who_error_msg: str = ""
    # WHY-1 #1 P1: WHOPIR excerpt 관측 — collect_who.LAST_HEALTH 집계분. 실패/cap 은
    # graceful(링크 카드 유지)이라 warning 으로만 표면화(flag off 면 0 → 무발생).
    whopir_excerpt_attempted: int = 0
    whopir_excerpt_failed: int = 0
    whopir_excerpt_capped: int = 0   # cap 도달 여부(0/1) — 이후 항목 excerpt 생략 신호
    # ── P1: Health Canada ──────────────────────────────────────────────────
    hc_fetched: int = 0
    hc_inserted: int = 0
    hc_skipped_dup: int = 0
    hc_insert_failed: int = 0
    hc_error: bool = False
    hc_error_msg: str = ""
    # ── WHY-1 #3: FDA 483 ──────────────────────────────────────────────────
    fda483_fetched: int = 0
    fda483_inserted: int = 0
    fda483_skipped_dup: int = 0
    fda483_insert_failed: int = 0
    fda483_error: bool = False
    fda483_error_msg: str = ""
    # P1: 483 excerpt 관측 — collect_fda_483.LAST_HEALTH 집계분. 실패/cap 은 graceful
    # (메타 카드 유지)이라 warning 으로만 표면화(flag off 면 0 → 무발생). source_degraded
    # 는 DataTables AJAX 실패 → 정적 HTML 폴백(부분·완전성 미보장) 신호.
    fda483_excerpt_attempted: int = 0
    fda483_excerpt_failed: int = 0
    fda483_excerpt_capped: int = 0    # cap 도달 여부(0/1)
    fda483_source_degraded: int = 0   # DataTables AJAX 실패→정적 HTML fallback 여부(0/1)
    # [FDA 483 상세보기 2026-07-02] Observation 구조 추출 관측 — 실패는 상세만 생략하고
    # 요약카드 유지. ENABLE_FDA_483_OBSERVATIONS=false 면 enabled=false·카운터 0.
    fda483_observations_enabled: bool = False
    fda483_observations_attempted: int = 0
    fda483_observations_extracted: int = 0
    fda483_observations_failed: int = 0
    fda483_observations_warnings: list[str] = field(default_factory=list)

    def total_insert_failures(self) -> int:
        return (
            self.fr_insert_failed + self.recall_insert_failed
            + self.ema_insert_failed + self.mhra_insert_failed
            + self.pics_insert_failed + self.eca_insert_failed
            + self.wl_insert_failed + self.search_insert_failed
            + self.mfds_insert_failed + self.mfds_law_insert_failed
            + self.mfds_recall_insert_failed
            + self.mfds_admin_insert_failed + self.mfds_gmp_cert_insert_failed
            + self.mfds_safety_letter_insert_failed
            + self.mfds_gmp_inspection_insert_failed
            + self.ich_insert_failed
            + self.who_insert_failed
            + self.hc_insert_failed
            + self.fda483_insert_failed
        )

    def has_insert_failures(self) -> bool:
        return self.total_insert_failures() > 0

    def has_source_errors(self) -> bool:
        """수집기 레벨 오류 여부 (insert 실패와 별개).
        ENABLE_SEARCH=true + BRAVE_API_KEY 누락 같은 misconfiguration도 포함.
        workflow issue 생성 / exit code 판정에 이 메서드를 사용할 것.
        """
        return (
            self.fr_error or self.recall_error or self.ema_error
            or self.mhra_error or self.pics_error or self.eca_error
            or self.wl_error
            or self.search_error   # Phase 2a 신규 — misconfiguration 포함
            or self.mfds_error
            or self.mfds_law_error
            or self.mfds_recall_error
            or self.mfds_admin_error
            or self.mfds_gmp_cert_error
            or self.mfds_safety_letter_error
            or self.mfds_gmp_inspection_error
            or self.ich_error
            or self.who_error
            or self.hc_error
            or self.fda483_error
        )

    def summary(self) -> str:
        fr_warn = " ⚠️ TRUNCATED" if self.fr_truncated else ""
        rec_warn = " ⚠️ TRUNCATED" if self.recall_truncated else ""
        lines = [
            f"FR   fetched={self.fr_fetched}  inserted={self.fr_inserted}  "
            f"skip_dup={self.fr_skipped_dup}  failed={self.fr_insert_failed}  "
            f"error={self.fr_error}{fr_warn}",
            f"REC  fetched={self.recall_fetched}  inserted={self.recall_inserted}  "
            f"skip_dup={self.recall_skipped_dup}  failed={self.recall_insert_failed}  "
            f"error={self.recall_error}{rec_warn}",
            f"EMA  fetched={self.ema_fetched}  inserted={self.ema_inserted}  "
            f"skip_dup={self.ema_skipped_dup}  failed={self.ema_insert_failed}  "
            f"error={self.ema_error}",
            f"MHRA fetched={self.mhra_fetched}  inserted={self.mhra_inserted}  "
            f"skip_dup={self.mhra_skipped_dup}  failed={self.mhra_insert_failed}  "
            f"error={self.mhra_error}",
            f"PICS fetched={self.pics_fetched}  inserted={self.pics_inserted}  "
            f"skip_dup={self.pics_skipped_dup}  failed={self.pics_insert_failed}  "
            f"error={self.pics_error}",
            f"ECA  fetched={self.eca_fetched}  inserted={self.eca_inserted}  "
            f"skip_dup={self.eca_skipped_dup}  failed={self.eca_insert_failed}  "
            f"error={self.eca_error}",
            f"WL   fetched={self.wl_fetched}  inserted={self.wl_inserted}  "
            f"skip_dup={self.wl_skipped_dup}  failed={self.wl_insert_failed}  "
            f"error={self.wl_error}",
            f"SRC  fetched={self.search_fetched}  inserted={self.search_inserted}  "
            f"skip_dup={self.search_skipped_dup}  failed={self.search_insert_failed}  "
            f"error={self.search_error}",
            f"MFDS fetched={self.mfds_fetched}  inserted={self.mfds_inserted}  "
            f"skip_dup={self.mfds_skipped_dup}  failed={self.mfds_insert_failed}  "
            f"error={self.mfds_error}",
            f"MFL  fetched={self.mfds_law_fetched}  inserted={self.mfds_law_inserted}  "
            f"skip_dup={self.mfds_law_skipped_dup}  failed={self.mfds_law_insert_failed}  "
            f"error={self.mfds_law_error}",
            f"MFR  fetched={self.mfds_recall_fetched}  inserted={self.mfds_recall_inserted}  "
            f"skip_dup={self.mfds_recall_skipped_dup}  failed={self.mfds_recall_insert_failed}  "
            f"error={self.mfds_recall_error}",
            f"MFA  fetched={self.mfds_admin_fetched}  inserted={self.mfds_admin_inserted}  "
            f"skip_dup={self.mfds_admin_skipped_dup}  failed={self.mfds_admin_insert_failed}  "
            f"error={self.mfds_admin_error}",
            f"MFC  fetched={self.mfds_gmp_cert_fetched}  inserted={self.mfds_gmp_cert_inserted}  "
            f"skip_dup={self.mfds_gmp_cert_skipped_dup}  failed={self.mfds_gmp_cert_insert_failed}  "
            f"error={self.mfds_gmp_cert_error}",
            f"MFS  fetched={self.mfds_safety_letter_fetched}  "
            f"inserted={self.mfds_safety_letter_inserted}  "
            f"skip_dup={self.mfds_safety_letter_skipped_dup}  "
            f"failed={self.mfds_safety_letter_insert_failed}  "
            f"error={self.mfds_safety_letter_error}",
            f"MFG  fetched={self.mfds_gmp_inspection_fetched}  "
            f"inserted={self.mfds_gmp_inspection_inserted}  "
            f"skip_dup={self.mfds_gmp_inspection_skipped_dup}  "
            f"failed={self.mfds_gmp_inspection_insert_failed}  "
            f"error={self.mfds_gmp_inspection_error}  "
            f"parse={self.mfds_gmp_inspection_parse_status}  "
            f"manual_review={self.mfds_gmp_inspection_manual_review}",
            f"ICH  fetched={self.ich_fetched}  inserted={self.ich_inserted}  "
            f"skip_dup={self.ich_skipped_dup}  failed={self.ich_insert_failed}  "
            f"error={self.ich_error}",
            f"WHO  fetched={self.who_fetched}  inserted={self.who_inserted}  "
            f"skip_dup={self.who_skipped_dup}  failed={self.who_insert_failed}  "
            f"error={self.who_error}",
            f"HC   fetched={self.hc_fetched}  inserted={self.hc_inserted}  "
            f"skip_dup={self.hc_skipped_dup}  failed={self.hc_insert_failed}  "
            f"error={self.hc_error}",
            f"483  fetched={self.fda483_fetched}  inserted={self.fda483_inserted}  "
            f"skip_dup={self.fda483_skipped_dup}  failed={self.fda483_insert_failed}  "
            f"error={self.fda483_error}",
        ]
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────────────


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def kst_run_date(now: datetime | None = None) -> date:
    """KST 기준 '오늘' 의 자정 날짜."""
    return (now or now_kst()).astimezone(KST).date()


def date_window(run_date: date, window_days: int = 7) -> tuple[date, date]:
    start = run_date - timedelta(days=window_days)
    return start, run_date


# ─────────────────────────────────────────────────────────────────────────────
# RSS / Atom / HTML 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

# XML 네임스페이스
_NS_ATOM  = "http://www.w3.org/2005/Atom"
_NS_DC    = "http://purl.org/dc/elements/1.1/"
_NS_DCTERMS = "http://purl.org/dc/terms/"
_NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"


def _rss_text(el: ET.Element | None) -> str:
    """ElementTree 텍스트 안전 추출."""
    if el is None:
        return ""
    return (el.text or "").strip()


def _rss_find(parent: ET.Element, *tags: str) -> ET.Element | None:
    """네임스페이스 없는 태그 및 네임스페이스 조합 순차 탐색."""
    for tag in tags:
        el = parent.find(tag)
        if el is not None:
            return el
    return None


def _parse_rss2_date(raw: str) -> str:
    """RFC 2822 (RSS 2.0) 날짜 → YYYY-MM-DD. 실패 시 "" 반환."""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.date().isoformat()
    except Exception:
        pass
    # 일부 피드가 ISO 8601 을 쓰는 경우 폴백
    return _parse_atom_date(raw)


def _parse_atom_date(raw: str) -> str:
    """Atom/ISO 8601 날짜 → YYYY-MM-DD. 실패 시 "" 반환."""
    if not raw:
        return ""
    # 공통 패턴: 2024-03-15T12:00:00Z / 2024-03-15T12:00:00+00:00 / 2024-03-15
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:25], fmt).date().isoformat()
        except ValueError:
            continue
    # fromisoformat (Python 3.11+) 폴백
    try:
        return datetime.fromisoformat(raw[:25].rstrip("Z")).date().isoformat()
    except ValueError:
        pass
    log("WARN", f"날짜 파싱 실패 (atom): {raw!r}")
    return ""


def _stable_doc_id(source: str, title: str, url: str, date_iso: str) -> str:
    """RSS 항목에 고유 document_id 가 없을 경우 콘텐츠 기반 안정 ID 생성.

    SHA-1 상위 12자리 사용 — URL+제목+날짜 조합이므로 동일 항목은 동일 ID 를 가진다.
    dedupe 목적에 충분한 고유성을 제공한다.
    """
    key = f"{source}|{url}|{title}|{date_iso}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _within_window(date_iso: str, start: date, end: date) -> bool:
    """date_iso (YYYY-MM-DD) 가 [start, end] 구간에 포함 여부."""
    if not date_iso:
        return False
    try:
        d = date.fromisoformat(date_iso)
        return start <= d <= end
    except ValueError:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# RSS 수집 공통 파서
# ─────────────────────────────────────────────────────────────────────────────


def _rss2_items_from_root(root: ET.Element) -> list[ET.Element]:
    """RSS 2.0 channel → item 리스트. RDF(rss/1.0) 도 처리."""
    channel = root.find("channel")
    if channel is not None:
        return channel.findall("item")
    return root.findall("item")


def _atom_entries_from_root(root: ET.Element) -> list[ET.Element]:
    """Atom feed → entry 리스트. 네임스페이스 있는 경우 처리."""
    entries = root.findall(f"{{{_NS_ATOM}}}entry")
    if entries:
        return entries
    return root.findall("entry")


def _atom_text(entry: ET.Element, tag: str) -> str:
    """Atom 네임스페이스 포함 텍스트 추출 (네임스페이스 있는 버전, 없는 버전 모두)."""
    el = _rss_find(entry, f"{{{_NS_ATOM}}}{tag}", tag)
    if el is None:
        return ""
    # <content> / <summary> 등의 type="html" 처리
    text = (el.text or "").strip()
    # HTML 태그 간단 제거
    return re.sub(r"<[^>]+>", " ", text).strip()


def _atom_link(entry: ET.Element) -> str:
    """Atom <link href="..."> 추출."""
    # href 속성을 가진 link 먼저
    for link in entry.findall(f"{{{_NS_ATOM}}}link"):
        href = link.get("href", "")
        if href and link.get("rel", "alternate") == "alternate":
            return href
    for link in entry.findall(f"{{{_NS_ATOM}}}link"):
        href = link.get("href", "")
        if href:
            return href
    # 네임스페이스 없는 버전
    el = entry.find("link")
    return _rss_text(el)


# ─────────────────────────────────────────────────────────────────────────────
# QA Relevance 휴리스틱
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Federal Register 수집
# ─────────────────────────────────────────────────────────────────────────────
# OSD Relevance 분류 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 제품군(Modality) 분류 (제품군 확장)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Signal Tier 자동 분류 (v15.x Phase 1)
# ─────────────────────────────────────────────────────────────────────────────


def compute_signal_tier(source: str, type_or_class: str, qa_relevance: str,
                        osd_relevance: str, *text_parts: str) -> str:
    """수집 항목의 Signal Tier (Tier 1/2/3) 1차 자동 분류.

    최종 판정은 Routine 에 위임하되, 고신호 항목을 우선 노출하기 위한 휴리스틱.
        Tier 3 — 즉시 검토 가치가 높은 강제조치/핵심 GMP 신호
        Tier 2 — GMP/품질 관련 신호
        Tier 1 — 기본 (기타)

    인자:
        source         : SOURCE_* 상수
        type_or_class  : Recall classification("Class I"…) / FR type("Rule"…) / 카테고리
        qa_relevance   : compute_relevance 결과 ("Likely" 등)
        osd_relevance  : compute_osd_relevance 결과 ("Direct" 등)
        text_parts     : 키워드 매칭 대상 텍스트 (제목·본문·카테고리 등)
    """
    blob = " ".join(t for t in text_parts if t).lower()
    type_lc = (type_or_class or "").lower()

    # Recall classification — 단어 경계로 Class I / II / III 정확히 구분
    is_class_i = source == SOURCE_RECALL and re.search(r"\bclass i\b", type_lc) is not None
    is_class_ii = source == SOURCE_RECALL and re.search(r"\bclass ii\b", type_lc) is not None
    is_fr_rule = source == SOURCE_FR and "rule" in type_lc

    t3_matches = _kw_match(blob, SIGNAL_TIER3_KEYWORDS)
    t2_matches = _kw_match(blob, SIGNAL_TIER2_KEYWORDS)

    # ── Tier 3 ─────────────────────────────────────────────────────────────
    if source == SOURCE_FDA_WL and _kw_any(
            blob, ["cgmp", "current good manufacturing practice"]):
        return "Tier 3"
    if is_class_i:
        return "Tier 3"
    # 제외 도메인(QA Unrelated: 의료기기·식품·화장품·수의 등)은 위 강제 예외(Class I·FDA WL cGMP)
    # 외에는 키워드로 Tier 2/3 승격하지 않고 Tier 1 로 고정(handoff·통계 노이즈 방지).
    if qa_relevance == "Unrelated":
        return "Tier 1"
    if osd_relevance == "Direct" and _kw_any(
            blob, ["dissolution", "nitrosamine", "subpotent"]):
        return "Tier 3"
    # 무균·바이오 치명적 단일 신호는 1개만 있어도 Tier 3 (floor)
    # 단, QA 관련성이 Unrelated(의료기기·식품 등 제외 도메인)인 항목은 승격하지 않는다.
    if qa_relevance != "Unrelated" and _kw_any(blob, STERILE_BIO_TIER3_FLOOR):
        return "Tier 3"
    if t3_matches >= 2:
        return "Tier 3"
    if is_fr_rule and t3_matches >= 1:
        return "Tier 3"

    # ── Tier 2 ─────────────────────────────────────────────────────────────
    if qa_relevance == "Likely":
        return "Tier 2"
    if is_class_ii:
        return "Tier 2"
    if t2_matches >= 1:
        return "Tier 2"
    if osd_relevance == "Direct":
        return "Tier 2"

    return "Tier 1"


# ─────────────────────────────────────────────────────────────────────────────
# 날짜 유틸
# ─────────────────────────────────────────────────────────────────────────────


def _safe_date_iso(value: str, context: str = "") -> str:
    """날짜 문자열 검증 후 YYYY-MM-DD 반환. 실패 시 "" 반환 + WARN 로그.

    지원 포맷:
        - YYYY-MM-DD (ISO 8601)
        - YYYYMMDD   (OpenFDA report_date 형식) → 자동 변환
    """
    if not value:
        return ""
    # YYYYMMDD → YYYY-MM-DD
    if len(value) == 8 and value.isdigit():
        value = f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    try:
        datetime.fromisoformat(value)
        return value
    except ValueError:
        log("WARN", f"날짜 파싱 실패 (context={context}): {value!r} → 빈 문자열 처리")
        return ""


# ─────────────────────────────────────────────────────────────────────────────


def collect_federal_register(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Federal Register 문서 지난 7 일 전수 수집 (pagination 처리)."""
    params: dict[str, Any] = {
        "conditions[agencies][]": "food-and-drug-administration",
        "conditions[publication_date][gte]": start.isoformat(),
        "conditions[publication_date][lte]": end.isoformat(),
        "per_page": FR_PER_PAGE,
        "order": "newest",
    }
    api_query_url = FR_API_BASE + "?" + urllib.parse.urlencode(params, doseq=True)
    log("INFO", f"FR API 호출: {api_query_url}")

    items: list[IntakeItem] = []
    next_url: str | None = api_query_url
    page_count = 0
    try:
        while next_url:
            page_count += 1
            if page_count > 10:
                msg = (f"FR pagination 10페이지 상한 초과 — truncated "
                       f"(수집 {len(items)}건, 이후 항목 누락 가능)")
                log("WARN", msg)
                return items, msg   # fr_error=True 로 집계됨
            data = http_get_json(next_url)
            results = data.get("results", []) or []
            log("INFO", f"FR page {page_count}: {len(results)} 건")
            for r in results:
                items.append(_fr_to_item(r, api_query_url))
            next_url = data.get("next_page_url")
        return items, None
    except Exception as e:
        log("ERROR", f"FR 수집 실패: {e}")
        return items, str(e)


def _fr_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    doc_id = str(r.get("document_number") or "").strip()
    title = (r.get("title") or "").strip()
    pub = _safe_date_iso((r.get("publication_date") or "").strip(),
                         context=f"FR/{doc_id}")
    html_url = (r.get("html_url") or "").strip()
    doc_type = (r.get("type") or "").strip()
    abstract = (r.get("abstract") or "").strip()
    comments_close = _safe_date_iso((r.get("comments_close_on") or "").strip(),
                                    context=f"FR/{doc_id}/comments_close")

    relevance = compute_relevance(title, abstract, doc_type)
    tier = compute_signal_tier(SOURCE_FR, doc_type, relevance, "N/A",
                               title, abstract, doc_type)

    return IntakeItem(
        source=SOURCE_FR,
        document_id=doc_id,
        date_iso=pub,
        headline=title,
        official_url=html_url,
        type_or_class=doc_type,
        body=abstract,
        comments_close_iso=comments_close,
        api_query=api_query_url,
        qa_relevance=relevance,
        # FR 항목은 OSD Relevance 분류 대상 아님
        osd_relevance="N/A",
        signal_tier=tier,
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OpenFDA Recall 수집
# ─────────────────────────────────────────────────────────────────────────────


def collect_openfda_recalls(start: date, end: date,
                            api_key: str | None) -> tuple[list[IntakeItem], str | None]:
    """OpenFDA Drug Enforcement 항목 지난 7 일 전수 수집."""
    search = f"report_date:[{start.strftime('%Y%m%d')}+TO+{end.strftime('%Y%m%d')}]"
    items: list[IntakeItem] = []
    skip = 0
    api_query_url_for_log: str | None = None
    try:
        while True:
            params = [
                ("search", search),
                ("limit", str(OPENFDA_LIMIT)),
                ("skip", str(skip)),
            ]
            if api_key:
                params.append(("api_key", api_key))
            url = OPENFDA_API_BASE + "?" + urllib.parse.urlencode(params, safe=":[]+")
            if api_query_url_for_log is None:
                # api_key 는 로그·Notion 저장에서 마스킹
                api_query_url_for_log = _mask_api_key(url)
                log("INFO", f"OpenFDA 호출: {api_query_url_for_log}")
            try:
                data = http_get_json(url)
            except HTTPClientError as e:
                # OpenFDA 는 해당 기간 결과 0건일 때 404 를 반환하는 것이 관행
                if e.status_code == 404:
                    log("INFO", "OpenFDA 404 (해당 기간 결과 0건) — 정상 종료")
                    return items, None
                # 404 외 4xx (401, 403 등) 는 실제 에러로 처리
                raise RuntimeError(str(e)) from e
            results = data.get("results", []) or []
            meta_total = (data.get("meta", {}).get("results", {}) or {}).get("total", 0)
            log("INFO", f"OpenFDA skip={skip}: {len(results)} 건 (total {meta_total})")
            for r in results:
                items.append(_recall_to_item(r, api_query_url_for_log or url))
            skip += len(results)
            if not results or skip >= meta_total:
                break
            if skip >= OPENFDA_MAX_TOTAL:
                msg = (f"OpenFDA OPENFDA_MAX_TOTAL({OPENFDA_MAX_TOTAL}) 상한 초과 — truncated "
                       f"(수집 {len(items)}건, meta.total={meta_total}, 이후 항목 누락 가능)")
                log("WARN", msg)
                return items, msg   # recall_truncated=True 로 집계됨
        return items, None
    except Exception as e:
        log("ERROR", f"OpenFDA 수집 실패: {e}")
        return items, str(e)


def _mask_api_key(url: str) -> str:
    return re.sub(r"(api_key=)[^&]+", r"\1***REDACTED***", url)


def _recall_to_item(r: dict[str, Any], api_query_url: str) -> IntakeItem:
    recall_number = str(r.get("recall_number") or "").strip()
    classification = (r.get("classification") or "").strip()
    product = (r.get("product_description") or "").strip()
    reason = (r.get("reason_for_recall") or "").strip()
    firm = (r.get("recalling_firm") or "").strip()
    distribution = (r.get("distribution_pattern") or "").strip()
    report_date_raw = (r.get("report_date") or "").strip()  # YYYYMMDD
    product_type = (r.get("product_type") or "").strip()

    # report_date YYYYMMDD → YYYY-MM-DD (_safe_date_iso 가 변환 + 검증 처리)
    date_iso = _safe_date_iso(report_date_raw, context=f"Recall/{recall_number}")

    headline = product or firm or recall_number
    relevance = compute_relevance(product, reason, firm, distribution, product_type)
    osd_rel = compute_osd_relevance(r)
    tier = compute_signal_tier(SOURCE_RECALL, classification, relevance, osd_rel,
                               product, reason, firm, distribution, product_type)

    return IntakeItem(
        source=SOURCE_RECALL,
        document_id=recall_number,
        date_iso=date_iso,
        headline=headline,
        official_url=FDA_RECALLS_L2,  # OpenFDA 는 항목별 URL 부재 — L2 고정
        type_or_class=classification,
        firm=firm,
        body=reason,
        distribution=distribution,
        api_query=api_query_url,
        qa_relevance=relevance,
        osd_relevance=osd_rel,
        signal_tier=tier,
        raw_payload=r,
    )


# ─────────────────────────────────────────────────────────────────────────────
# EMA RSS 수집 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def collect_ema_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """EMA 공식 RSS 피드 4개 (scientific-guidelines · inspections · news ·
    regulatory-guidelines) 를 수집해 날짜 필터링 후 반환.

    Source Type: Official API (EMA 공식 RSS 피드).
    Evidence Level: A 불가 — RSS 요약이므로 B (Official direct identified) 이상.
    """
    items: list[IntakeItem] = []
    errors: list[str] = []

    for feed_name, feed_url in EMA_RSS_FEEDS.items():
        log("INFO", f"EMA RSS 수집: {feed_name} ({feed_url})")
        try:
            root = http_get_xml(feed_url)
        except Exception as e:
            msg = f"EMA RSS '{feed_name}' 실패: {e}"
            log("WARN", msg)
            errors.append(msg)
            continue

        # RSS 2.0 형식 확인
        rss_items = _rss2_items_from_root(root)
        for el in rss_items:
            title = _rss_text(el.find("title"))
            link  = _rss_text(el.find("link"))
            # <link> 가 CDATA 로 감싸진 경우 텍스트에 없고 tail 에 있을 수 있음
            if not link:
                link_el = el.find("link")
                if link_el is not None:
                    link = (link_el.tail or "").strip()
            pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
            # EMA 피드에 따라 dc:date 를 fallback 으로 사용
            if not pub_raw:
                dc_date = _rss_find(el, f"{{{_NS_DC}}}date", f"{{{_NS_DCTERMS}}}modified")
                pub_raw = _rss_text(dc_date)
            date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
            # dc:date 가 Atom 형식인 경우 재시도
            if not date_iso and pub_raw:
                date_iso = _parse_atom_date(pub_raw)

            description = _rss_text(el.find("description"))
            category_el = el.find("category")
            category = _rss_text(category_el)
            guid_el = el.find("guid")
            guid = _rss_text(guid_el) or link

            if not _within_window(date_iso, start, end):
                continue

            doc_id = _stable_doc_id(SOURCE_EMA, title, link, date_iso)
            relevance = compute_relevance(title, description, category)
            tier = compute_signal_tier(SOURCE_EMA, category or feed_name, relevance,
                                       "N/A", title, description, category)

            items.append(IntakeItem(
                source=SOURCE_EMA,
                document_id=doc_id,
                date_iso=date_iso,
                headline=title,
                official_url=link,
                type_or_class=category or feed_name,
                body=description,
                api_query=feed_url,
                qa_relevance=relevance,
                osd_relevance="N/A",
                source_type=SRC_TYPE_OFFICIAL_API,
                signal_tier=tier,
                raw_payload={
                    "feed": feed_name,
                    "title": title,
                    "link": link,
                    "pubDate": pub_raw,
                    "description": description,
                    "category": category,
                    "guid": guid,
                },
            ))

    err_msg = "; ".join(errors) if errors else None
    # 오류가 있어도 다른 피드에서 수집한 항목은 반환 (graceful degradation)
    log("INFO", f"EMA RSS 수집 완료: {len(items)}건 (errors={len(errors)})")
    return items, err_msg if errors else None


# ─────────────────────────────────────────────────────────────────────────────
# MHRA Inspectorate RSS 수집 (v15.1) — Atom 형식
# ─────────────────────────────────────────────────────────────────────────────


def collect_mhra_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """MHRA Inspectorate Blog RSS (Atom 형식) 수집.

    Source Type: Official Regulator Blog.
    URL: https://mhrainspectorate.blog.gov.uk/feed/
    """
    log("INFO", f"MHRA RSS 수집: {MHRA_RSS_URL}")
    try:
        root = http_get_xml(MHRA_RSS_URL)
    except Exception as e:
        log("WARN", f"MHRA RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    entries = _atom_entries_from_root(root)

    for entry in entries:
        title   = _atom_text(entry, "title")
        link    = _atom_link(entry)
        # Atom: <updated> 또는 <published>
        pub_raw = (
            _rss_text(entry.find(f"{{{_NS_ATOM}}}published"))
            or _rss_text(entry.find(f"{{{_NS_ATOM}}}updated"))
            or _rss_text(entry.find("published"))
            or _rss_text(entry.find("updated"))
        )
        date_iso = _parse_atom_date(pub_raw)
        summary  = _atom_text(entry, "summary") or _atom_text(entry, "content")

        # category
        cat_el = _rss_find(entry, f"{{{_NS_ATOM}}}category", "category")
        category = (cat_el.get("term", "") if cat_el is not None else "").strip()

        # Atom id 를 document_id 로 사용
        id_el = _rss_find(entry, f"{{{_NS_ATOM}}}id", "id")
        guid  = _rss_text(id_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_MHRA, title, link, date_iso)
        relevance = compute_relevance(title, summary, category)
        tier      = compute_signal_tier(SOURCE_MHRA, category or "Blog", relevance,
                                        "N/A", title, summary, category)

        items.append(IntakeItem(
            source=SOURCE_MHRA,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class=category or "Blog",
            body=summary,
            api_query=MHRA_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_BLOG,
            signal_tier=tier,
            raw_payload={
                "title": title, "link": link,
                "published": pub_raw, "summary": summary,
                "category": category, "id": guid,
            },
        ))

    log("INFO", f"MHRA RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# PIC/S RSS 수집 (v15.1)
# ─────────────────────────────────────────────────────────────────────────────


def collect_pics_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """PIC/S 공식 RSS 수집.

    Source Type: Official Regulatory Page.
    URL: https://picscheme.org/rss/general_en.rss
    """
    log("INFO", f"PIC/S RSS 수집: {PICS_RSS_URL}")
    try:
        root = http_get_xml(PICS_RSS_URL)
    except Exception as e:
        log("WARN", f"PIC/S RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    rss_items = _rss2_items_from_root(root)

    for el in rss_items:
        title   = _rss_text(el.find("title"))
        link    = _rss_text(el.find("link"))
        pub_raw = _rss_text(el.find("pubDate")) or _rss_text(el.find("pubdate"))
        date_iso = _parse_rss2_date(pub_raw) if pub_raw else ""
        description = _rss_text(el.find("description"))
        guid_el = el.find("guid")
        guid    = _rss_text(guid_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_PICS, title, link, date_iso)
        relevance = compute_relevance(title, description)
        tier      = compute_signal_tier(SOURCE_PICS, "PIC/S", relevance,
                                        "N/A", title, description)

        items.append(IntakeItem(
            source=SOURCE_PICS,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class="PIC/S",
            body=description,
            api_query=PICS_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier=tier,
            raw_payload={
                "title": title, "link": link,
                "pubDate": pub_raw, "description": description, "guid": guid,
            },
        ))

    log("INFO", f"PIC/S RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# ECA Academy RSS 수집 (v15.1) — Expert Secondary
# ─────────────────────────────────────────────────────────────────────────────


def collect_eca_rss(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """ECA Academy (gmp-compliance.org) RSS 수집.

    Source Type: Expert Secondary — FDA·EMA·MHRA·TGA·PIC/S·ICH 전문 GMP 뉴스 큐레이션.
    URL: https://app.gxp-services.net/eca_newsfeed.xml
    403 발생 시 운영 경고 없이 진행 (Expert Secondary 허용 정책).
    """
    log("INFO", f"ECA RSS 수집: {ECA_RSS_URL}")
    try:
        root = http_get_xml(ECA_RSS_URL)
    except HTTPClientError as e:
        # Expert Secondary: 403/404 는 경고 없이 넘어감
        log("INFO", f"ECA RSS HTTP {e.status_code} — 건너뜀 (Expert Secondary 정책)")
        return [], None
    except Exception as e:
        log("WARN", f"ECA RSS 실패: {e}")
        return [], str(e)

    items: list[IntakeItem] = []
    # ECA 피드는 RSS 2.0 또는 Atom 모두 가능 — 두 방향 시도
    rss_items = _rss2_items_from_root(root)
    if not rss_items:
        rss_items = _atom_entries_from_root(root)  # type: ignore[assignment]

    for el in rss_items:
        # RSS 2.0 태그 우선, Atom 폴백
        title = (
            _rss_text(el.find("title"))
            or _atom_text(el, "title")
        )
        link = (
            _rss_text(el.find("link"))
            or _atom_link(el)
        )
        pub_raw = (
            _rss_text(el.find("pubDate"))
            or _rss_text(el.find("pubdate"))
            or _rss_text(el.find(f"{{{_NS_ATOM}}}published"))
            or _rss_text(el.find("published"))
        )
        date_iso = (
            _parse_rss2_date(pub_raw) if pub_raw
            else _parse_atom_date(pub_raw)
        )
        description = (
            _rss_text(el.find("description"))
            or _atom_text(el, "summary")
        )
        guid_el = el.find("guid")
        guid    = _rss_text(guid_el) or link

        if not _within_window(date_iso, start, end):
            continue

        doc_id    = _stable_doc_id(SOURCE_ECA, title, link, date_iso)
        relevance = compute_relevance(title, description)
        tier      = compute_signal_tier(SOURCE_ECA, "GMP News", relevance,
                                        "N/A", title, description)

        items.append(IntakeItem(
            source=SOURCE_ECA,
            document_id=doc_id,
            date_iso=date_iso,
            headline=title,
            official_url=link,
            type_or_class="GMP News",
            body=description,
            api_query=ECA_RSS_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_EXPERT_SECONDARY,
            signal_tier=tier,
            raw_payload={
                "title": title, "link": link,
                "pubDate": pub_raw, "description": description, "guid": guid,
            },
        ))

    log("INFO", f"ECA RSS 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# FDA Warning Letters 수집 (v15.1) — HTML 파싱
# ─────────────────────────────────────────────────────────────────────────────


class _FDAWLTableParser(HTMLParser):
    """FDA Warning Letters 페이지 HTML 에서 테이블 행을 파싱.

    대상 테이블은 class="table" 이며 열 순서:
      Posted Date | Recipient | Letter Issue Date | Issuing Office | Subject (or close match)
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_table: bool = False
        self._in_row: bool = False
        self._in_cell: bool = False
        self._cell_depth: int = 0
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._current_href: str = ""
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)
        if tag == "table" and "table" in (attr_dict.get("class") or ""):
            self._in_table = True
        if not self._in_table:
            return
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        if tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_depth = 1
            self._current_cell = []
            self._current_href = ""
        elif tag == "a" and self._in_cell:
            href = attr_dict.get("href") or ""
            if href and not self._current_href:
                self._current_href = href
        elif tag in ("td", "th") and self._in_cell:
            self._cell_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in ("td", "th") and self._in_cell:
            self._cell_depth -= 1
            if self._cell_depth <= 0:
                cell_text = " ".join(self._current_cell).strip()
                # href 와 함께 저장
                self._current_row.append(
                    f"{cell_text}|HREF:{self._current_href}" if self._current_href else cell_text
                )
                self._in_cell = False
        if tag == "tr" and self._in_row:
            self._in_row = False
            if len(self._current_row) >= 4:
                self.rows.append({"_cols": self._current_row})
        if tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell.append(stripped)


def _parse_wl_date(raw: str) -> str:
    """FDA WL 날짜 형식 (MM/DD/YYYY 또는 YYYY-MM-DD) → YYYY-MM-DD."""
    raw = raw.strip()
    if re.match(r"^\d{2}/\d{2}/\d{4}$", raw):
        try:
            return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
        except ValueError:
            pass
    return _safe_date_iso(raw, context="FDA_WL")


def _wl_html_to_text(html_text: str) -> str:
    """WL 편지 HTML → 평탄화 텍스트. script/style 제거 + 태그 제거 + 엔티티 복원 + 공백 정규화.

    PyMuPDF 불요(편지 본문은 HTML). 결정론·무의존(re + stdlib unescape).
    """
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = _html_unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _earliest_anchor(text: str, patterns: tuple[str, ...]) -> int | None:
    """patterns 중 text 에서 가장 이른 매치 시작 위치. 없으면 None (re.I)."""
    best: int | None = None
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def _extract_wl_body_span(html_text: str, max_chars: int) -> str:
    """WL 본문 위반 서술 구간 추출 공통 코어(앵커 탐색 + max_chars 절단).

    1차 앵커(위반 서술 직접 지시)가 있으면 그 가장 이른 위치, 없으면 폴백 앵커(일반 머리말),
    둘 다 없으면 ""(키 미기록·목록 메타 카드 유지). `_extract_wl_body_excerpt`(1500자, 기존
    W1 카드 "왜"용)와 `_extract_wl_body_full`(전문, 심층분석 fan-out 입력용)이 이 코어를
    공유한다 — 앵커 탐색 로직 중복 방지, `max_chars` 만 다르다(기존 호출부 동작 불변).

    표지/머리말 보일러플레이트가 아니라 위반 서술을 카드 컨텍스트("왜")로 올리기 위한 추출.
    FDA 페이지는 nav/푸터가 많아 앵커 미발견 시 앞부분 폴백을 하지 않고 메타 카드를 유지한다.
    """
    text = _wl_html_to_text(html_text)
    if not text:
        return ""
    start = _earliest_anchor(text, _WL_BODY_ANCHORS_PRIMARY)
    if start is None:
        start = _earliest_anchor(text, _WL_BODY_ANCHORS_FALLBACK)
    if start is None:
        return ""
    return text[start:start + max_chars].strip()


def _extract_wl_body_excerpt(html_text: str) -> str:
    """WL 본문 위반 서술 구간 excerpt(1500자) — `_extract_wl_body_span` 위임(동작 불변)."""
    return _extract_wl_body_span(html_text, WL_BODY_MAX_CHARS)


def _extract_wl_body_full(html_text: str) -> str:
    """[WL 심층분석 fan-out] WL 본문 전문(全文, 최대 WL_BODY_FULL_MAX_CHARS) — 신규·additive.

    excerpt 와 동일 앵커(위반 서술 시작점)에서 시작해 훨씬 더 긴 구간을 담는다 — 위반 서술
    이후 이어지는 구제조치 기한·행정 리스크 문단까지 포함하도록(편지 뒷부분). 카드별 fan-out
    심층분석(docs/prompts/GRM_Prompt_DeepWL_v1.md)의 유일한 입력 컨텍스트가 된다.
    """
    return _extract_wl_body_span(html_text, WL_BODY_FULL_MAX_CHARS)


# WHY-1 #2 P1: WL 본문 excerpt 관측용 — collect_who.LAST_HEALTH 동형 패턴.
# collect_fda_warning_letters 가 매 호출 갱신하고 오케스트레이터가 stats 로 옮긴다.
LAST_WL_HEALTH: dict[str, Any] = {}


def _fetch_wl_body_excerpt(url: str) -> str:
    """WL 편지 페이지 fetch → 위반 excerpt. 실패(403/timeout/네트워크)는 graceful("")."""
    try:
        resp = requests.get(url, timeout=WL_BODY_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", f"FDA WL 본문 403 — excerpt 건너뜀(메타 카드 유지): {truncate(url, 80)}")
            return ""
        resp.raise_for_status()
    except requests.RequestException as e:
        log("WARN", f"FDA WL 본문 fetch 실패(메타 카드 유지): {truncate(str(e), 120)}")
        return ""
    excerpt = _extract_wl_body_excerpt(resp.text)
    if not excerpt:
        log("INFO", f"FDA WL 본문 위반 앵커 미발견 — 메타 카드 유지: {truncate(url, 80)}")
    return excerpt


def _fetch_wl_body_full(url: str) -> str:
    """[WL 심층분석 fan-out] WL 편지 페이지 fetch → 본문 전문. 실패는 graceful("").

    `_fetch_wl_body_excerpt` 와 별도 GET(단순성·격리 우선 — in-window WL 은 주 소수라
    중복 fetch 비용 무시 가능). 두 플래그가 모두 켜져도 서로 독립적으로 동작한다.
    """
    try:
        resp = requests.get(url, timeout=WL_BODY_FETCH_TIMEOUT, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", f"FDA WL 본문(전문) 403 — 건너뜀(메타 카드 유지): {truncate(url, 80)}")
            return ""
        resp.raise_for_status()
    except requests.RequestException as e:
        log("WARN", f"FDA WL 본문(전문) fetch 실패(메타 카드 유지): {truncate(str(e), 120)}")
        return ""
    full = _extract_wl_body_full(resp.text)
    if not full:
        log("INFO", f"FDA WL 본문(전문) 위반 앵커 미발견 — 메타 카드 유지: {truncate(url, 80)}")
    return full


def collect_fda_warning_letters(start: date, end: date) -> tuple[list[IntakeItem], str | None]:
    """FDA Warning Letters 페이지 HTML 테이블 파싱 수집.

    Source Type: Official Regulatory Page.
    페이지: https://www.fda.gov/.../warning-letters

    FDA WL 페이지는 정적 HTML 테이블을 포함하므로 WebFetch 가능.
    JS-heavy 인 경우 content 부재 → 빈 결과 반환 (fail-silent).
    403/timeout 시 WARN 로그 후 빈 결과 반환.
    """
    log("INFO", f"FDA WL 수집: {FDA_WL_URL}")
    wl_body_enabled = env_flag("ENABLE_WL_BODY")
    # [WL 심층분석 fan-out] 전문 확보 게이트 — ENABLE_WL_BODY 와 독립(둘 다 off 가 기본).
    wl_body_full_enabled = env_flag("ENABLE_WL_BODY_FULL")
    # P1: excerpt 시도/실패 집계 — 시작 시점에 전역을 교체해(이른 return 포함) 항상
    # 이번 호출 분만 남긴다. dict 는 in-place 갱신이라 이후 증가분이 그대로 반영.
    global LAST_WL_HEALTH
    wl_body_health: dict[str, Any] = {
        "enabled": wl_body_enabled, "attempted": 0, "failed": 0,
    }
    wl_body_full_health: dict[str, Any] = {
        "enabled": wl_body_full_enabled, "attempted": 0, "failed": 0,
    }
    LAST_WL_HEALTH = {"wl_body": wl_body_health, "wl_body_full": wl_body_full_health}
    try:
        resp = requests.get(FDA_WL_URL, timeout=30, headers={
            "User-Agent": "GRM-Intake/1.1 (+github-actions)",
            "Accept": "text/html",
        })
        if resp.status_code == 403:
            log("WARN", "FDA WL 403 — HTML 수집 불가, 이번 주 WL 슬롯 건너뜀")
            return [], "HTTP 403"
        resp.raise_for_status()
        html_text = resp.text
    except requests.RequestException as e:
        log("WARN", f"FDA WL HTTP 실패: {e}")
        return [], str(e)

    parser = _FDAWLTableParser()
    parser.feed(html_text)

    if not parser.rows:
        msg = "FDA WL HTML 테이블 미발견 — 구조 변경 또는 JS-rendered 가능성"
        log("WARN", msg)
        return [], msg

    items: list[IntakeItem] = []
    for row in parser.rows:
        cols = row.get("_cols", [])
        if len(cols) < 4:
            continue
        # 실제 FDA WL 테이블 열 순서 (2026년 5월 확인):
        # [0] Posted Date  [1] Letter Issue Date  [2] Company Name(+href)
        # [3] Issuing Office  [4] Subject  [5] Response Letter  [6+] 기타
        posted_raw = cols[0].split("|HREF:")[0].strip()

        # 헤더 행 건너뜀 (col[0]이 날짜 패턴이 아닌 텍스트)
        if not re.match(r"^\d", posted_raw):
            continue

        letter_date_raw = cols[1].split("|HREF:")[0].strip() if len(cols) > 1 else ""

        # Company Name — href 포함 가능
        recipient_raw = cols[2] if len(cols) > 2 else ""
        wl_href = ""
        if "|HREF:" in recipient_raw:
            parts = recipient_raw.split("|HREF:", 1)
            recipient_raw = parts[0].strip()
            wl_href = parts[1].strip()

        issuing_office = cols[3].split("|HREF:")[0].strip() if len(cols) > 3 else ""
        subject = cols[4].split("|HREF:")[0].strip() if len(cols) > 4 else ""

        # Posted date 가 주 우선 날짜
        date_iso = _parse_wl_date(posted_raw) or _parse_wl_date(letter_date_raw)

        if not _within_window(date_iso, start, end):
            continue

        # URL 정규화
        if wl_href and wl_href.startswith("/"):
            wl_href = "https://www.fda.gov" + wl_href

        firm = recipient_raw
        headline = subject or firm or "FDA Warning Letter"

        # M0: 발행 부서 1차 게이트 (redesign §7). 부서 결측/미매핑은 본문 키워드 폴백.
        office_verdict = _fda_wl_office_gate(issuing_office, headline, subject, firm)
        if office_verdict == "exclude":
            log("INFO", f"FDA WL 부서 게이트 제외({truncate(issuing_office, 40)}): "
                        f"{truncate(firm or headline, 60)}")
            continue
        if office_verdict == "unknown" and _is_low_value_fda_warning_letter(
            headline, subject, issuing_office, firm
        ):
            log("INFO", f"FDA WL 저가치 식품/보충제 항목 제외: {truncate(firm or headline, 80)}")
            continue
        # keep / review / unknown(본문 폴백 통과) → 유지. review(OII 맥락 모호)는
        # 보수적 비-드롭(약품 WL 오삭제 방지). 전용 Status 마킹은 인프라 부재로 K4 이월.

        doc_id = _stable_doc_id(SOURCE_FDA_WL, firm, wl_href or FDA_WL_URL, date_iso)
        relevance = compute_relevance(headline, subject, issuing_office)
        tier = compute_signal_tier(SOURCE_FDA_WL, issuing_office or "Warning Letter",
                                   relevance, "N/A", headline, subject, issuing_office)

        wl_raw: dict[str, Any] = {
            "firm": firm, "posted_date": posted_raw,
            "letter_date": letter_date_raw,
            "issuing_office": issuing_office,
            "subject": subject, "url": wl_href,
        }
        # P2: 확정 제외/유지가 아닌 verdict(review·unknown)는 관측 가능하게 raw 에 남긴다
        # (머지 후 부서 게이트 오판 모니터링용). keep/exclude 는 자명하므로 기록 생략.
        if office_verdict in ("review", "unknown"):
            wl_raw["office_gate_verdict"] = office_verdict
            log("INFO", f"FDA WL 부서 게이트 {office_verdict}(유지·관측): "
                        f"{truncate(issuing_office or '부서결측', 40)} | "
                        f"{truncate(firm or headline, 50)}")

        # WHY-1 #2: 게이트 통과(유지) WL 의 편지 본문에서 위반 excerpt 추출(flag on 시).
        # 실패는 graceful(키 미기록 → 목록 메타 카드 유지). in-window 유지 WL 은 소수라 부담 낮음.
        if wl_body_enabled and wl_href:
            wl_body_health["attempted"] += 1
            body_excerpt = _fetch_wl_body_excerpt(wl_href)
            if body_excerpt:
                wl_raw["wl_body_excerpt"] = body_excerpt
            else:
                wl_body_health["failed"] += 1

        # [WL 심층분석 fan-out 2026-07-01] 전문 확보(flag on 시) — 카드별 fan-out 심층분석
        # (docs/prompts/GRM_Prompt_DeepWL_v1.md)의 유일한 입력. wl_body_enabled 와 완전
        # 독립 — 기존 excerpt 플로우는 이 블록의 영향을 받지 않는다(additive).
        if wl_body_full_enabled and wl_href:
            wl_body_full_health["attempted"] += 1
            body_full = _fetch_wl_body_full(wl_href)
            if body_full:
                wl_raw["wl_body_full"] = body_full
            else:
                wl_body_full_health["failed"] += 1

        items.append(IntakeItem(
            source=SOURCE_FDA_WL,
            document_id=doc_id,
            date_iso=date_iso,
            headline=headline,
            official_url=wl_href or FDA_WL_URL,
            type_or_class=issuing_office or "Warning Letter",
            firm=firm,
            body=subject,
            api_query=FDA_WL_URL,
            qa_relevance=relevance,
            osd_relevance="N/A",
            source_type=SRC_TYPE_OFFICIAL_PAGE,
            signal_tier=tier,
            raw_payload=wl_raw,
        ))

    log("INFO", f"FDA WL 수집 완료: {len(items)}건")
    return items, None


# ─────────────────────────────────────────────────────────────────────────────
# Notion 헬퍼
# ─────────────────────────────────────────────────────────────────────────────


def _plain_text(parts: list[dict[str, Any]] | None) -> str:
    if not parts:
        return ""
    return "".join(p.get("plain_text", "") for p in parts).strip()


def _prop_title(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("title", []))


def _prop_rich_text(props: dict[str, Any], name: str) -> str:
    return _plain_text(props.get(name, {}).get("rich_text", []))


def _prop_select(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("select") or {}).get("name", "") or ""


def _prop_date(props: dict[str, Any], name: str) -> str:
    return (props.get(name, {}).get("date") or {}).get("start", "") or ""


def _prop_url(props: dict[str, Any], name: str) -> str:
    return props.get(name, {}).get("url") or ""


def _intake_page_snapshot(page: dict[str, Any]) -> dict[str, Any]:
    props = page.get("properties", {})
    return {
        "page_id": page.get("id", ""),
        "page_url": page.get("url", ""),
        "title": _prop_title(props, PROP_NAME),
        "source": _prop_select(props, PROP_SOURCE),
        "document_id": _prop_rich_text(props, PROP_DOC_ID),
        "date": _prop_date(props, PROP_DATE),
        "headline": _prop_rich_text(props, PROP_HEADLINE),
        "official_url": _prop_url(props, PROP_OFFICIAL_URL),
        "source_url": _prop_url(props, PROP_SOURCE_URL),
        "type_or_class": _prop_select(props, PROP_TYPE_CLASS),
        "firm": _prop_rich_text(props, PROP_FIRM),
        "body": _prop_rich_text(props, PROP_BODY),
        "distribution": _prop_rich_text(props, PROP_DISTRIBUTION),
        "comments_close": _prop_date(props, PROP_COMMENTS_CLOSE),
        "run_date": _prop_date(props, PROP_RUN_DATE),
        "collected_at": _prop_date(props, PROP_COLLECTED_AT),
        "api_query": _prop_url(props, PROP_API_QUERY),
        "search_query": _prop_rich_text(props, PROP_SEARCH_QUERY),
        "raw_excerpt": _prop_rich_text(props, PROP_RAW_EXCERPT),
        "qa_relevance": _prop_select(props, PROP_QA_RELEVANCE),
        "osd_relevance": _prop_select(props, PROP_OSD_RELEVANCE),
        "modality": _prop_select(props, PROP_MODALITY),
        "source_type": _prop_select(props, PROP_SOURCE_TYPE),
        "signal_tier": _prop_select(props, PROP_SIGNAL_TIER),
        "evidence_candidate": _prop_select(props, PROP_EVIDENCE_CANDIDATE),
        "language": _prop_select(props, PROP_LANGUAGE),
        "region_jurisdiction": _prop_select(props, PROP_REGION_JURISDICTION),
        "site_country": _prop_rich_text(props, PROP_SITE_COUNTRY),
        "status": _prop_select(props, PROP_STATUS),
    }


# B1 임시 방어: handoff 조회 윈도우는 발행 cadence(주간 7일)보다 커야 주간 Routine 이
# 1회 지연돼도 미소비 New row 가 Run Date 하한 밖으로 빠져 영구 누락되지 않는다.
# 기본 30일은 dedup(MFDS enforcement 30일)과 정합. enforcement 의미를 handoff 에
# 섞지 않도록 전용 환경변수를 둔다. 근본 해결(날짜 하한 제거)은 PL-10b 와 별도 트랙.
_DEFAULT_HANDOFF_WINDOW_DAYS = 30


def resolve_handoff_window_days(cli_value: int | None) -> int:
    """handoff 조회 윈도우 결정 — CLI(--handoff-window-days) > GRM_HANDOFF_WINDOW_DAYS > 30."""
    if cli_value:
        return cli_value
    return _env_int("GRM_HANDOFF_WINDOW_DAYS", _DEFAULT_HANDOFF_WINDOW_DAYS)


def notion_query_new_intake_rows(token: str, db_id: str, run_date: date,
                                 window_days: int = 7,
                                 source_names: set[str] | None = None,
                                 doc_ids: set[str] | None = None,
                                 current_handoff_id: str | None = None,
                                 current_handoff_open: bool = True
                                 ) -> list[dict[str, Any]]:
    """Routine 에 넘길 Status=New row 를 Notion API 속성 필터로 조회한다.

    `current_handoff_id` 지정(멱등성 v2) 시 소비 자격을 날짜 윈도우가 아니라
    Handoff Ref 로 판정한다: `Status=New ∧ (Ref 비어있음 ∨ Ref=오늘 handoff)` —
    Run Date 하한 제거(PL-10b/B1 근본해결). `Ref=오늘` OR 절은 같은 날 재-emit 때
    이미 표시된 row 가 누락되지 않게 한다. 미지정(v1) 시 기존 날짜 윈도우 동작 그대로.

    `current_handoff_open=False`(Codex P1): 오늘 handoff 가 이미 CONSUMED/STALE 로
    종결된 경우 — `Ref=오늘` OR 절을 빼고 `Ref 비어있음` 만 자격으로 인정한다.
    이미 발행된 handoff 의 잔존 New row(Status 갱신 실패분)가 같은 날 재실행에서
    재유입되는 것을 차단한다(그 잔존분 마감은 reconcile 의 CONSUMED-cleanup 몫).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    window_start = (run_date - timedelta(days=window_days)).isoformat()
    if current_handoff_id:
        # v2: 날짜 하한 없음 — 상한(미래 Run Date 방어)과 ref 자격만.
        if current_handoff_open:
            ref_clause: dict[str, Any] = {"or": [
                {"property": PROP_HANDOFF_REF, "rich_text": {"is_empty": True}},
                {"property": PROP_HANDOFF_REF, "rich_text": {"equals": current_handoff_id}},
            ]}
        else:
            ref_clause = {"property": PROP_HANDOFF_REF, "rich_text": {"is_empty": True}}
        filters: list[dict[str, Any]] = [
            {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
            ref_clause,
        ]
    else:
        filters = [
            {"property": PROP_RUN_DATE, "date": {"on_or_after": window_start}},
            {"property": PROP_RUN_DATE, "date": {"on_or_before": run_date.isoformat()}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]
    body: dict[str, Any] = {
        "filter": {"and": filters},
        "page_size": 100,
    }
    snapshots: list[dict[str, Any]] = []
    start_cursor: str | None = None
    for page_no in range(50):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            if source_names and snap["source"] not in source_names:
                continue
            if doc_ids and snap["document_id"] not in doc_ids:
                continue
            if not snap["source"] or not snap["document_id"]:
                log("WARN", f"handoff 후보 row 필수 키 누락 — skip page={snap['page_id']}")
                continue
            snapshots.append(snap)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "Routine handoff New row 조회 50페이지 상한 도달 — 일부 row 누락 가능")

    if current_handoff_id:
        log("INFO", f"Routine handoff 후보 New row {len(snapshots)}건 "
                    f"(멱등성 v2 ref 기반 — Run Date ≤{run_date.isoformat()}, 날짜 하한 없음)")
    else:
        log("INFO", f"Routine handoff 후보 New row {len(snapshots)}건 "
                    f"(Run Date {window_start}~{run_date.isoformat()})")
    return snapshots


# B1 임시 방어 ②: 윈도우(30일)로도 못 막는 케이스(Routine 3주+ 누락)를 침묵 대신
# 경고로 띄운다. 카운트 목적이라 전수 페이지네이션 불요 — 상한 도달 시 하한값으로
# 충분하다(경고 트리거는 N>0 여부, 정확 수는 사람이 Notion 에서 확인).
_AGED_NEW_MAX_PAGES = 5
_AGED_NEW_PAGE_SIZE = 50


def notion_count_aged_unconsumed_new(token: str, db_id: str, run_date: date,
                                     handoff_window_days: int) -> int:
    """handoff 조회 윈도우 밖에 남은 미소비 Status=New row 수(읽기전용, 하한값).

    필터: Status=New AND Run Date on_or_before (run_date − handoff_window_days − 1)
    — notion_query_new_intake_rows 하한(on_or_after run_date−window) 바로 바깥.
    handoff 페이지 자체(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아니므로
    동일 규칙으로 제외. 조회 실패는 예외를 그대로 올린다 — 호출부(main)가
    try/except 후 경고로 표면화한다(조용한 0 반환 금지).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    cutoff = (run_date - timedelta(days=handoff_window_days + 1)).isoformat()
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_RUN_DATE, "date": {"on_or_before": cutoff}},
            {"property": PROP_STATUS, "select": {"equals": "New"}},
        ]},
        "page_size": _AGED_NEW_PAGE_SIZE,
    }
    count = 0
    start_cursor: str | None = None
    for _ in range(_AGED_NEW_MAX_PAGES):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            count += 1
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    return count


# ── K2-prep: page_id 로 원 row raw API JSON 복원 → rows 에 부착 (card_spec §12(A)) ──
# v1 handoff snapshot 에는 raw 가 없다(원본 API 응답 JSON 전체는 Intake row 본문
# code block 에만 있고, `raw_excerpt` 속성은 잘린 발췌라 불충분). raw 의존 칸(W3 인용·
# MFDS W2·Modality 폴백)은 이 보강 후에만 결정론적이다(redesign §4, card_spec §12(A)).
# ⚠️ 이 단계는 네트워크 호출이므로 build_card_scaffold() 안에 두지 않는다(§12(G) 순수성).
_INTAKE_RAW_MAX_PAGES = 25  # children 100/page · raw 는 ≤2KB 청크라 1페이지로 충분(안전 상한)


def fetch_intake_raw_payload(token: str, page_id: str) -> dict[str, Any] | None:
    """Intake row(page_id) 본문의 JSON code block 들을 순서대로 이어붙여 raw dict 복원.

    `build_notion_children()` 이 저장한 'Raw API payload' code block(language=json,
    NOTION_CODE_BLOCK_CHUNK 청크)을 역으로 재조립한다. fetch/파싱 실패 시 None 반환
    (호출부 graceful degrade — 예외를 던지지 않아 전체 handoff 를 중단시키지 않는다).
    """
    if not page_id:
        return None
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    code_chunks: list[str] = []
    start_cursor: str | None = None
    try:
        for _ in range(_INTAKE_RAW_MAX_PAGES):
            req_url = url
            if start_cursor:
                req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}"
            data = notion_api_request("GET", req_url, token)
            for block in data.get("results", []):
                if block.get("type") != "code":
                    continue
                code = block.get("code", {})
                if (code.get("language") or "") not in ("json", "plain text", ""):
                    continue
                for rt in code.get("rich_text", []):
                    code_chunks.append(
                        rt.get("plain_text")
                        or rt.get("text", {}).get("content", "")
                        or ""
                    )
            if not data.get("has_more"):
                break
            start_cursor = data.get("next_cursor")
    except (NotionHandoffError, requests.RequestException) as e:
        log("WARN", f"K2-prep children fetch 실패 page={page_id}: {truncate(str(e), 120)}")
        return None
    if not code_chunks:
        return None
    raw_text = "".join(code_chunks)
    try:
        parsed = json.loads(raw_text)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def attach_raw_to_rows(token: str, rows: list[dict[str, Any]],
                       inmemory_raw: dict[str, dict[str, Any]] | None = None,
                       sleep_s: float = 0.34) -> dict[str, int]:
    """각 row 에 raw API JSON 을 부착한다(하이브리드 — 지시문 단계 B).

    `inmemory_raw`(당일 수집분 raw_payload, key = `source::document_id`)에 있으면
    네트워크 없이 그대로 사용하고, 없으면(과거 누적 New row) page children 을 fetch 한다.
    실패 row 는 graceful degrade(card_spec §8, Codex 정정): raw=None ·
    raw_fetch_ok=False · evidence_hint='B'(A 불가) · status_hint='Error'(기존 DB
    옵션; 전용 'Needs Review' 옵션 신설은 K4 이월). 전체 중단 금지.

    ⚠️ raw 는 메모리 상 enriched row 에만 부착한다 — 최종 handoff v2 JSON 에는 넣지
    않는다(scaffold·prose_input 만; 크기 폭증·Notion children 한도 방지, 단계 B 보정).
    반환: {'ok','failed','from_memory','total'} 통계.
    """
    inmemory_raw = inmemory_raw or {}
    ok = failed = from_memory = 0
    for row in rows:
        card_id = f"{row.get('source', '')}::{row.get('document_id', '')}"
        cached = inmemory_raw.get(card_id)
        if cached is not None:
            row["raw"] = cached
            row["raw_fetch_ok"] = True
            row["raw_source"] = "memory"
            ok += 1
            from_memory += 1
            continue
        page_id = row.get("page_id", "")
        raw = fetch_intake_raw_payload(token, page_id)
        if raw is None:
            row["raw"] = None
            row["raw_fetch_ok"] = False
            row["raw_source"] = "fetch"
            row["evidence_hint"] = "B"
            row["status_hint"] = "Error"
            failed += 1
            log("WARN", "K2-prep raw 부착 실패 → graceful degrade(Evidence B·Status Error): "
                        f"{card_id} page={page_id}")
        else:
            row["raw"] = raw
            row["raw_fetch_ok"] = True
            row["raw_source"] = "fetch"
            ok += 1
        if sleep_s:
            time.sleep(sleep_s)  # 실제 fetch 한 경우만 rate-limit 대기
    log("INFO", f"K2-prep raw 부착 완료: 성공 {ok}건(메모리 {from_memory}·fetch {ok - from_memory}) "
                f"/ 실패 {failed}건 (총 {len(rows)})")
    return {"ok": ok, "failed": failed, "from_memory": from_memory, "total": len(rows)}


def enrich_rows_with_raw(token: str, rows: list[dict[str, Any]],
                         inmemory_raw: dict[str, dict[str, Any]] | None = None,
                         sleep_s: float = 0.34
                         ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """K2-prep 진입점: `_dedupe_latest_rows()` 선적용(중복 fetch 제거) → 하이브리드 raw 부착.

    handoff v2 생성 직전 단계. 반환 (deduped_rows, stats). v2 payload 빌더가 이 결과를
    소비하되 raw 는 JSON 직렬화에서 제외한다(단계 B·D 보정).
    """
    deduped = _dedupe_latest_rows(rows)
    stats = attach_raw_to_rows(token, deduped, inmemory_raw=inmemory_raw, sleep_s=sleep_s)
    return deduped, stats


def build_inmemory_raw(*item_lists: list["IntakeItem"]) -> dict[str, dict[str, Any]]:
    """당일 수집 IntakeItem 들을 `{card_id: raw_payload}` 로 모은다(K3 G2 와이어링).

    key = `source::document_id`(handoff row·`attach_raw_to_rows` 와 동일 규약). 이 dict 를
    `emit_routine_handoff(inmemory_raw=...)` 로 넘기면 당일 수집분은 children fetch 없이
    메모리에서 raw 를 부착하고, 과거 누적 New row 만 fetch 폴백한다(혼합 케이스).
    raw_payload 가 비어 있으면 제외 — `attach_raw_to_rows` 는 `get(card_id) is not None`
    으로 적중 판정하므로 빈 dict 가 들어가면 fetch 폴백을 가로채 graceful degrade 를 막는다.
    중복 card_id 는 첫 항목 우선(수집 순서 결정론).
    """
    out: dict[str, dict[str, Any]] = {}
    for items in item_lists:
        for it in items:
            if not it.raw_payload:
                continue
            out.setdefault(f"{it.source}::{it.document_id}", it.raw_payload)
    return out


def _dedupe_latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('source', '')}::{row.get('document_id', '')}"
        current = latest.get(key)
        freshness = (row.get("run_date", ""), row.get("collected_at", ""), row.get("page_id", ""))
        if current is None:
            latest[key] = row
            continue
        current_freshness = (
            current.get("run_date", ""),
            current.get("collected_at", ""),
            current.get("page_id", ""),
        )
        if freshness > current_freshness:
            latest[key] = row

    tier_order = {"Tier 3": 0, "Tier 2": 1, "Tier 1": 2}
    return sorted(
        latest.values(),
        key=lambda r: (
            tier_order.get(r.get("signal_tier", ""), 9),
            r.get("source", ""),
            r.get("document_id", ""),
        ),
    )


# 한국어 요일(date.weekday(): 월=0..일=6). 발행 요일을 LLM 이 산술하지 않게 handoff 에
# 결정론 산출해 싣는다(06-17 dry-run D-1: LLM 이 수요일을 화요일로 오산). run_date 는 이미
# KST 달력일이라 weekday() 가 KST 요일과 일치(타임존 off-by-one 없음).
_KO_WEEKDAYS_FULL = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


def weekday_kst(run_date: date) -> str:
    """run_date(KST 달력일)의 한국어 요일 문자열(예: '수요일'). handoff weekday_kst 슬롯용."""
    return _KO_WEEKDAYS_FULL[run_date.weekday()]


# ─────────────────────────────────────────────────────────────────────────────
# 수집 현황(커버리지) '수집' 컬럼 결정론화 (W1) — handoff source_counts 를 발행 커버리지
# callout 어휘로 미리 포맷한다. LLM 이 재집계·추정하지 않고 그대로 전사한다(요일 weekday_kst
# 와 동형). 라벨·순서는 v16 프롬프트 [블록 3 — 커버리지] callout 과 일치 — 코드가 단일 기준.
# 발행 후 탐지(verify_published_brief + brief_lint.lint_coverage_counts)도 같은 정본으로
# 발행물 숫자를 대조한다(W2). 06-17 검증: 발행=실제 카드수는 확인됐으나 수집/스킵은 LLM
# 집계라 무보증 클래스(요일 오산과 동형) → 수집은 코드가 산출·감사 가능하게 한다.
# ─────────────────────────────────────────────────────────────────────────────
# (source 문자열, 발행 callout 라벨) — 고정 순서. 상시 수집기 11종(프롬프트 callout 동일).
COVERAGE_SOURCE_LABELS: tuple[tuple[str, str], ...] = (
    (SOURCE_FR, "FR"),
    (SOURCE_RECALL, "Recall"),
    (SOURCE_EMA, "EMA"),
    (SOURCE_MHRA, "MHRA"),
    (SOURCE_PICS, "PIC/S"),
    (SOURCE_ECA, "ECA"),
    (SOURCE_FDA_WL, "FDA WL"),
    (SOURCE_MFDS, "MFDS"),
    (SOURCE_ICH, "ICH"),
    (SOURCE_WHO, "WHO"),
    (SOURCE_HC, "HC"),
)
_COVERAGE_KNOWN_SOURCES = frozenset(s for s, _ in COVERAGE_SOURCE_LABELS)


def coverage_source_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """rows 에서 소스별 수집 건수 재집계 — build_routine_handoff_payload* 와 동일 산식.

    발행 후 탐지가 handoff rows(top-level source_counts 없이도)로 '수집' 정본을 독립
    복원할 때 쓴다(병합 멤버 포함 전수 카운트 — payload 의 source_counts 와 바이트 동일).
    """
    out: dict[str, int] = {}
    for row in rows or []:
        if isinstance(row, dict):
            src = row.get("source", "")
            out[src] = out.get(src, 0) + 1
    return out


def build_coverage_collected(source_counts: dict[str, int]) -> dict[str, Any]:
    """'수집' 컬럼(소스별 수집 건수 + 총계)을 결정론 산출한다(W1).

    반환 {"total": int, "items": [{"label","source","count"}...], "md": str}:
    - known 소스(COVERAGE_SOURCE_LABELS)는 고정 순서로 전부 포함(0건도 — '조용한 주' 가시화).
    - 라벨 미정의 소스(예: FDA 483)는 count>0 일 때만 원 이름으로 끝에 덧붙인다(조용한 유실 금지).
    - total = 모든 source_counts 합(= handoff row_count, 병합 멤버 포함).
    - md = 발행 callout 의 수집 세그먼트: "Intake row {total}건 ({label} {n} · ...)".
    LLM 은 md 를 그대로 삽입하고 병합·WebSearch·유효항목·Evidence·미확인 등 발행측 값만 채운다.
    """
    counts = {k: int(v) for k, v in (source_counts or {}).items()}
    items: list[dict[str, Any]] = []
    for source, label in COVERAGE_SOURCE_LABELS:
        items.append({"label": label, "source": source, "count": counts.get(source, 0)})
    for source in sorted(k for k in counts if k and k not in _COVERAGE_KNOWN_SOURCES):
        if counts[source] > 0:
            items.append({"label": source, "source": source, "count": counts[source]})
    total = sum(counts.values())
    seg = " · ".join(f"{it['label']} {it['count']}" for it in items)
    return {"total": total, "items": items, "md": f"Intake row {total}건 ({seg})"}


def build_routine_handoff_payload(rows: list[dict[str, Any]], run_date: date,
                                  window_days: int,
                                  generated_at: datetime) -> dict[str, Any]:
    start = run_date - timedelta(days=window_days)
    deduped = _dedupe_latest_rows(rows)
    source_counts: dict[str, int] = {}
    for row in deduped:
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "handoff_id": f"routine-handoff::{run_date.isoformat()}",
        "run_date_kst": run_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(deduped),
        "source_counts": source_counts,
        "rows": deduped,
    }


def _enable_handoff_v2() -> bool:
    """handoff v2 feature flag (기본 off, vars fallback 패턴). 운영 전환은 K3 와 함께."""
    return env_flag("ENABLE_HANDOFF_V2")


def _enable_web_brief_emit() -> bool:
    """빈슬롯 web brief emit flag (§1-B 영구배선, 기본 off, vars fallback 패턴).

    off = 현행과 byte 동일(파일 산출 0). on(+ENABLE_HANDOFF_V2 path) = 수집 시점에
    `brief_web_{run_date}.json`(grm-web-card/v1 빈슬롯)을 결정론 산출 → routine 델타를
    `inject_slots` 로 주입만 하면 그 주 산문 발행(1회용 파서·수기 fixture 제거). raw 가
    살아있는 handoff v2 카드 producer 를 재사용하므로 ENABLE_HANDOFF_V2 전제(아래 emit 분기).
    """
    return env_flag("ENABLE_WEB_BRIEF_EMIT")


def resolve_web_brief_dir() -> str:
    """빈슬롯 web brief 산출 디렉터리 — GRM_WEB_BRIEF_DIR > 현재 작업 디렉터리('.').

    워크플로(Option A)는 이 경로의 `brief_web_*.json` 을 artifact 로 업로드하고, 사람이
    `web/data/briefs/` 에 커밋한다(무인 라이브 0 = D5 게이트 보존). 직접 main push 금지.
    """
    return os.environ.get("GRM_WEB_BRIEF_DIR", "").strip() or "."


def _enable_handoff_idempotency_v2() -> bool:
    """PL-10b/B1 근본해결 flag (기본 off) — Handoff Ref 상태기계로 소비 자격 판정.

    off = 현행(날짜 윈도우 + K4-1 STALE) 100% 동일. on 전환은 K3 4주 관찰 종료 후
    사람 승인으로(Notion 'Handoff Ref' rich_text 속성 사전 생성 필요 — preflight 가
    부재를 감지하면 그 실행만 v1 으로 폴백). ENABLE_HANDOFF_V2(payload 스키마)와 직교.
    """
    return env_flag("ENABLE_HANDOFF_IDEMPOTENCY_V2")


# v2 row 에 보존할 v1 호환 필드(whitelist) — _intake_page_snapshot() 스키마.
# blacklist 대신 whitelist 로(Codex P2): raw·Stage B 부착 bookkeeping(raw_fetch_ok·
# raw_source·status_hint·evidence_hint) 같은 내부/대형 필드가 새지 않도록 보장.
_HANDOFF_V2_ROW_KEEP = (
    "page_id", "page_url", "title", "source", "document_id", "date", "headline",
    "official_url", "source_url", "type_or_class", "firm", "body", "distribution",
    "comments_close", "run_date", "collected_at", "api_query", "search_query",
    "raw_excerpt", "qa_relevance", "osd_relevance", "modality", "source_type",
    "signal_tier", "evidence_candidate", "language", "region_jurisdiction",
    "site_country", "status",
)


def build_routine_handoff_payload_v2(rows: list[dict[str, Any]], run_date: date,
                                     window_days: int,
                                     generated_at: datetime) -> dict[str, Any]:
    """handoff v2(additive) payload. 순수 함수 — 네트워크 없음(scaffold 조립만).

    `rows` 는 K2-prep(`enrich_rows_with_raw`)로 **dedupe·raw 부착**된 상태여야 한다.
    각 row 는 v1 호환 필드 whitelist 복사 + `card_scaffold`·`prose_input`·`section`·
    `card_id`·`evidence`·`recall_group_key`(해당 시)·`status_hint`(degrade 시) additive.
    **raw 전체·Stage B bookkeeping 은 제외**(크기 폭증·내부필드 누출 방지).
    recall 다품목은 `merge_recall_cards()`(§14)로 대표 1카드 + 멤버 `merged_into` 직렬화:
    멤버 row 는 v1 호환 필드 + `merged_into` 만 유지(자체 card_id 포함 v2 additive 필드 전부
    생략 → Routine 렌더 제외, page_id 보존으로 Status 갱신 목록에는 잔존, Codex R1-a).
    대표/단독 row 는 `render_order`·`group_label`(A안, R1-d)로 §7 정렬·그룹핑 결과를 받아
    Routine 이 정렬을 재현하지 않게 한다(`compute_render_plan` = assemble_brief_skeleton 공유).
    """
    start = run_date - timedelta(days=window_days)
    cards = merge_recall_cards([build_card_scaffold(row, row.get("raw")) for row in rows])
    render_plan = compute_render_plan(cards)
    out_rows: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for row, card in zip(rows, cards):
        v2row = {k: row[k] for k in _HANDOFF_V2_ROW_KEEP if k in row}
        if card.merged_into:
            # §14(F)·R1-a 멤버: v1 호환 필드 + merged_into 만(자체 card_id·card_scaffold·
            # prose_input·needs_llm_slots·section·evidence·recall_group_key·render_order 생략).
            # 렌더 제외, page_id 보존으로 Status 갱신 목록에만 잔존. 그룹 식별은 merged_into 로.
            v2row["merged_into"] = card.merged_into
        else:
            v2row["card_id"] = card.card_id
            v2row["section"] = card.section
            v2row["evidence"] = card.evidence
            v2row["card_scaffold"] = card.markdown
            v2row["prose_input"] = card.prose_input
            v2row["needs_llm_slots"] = list(card.needs_llm_slots)
            plan = render_plan.get(card.card_id)
            if plan is not None:
                v2row["render_order"] = plan["render_order"]
                if plan["group_label"]:
                    v2row["group_label"] = plan["group_label"]
            if card.recall_group_key:
                v2row["recall_group_key"] = card.recall_group_key
            if card.status_hint:
                v2row["status_hint"] = card.status_hint
        out_rows.append(v2row)
        source_counts[row.get("source", "")] = source_counts.get(row.get("source", ""), 0) + 1
    return {
        "schema_version": HANDOFF_SCHEMA_VERSION_V2,
        "handoff_id": f"routine-handoff::{run_date.isoformat()}",
        "run_date_kst": run_date.isoformat(),
        "weekday_kst": weekday_kst(run_date),  # 발행 요일 결정론 산출 — LLM 산술 금지(D-1)
        "window_start": start.isoformat(),
        "window_end": run_date.isoformat(),
        "generated_at_kst": generated_at.isoformat(),
        "row_count": len(out_rows),
        "source_counts": source_counts,
        # 수집 현황 '수집' 컬럼 결정론 산출 — LLM 재집계 금지(W1). 발행 callout 에 그대로 전사.
        "coverage_collected_md": build_coverage_collected(source_counts)["md"],
        "rows": out_rows,
    }


def build_web_brief_payload_v2(rows: list[dict[str, Any]], run_date: date,
                               window_days: int) -> dict[str, Any]:
    """빈슬롯 `grm-web-card/v1` 브리프 payload(§1-B 영구배선). 순수·결정론 — 네트워크·
    현재시각·LLM 0.

    `rows` 는 handoff v2(`build_routine_handoff_payload_v2`)와 **동일한 enriched(raw 부착)
    rows** 여야 한다 — 같은 카드 producer(`build_card_scaffold`→`merge_recall_cards`)를
    재구성하므로 두 산출의 카드 사실 셀은 byte 동일(드리프트 0). `card_scaffold.assemble_web_brief`
    가 LLM 슬롯(title_issue·summary·key_facts·implication·checks·비KO translation·tldr)을
    빈값으로 둔 브리프를 낸다 → routine 델타를 `inject_slots` 로 주입만 하면 발행(1회용 파서 제거).

    `brief_meta` = handoff 와 동일 소스: run_date·window(수집 윈도우)·intake_total(=row 수).
    `publish_date` 기본 = run_date(주차 재발행 시 사람이 커밋 단계에서 조정). tldr 은 빈슬롯([]).
    """
    start = run_date - timedelta(days=window_days)
    cards = merge_recall_cards([build_card_scaffold(row, row.get("raw")) for row in rows])
    brief_meta = {
        "run_date_kst": run_date.isoformat(),
        "window": f"{start.isoformat()} ~ {run_date.isoformat()}",
        "publish_date": run_date.isoformat(),
        "intake_total": len(rows),
        "tldr": [],  # LLM placeholder (inject_slots 가 채움)
    }
    return assemble_web_brief(cards, brief_meta)


def web_brief_filename(run_date: date) -> str:
    """`brief_web_{YYYY_MM_DD}.json`(web/data/briefs 규약 — 날짜 구분자 '_')."""
    return f"brief_web_{run_date.isoformat().replace('-', '_')}.json"


def emit_web_brief_file(rows: list[dict[str, Any]], run_date: date, window_days: int,
                        out_dir: str) -> str:
    """빈슬롯 web brief 를 `out_dir/brief_web_{run_date}.json` 로 결정론 기록 후 경로 반환.

    실 producer 경로(`build_web_brief_payload_v2` = `assemble_web_brief`)로 산출 —
    파싱/수기 fixture 아님. data 관례(`indent=1`·`ensure_ascii=False`·LF·후행개행)로 쓴다
    (`web/render._write_json` 과 동형 → 같은 입력 byte 동일).
    """
    payload = build_web_brief_payload_v2(rows, run_date, window_days)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, web_brief_filename(run_date))
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    with open(path, "wb") as f:  # LF/UTF-8 고정 — OS 무관 결정론(Windows \r\n 차단)
        f.write(text.encode("utf-8"))
    return path


def _handoff_page_properties(payload: dict[str, Any],
                             generated_at: datetime) -> dict[str, Any]:
    run_date = payload["run_date_kst"]
    row_count = payload["row_count"]
    title = f"OPEN GRM Routine Handoff {run_date}"
    body = (
        f"New-only Routine handoff. rows={row_count}; "
        f"window={payload['window_start']}~{payload['window_end']}; "
        f"generated_at={payload['generated_at_kst']}"
    )
    return {
        PROP_NAME: {"title": _rich_text(title)},
        PROP_SOURCE: _select(SOURCE_HANDOFF),
        PROP_DOC_ID: {"rich_text": _rich_text(payload["handoff_id"])},
        PROP_DATE: {"date": {"start": run_date}},
        PROP_HEADLINE: {"rich_text": _rich_text(f"Routine New-only handoff ({row_count} rows)")},
        PROP_TYPE_CLASS: _select(TYPE_ROUTINE_HANDOFF),
        PROP_BODY: {"rich_text": _rich_text(body)},
        PROP_RUN_DATE: {"date": {"start": run_date}},
        PROP_COLLECTED_AT: _datetime_iso(generated_at),
        PROP_STATUS: _select("New"),
    }


def _handoff_blocks(payload: dict[str, Any], compact: bool = False) -> list[dict[str, Any]]:
    # v2(compact=True): sort_keys 결정론 + 공백 제거(크기 절감, §12G). v1: 기존 indent=2 유지(바이트 동일).
    if compact:
        json_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"))
    else:
        json_payload = json.dumps(payload, ensure_ascii=False, indent=2)
    blocks: list[dict[str, Any]] = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": _rich_text("GRM Routine Handoff")},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(
                f"HANDOFF_ID: {payload['handoff_id']} | "
                f"SCHEMA: {payload['schema_version']} | "
                f"ROW_COUNT: {payload['row_count']}"
            )},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _rich_text(
                f"WINDOW: {payload['window_start']}~{payload['window_end']} | "
                f"GENERATED_AT_KST: {payload['generated_at_kst']}"
            )},
        },
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": _rich_text("Payload JSON")},
        },
    ]
    for chunk in chunk_text(json_payload, NOTION_CODE_BLOCK_CHUNK):
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "language": "json",
                "rich_text": [{"type": "text", "text": {"content": chunk}}],
            },
        })
    return blocks


def notion_find_handoff_page(token: str, db_id: str,
                             handoff_id: str) -> dict[str, Any] | None:
    body = {
        "filter": {
            "property": PROP_DOC_ID,
            "rich_text": {"equals": handoff_id},
        },
        "page_size": 5,
    }
    data = notion_api_request("POST", NOTION_DB_QUERY_URL_TPL.format(db_id=db_id),
                              token, body=body)
    results = data.get("results", [])
    if not results:
        return None
    results.sort(key=lambda p: p.get("last_edited_time", ""), reverse=True)
    return results[0]


def notion_stale_prior_open_handoffs(token: str, db_id: str,
                                     keep_handoff_id: str,
                                     superseded_by: str,
                                     revert_refs: bool = False) -> int:
    """새 OPEN handoff emit 전, 직전 미소비 OPEN handoff 를 STALE 로 봉인한다(K4-1).

    Type or Class=`routine-handoff` 이고 Status=`New`(=OPEN) 인 handoff page 중
    handoff_id 가 `keep_handoff_id` 와 다른 것을 전부 찾아 Title→`STALE GRM Routine
    Handoff {날짜} (superseded by {superseded_by})`, Status→`Skipped` 로 바꾼다.
    → '항상 OPEN 1개' 불변식: 일일 emit 누적·주간 소비 오선택(6/8 근본원인) 차단.

    ⚠️ 불가침(v1, revert_refs=False 기본): handoff page **자신의** Name·Status 두
    속성만 PATCH 한다. 그 page 의 rows[] 가 가리키는 **개별 Intake row page 의
    Status 는 절대 건드리지 않는다**(handoff 의 children 은 JSON code block 일 뿐 —
    row page 가 아니다). 반환=봉인 건수.

    `revert_refs=True`(멱등성 v2, PL-10b/B1 근본해결 — K4-1 불가침의 의도적 변경):
    STALE 봉인한 handoff 의 **미발행(Status=New) row 만** `Handoff Ref` 를 비워
    다음 emit 에 재투입한다(B1 revert — 누락 0). row 의 Status 는 여기서도 불변 —
    Processed/Skipped row 는 ref 포함 일절 건드리지 않는다.
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {
            "and": [
                {"property": PROP_TYPE_CLASS, "select": {"equals": TYPE_ROUTINE_HANDOFF}},
                {"property": PROP_STATUS, "select": {"equals": "New"}},
            ]
        },
        "page_size": 100,
    }
    staled = 0
    staled_ids: list[str] = []
    start_cursor: str | None = None
    for _ in range(25):  # handoff page 는 소수 — 안전 상한
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            prior_id = snap["document_id"]
            if not prior_id or prior_id == keep_handoff_id:
                continue  # 오늘 emit 본인(keep)·식별 불가 page 는 봉인 금지
            prior_date = prior_id.split("::", 1)[-1] or snap["run_date"] or "?"
            new_title = (f"STALE GRM Routine Handoff {prior_date} "
                         f"(superseded by {superseded_by})")
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=snap["page_id"]), token,
                body={"properties": {
                    PROP_NAME: {"title": _rich_text(new_title)},
                    PROP_STATUS: _select("Skipped"),
                }},
            )
            staled += 1
            staled_ids.append(prior_id)
            time.sleep(0.34)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    if staled:
        log("INFO", f"직전 미소비 OPEN handoff {staled}건 STALE 봉인 "
                    f"(keep={keep_handoff_id})")
    if revert_refs:
        for prior_id in staled_ids:
            notion_revert_refs_for_handoff(token, db_id, prior_id)
    return staled


_HANDOFF_REF_ROWS_MAX_PAGES = 10  # ref 잔존 New row 는 소수(미마감 handoff 분량) — 안전 상한


def _query_new_rows_with_ref(token: str, db_id: str,
                             ref_filter: dict[str, Any]) -> list[tuple[str, str]]:
    """Status=New ∧ ref_filter 인 Intake row 의 (page_id, handoff_ref) 목록.

    handoff page 자체(SOURCE_HANDOFF/TYPE_ROUTINE_HANDOFF)는 큐 row 가 아니므로 제외.
    handoff_ref 는 snapshot 스키마에 넣지 않고 여기서만 읽는다 — snapshot 은 v1 handoff
    payload rows 로 그대로 직렬화되므로 키 추가 = flag off 경로 바이트 변경(금지).
    """
    url = NOTION_DB_QUERY_URL_TPL.format(db_id=db_id)
    body: dict[str, Any] = {
        "filter": {"and": [
            {"property": PROP_STATUS, "select": {"equals": "New"}},
            ref_filter,
        ]},
        "page_size": 100,
    }
    out: list[tuple[str, str]] = []
    start_cursor: str | None = None
    for _ in range(_HANDOFF_REF_ROWS_MAX_PAGES):
        if start_cursor:
            body["start_cursor"] = start_cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        data = notion_api_request("POST", url, token, body=body)
        for page in data.get("results", []):
            snap = _intake_page_snapshot(page)
            if snap["source"] == SOURCE_HANDOFF or snap["type_or_class"] == TYPE_ROUTINE_HANDOFF:
                continue
            ref = _prop_rich_text(page.get("properties", {}), PROP_HANDOFF_REF)
            out.append((snap["page_id"], ref))
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    else:
        log("WARN", "Handoff Ref row 조회 페이지 상한 도달 — 일부 row 는 다음 emit 에서 처리")
    return out


def notion_revert_refs_for_handoff(token: str, db_id: str, handoff_id: str) -> int:
    """STALE handoff 의 미발행 row 재투입(B1 revert) — Ref=handoff_id ∧ Status=New
    row 의 `Handoff Ref` 를 비운다. Status 는 불변. per-row 실패는 경고 후 계속 —
    남은 ref 는 다음 emit 의 reconcile sweep 이 다시 처리한다(자기치유). 반환=비운 건수.
    """
    rows = _query_new_rows_with_ref(
        token, db_id,
        {"property": PROP_HANDOFF_REF, "rich_text": {"equals": handoff_id}})
    reverted = 0
    for page_id, _ref in rows:
        try:
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                body={"properties": {PROP_HANDOFF_REF: {"rich_text": []}}})
            reverted += 1
        except NotionHandoffError as e:
            log("WARN", f"Handoff Ref revert 실패(다음 emit 재시도) page={page_id}: "
                        f"{truncate(str(e), 120)}")
        time.sleep(0.34)
    if rows:
        log("INFO", f"STALE handoff {handoff_id} 의 미발행 row {reverted}/{len(rows)}건 "
                    f"재투입(Handoff Ref 비움)")
    return reverted


def notion_reconcile_handoff_refs(token: str, db_id: str,
                                  current_handoff_id: str) -> dict[str, int]:
    """emit 시 reconcile sweep(멱등성 v2) — `Status=New ∧ Handoff Ref 비어있지 않음`
    row 전수를 ref 가 가리키는 handoff page 상태로 마감한다. 신뢰 신호 = handoff
    page Status(발행 종료 시 Routine 의 단일 쓰기 — per-row Status 갱신보다 견고).

      - CONSUMED(Processed) → row Status→Processed (PL-10b cleanup: 발행됐으나
        per-row Status 갱신 실패/지연분 마감 — 재유입 0). ref 는 추적성 위해 유지.
        **ref=오늘(current)이라도 동일**(Codex P1: 오늘 handoff 가 이미 CONSUMED 인
        같은 날 재실행 — 잔존 New 는 발행분이므로 마감).
      - STALE(Skipped) → ref 비움 (B1 revert — 직전 revert 의 per-row 실패/크래시
        잔존분 보정; 다음 소비 쿼리에 재투입).
      - OPEN(New) ∧ ref=오늘(current) → 불변(같은 날 재-emit; 소비 쿼리 OR 절이 포함).
      - OPEN(New) ∧ ref≠오늘 → 경고만(STALE 가드가 선행 실행되므로 비정상 상태).
      - handoff page 미발견·기타 상태 → ref 비움 + 경고 (고아 ref — 재투입이 침묵
        누락보다 안전. 중복은 v16 프롬프트 PL-10b 가드가 2차 방어).

    idempotent — 같은 입력에 재적용해도 결과 동일. per-row 실패는 경고 후 계속.
    반환: {"cleaned","reverted","orphaned","kept"} 건수.
    """
    rows = _query_new_rows_with_ref(
        token, db_id,
        {"property": PROP_HANDOFF_REF, "rich_text": {"is_not_empty": True}})
    stats = {"cleaned": 0, "reverted": 0, "orphaned": 0, "kept": 0}
    handoff_status_cache: dict[str, str | None] = {}
    for page_id, ref in rows:
        if ref not in handoff_status_cache:
            handoff_page = notion_find_handoff_page(token, db_id, ref)
            handoff_status_cache[ref] = (
                _intake_page_snapshot(handoff_page)["status"] if handoff_page else None)
        handoff_status = handoff_status_cache[ref]
        try:
            if handoff_status == "Processed":
                # CONSUMED — 발행 완료 신호. row 마감(Status 지연분 cleanup).
                notion_api_request(
                    "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                    body={"properties": {PROP_STATUS: _select("Processed")}})
                stats["cleaned"] += 1
            elif handoff_status == "New":
                if ref != current_handoff_id:
                    log("WARN", f"reconcile: row {page_id} 의 ref={ref} 가 여전히 OPEN — "
                                f"STALE 가드 선행 후 비정상 상태, 이번 emit 은 보류")
                stats["kept"] += 1  # ref=오늘 ∧ OPEN = 같은 날 재-emit(정상) → 불변
            else:
                # STALE(Skipped)·미발견·기타 — 재투입(ref 비움).
                if handoff_status is None:
                    log("WARN", f"reconcile: row {page_id} 의 ref={ref} handoff page "
                                f"미발견(고아) — ref 비우고 재투입")
                    stats["orphaned"] += 1
                else:
                    stats["reverted"] += 1
                notion_api_request(
                    "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                    body={"properties": {PROP_HANDOFF_REF: {"rich_text": []}}})
        except NotionHandoffError as e:
            log("WARN", f"reconcile PATCH 실패(다음 emit 재시도) page={page_id}: "
                        f"{truncate(str(e), 120)}")
        time.sleep(0.34)
    if any(stats.values()):
        log("INFO", "Handoff Ref reconcile: "
                    f"CONSUMED 마감 {stats['cleaned']}건 · STALE 재투입 {stats['reverted']}건 · "
                    f"고아 재투입 {stats['orphaned']}건 · 유지 {stats['kept']}건")
    return stats


def notion_mark_rows_handoff_ref(token: str, rows: list[dict[str, Any]],
                                 handoff_ref: str) -> tuple[int, int]:
    """emit 표시(멱등성 v2) — handoff 에 포함된 row 에 `Handoff Ref` 를 기록한다.

    Status 는 New 유지(상태기계 §3.2). dedupe 전 전체 row 대상 — dedup 으로 payload
    에서 빠진 중복 row 도 이 handoff 가 '가져간' 것이므로 함께 표시한다(CONSUMED 시
    reconcile 이 함께 마감 — 중복 row 의 영구 New 잔존 방지). per-row 실패는 경고 후
    계속(그 row 는 ref 없음 = v1 동작 폴백 — 다음 emit 재포함, 누락 없음).
    반환=(성공, 실패) 건수.
    """
    ok = failed = 0
    for row in rows:
        page_id = row.get("page_id", "")
        if not page_id:
            failed += 1
            continue
        try:
            notion_api_request(
                "PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id), token,
                body={"properties": {
                    PROP_HANDOFF_REF: {"rich_text": _rich_text(handoff_ref)},
                }})
            ok += 1
        except NotionHandoffError as e:
            failed += 1
            log("WARN", f"Handoff Ref 기록 실패(해당 row 는 v1 동작 폴백) "
                        f"page={page_id}: {truncate(str(e), 120)}")
        time.sleep(0.34)
    if failed:
        log("WARN", f"Handoff Ref 기록: 성공 {ok}건 / 실패 {failed}건 (ref={handoff_ref})")
    elif ok:
        log("INFO", f"Handoff Ref 기록 완료: {ok}건 (ref={handoff_ref})")
    return ok, failed


def notion_archive_page_children(token: str, page_id: str) -> None:
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    start_cursor: str | None = None
    archived = 0
    while True:
        req_url = url
        if start_cursor:
            req_url = f"{url}?start_cursor={urllib.parse.quote(start_cursor)}"
        data = notion_api_request("GET", req_url, token)
        for block in data.get("results", []):
            block_id = block.get("id")
            if not block_id:
                continue
            notion_api_request("PATCH", f"https://api.notion.com/v1/blocks/{block_id}",
                               token, body={"archived": True})
            archived += 1
            time.sleep(0.34)
        if not data.get("has_more"):
            break
        start_cursor = data.get("next_cursor")
    log("INFO", f"Routine handoff 기존 blocks archive 완료: {archived}개")


def notion_append_page_children(token: str, page_id: str,
                                blocks: list[dict[str, Any]]) -> None:
    url = NOTION_BLOCK_CHILDREN_URL_TPL.format(block_id=page_id)
    for i in range(0, len(blocks), 90):
        notion_api_request("PATCH", url, token, body={"children": blocks[i:i + 90]})
        time.sleep(0.34)


_NOTION_CHILDREN_CREATE_LIMIT = 90  # 요청당 100 한도 방어(단계 D, Codex P2)


def notion_upsert_routine_handoff(token: str, db_id: str,
                                  payload: dict[str, Any],
                                  generated_at: datetime,
                                  compact: bool = False) -> tuple[str, str]:
    """New-only handoff page 를 생성/갱신하고 (page_id, page_url) 반환.

    compact=True(v2) 면 payload JSON 을 compact 직렬화한다. children 이 한도(90)를
    넘으면 페이지 생성 후 append chunk 경로로 분할 전송(create 한 번에 100 초과 방지).
    """
    props = _handoff_page_properties(payload, generated_at)
    blocks = _handoff_blocks(payload, compact=compact)
    # K4-1: 새 OPEN 생성/갱신 전, 직전 미소비 OPEN handoff 를 STALE 봉인('항상 OPEN 1개').
    # 개별 Intake row Status 는 불변 — handoff page 자신의 Name·Status 만 바꾼다.
    notion_stale_prior_open_handoffs(
        token, db_id,
        keep_handoff_id=payload["handoff_id"],
        superseded_by=payload.get("run_date_kst") or payload["handoff_id"].split("::", 1)[-1],
    )
    existing = notion_find_handoff_page(token, db_id, payload["handoff_id"])
    if existing:
        # Codex P1 revive 가드(멱등성 v2 한정): 이미 CONSUMED(Processed)/STALE(Skipped)
        # 로 종결된 handoff page 를 재패치하면 Status 가 New 로 부활해 재소비(중복
        # 발행) 경로가 열린다. emit 진입 시 종결 확인 후 도달했으므로 여기 걸리면
        # 그 사이 Routine 이 소비한 경합 — 조용히 덮지 않고 실패로 표면화한다
        # (row ref 미기록 상태로 중단 → 다음 emit 이 자동 정상화). v1(flag off)은
        # 기존 재패치 동작 그대로(현행 운영 불변).
        if _enable_handoff_idempotency_v2():
            existing_status = _intake_page_snapshot(existing)["status"]
            if existing_status in ("Processed", "Skipped"):
                raise NotionHandoffError(
                    f"P1 revive 가드: handoff {payload['handoff_id']} 가 이미 "
                    f"'{existing_status}' 종결 — 재기록(부활) 금지. emit 중 Routine "
                    f"소비 경합 의심, 다음 emit 에서 자동 정상화됩니다.")
        page_id = existing["id"]
        notion_archive_page_children(token, page_id)
        notion_append_page_children(token, page_id, blocks)  # 이미 90 단위 분할
        notion_api_request("PATCH", NOTION_PAGE_URL_TPL.format(page_id=page_id),
                           token, body={"properties": props})
        page_url = existing.get("url", "")
        log("INFO", f"Routine handoff 갱신 완료: {page_url or page_id}")
        return page_id, page_url

    # 생성: children ≤90 이면 한 번에(v1 기존 동작 유지), >90 이면 첫 90 + 나머지 append.
    head = blocks[:_NOTION_CHILDREN_CREATE_LIMIT]
    tail = blocks[_NOTION_CHILDREN_CREATE_LIMIT:]
    body = {"parent": {"database_id": db_id}, "properties": props, "children": head}
    created = notion_api_request("POST", NOTION_PAGES_URL, token, body=body)
    page_id = created.get("id", "")
    page_url = created.get("url", "")
    if tail:
        notion_append_page_children(token, page_id, tail)
    log("INFO", f"Routine handoff 생성 완료: {page_url or page_id} (blocks={len(blocks)})")
    return page_id, page_url


def emit_routine_handoff(token: str, db_id: str, run_date: date,
                         window_days: int,
                         generated_at: datetime,
                         source_names: set[str] | None = None,
                         doc_ids: set[str] | None = None,
                         inmemory_raw: dict[str, dict[str, Any]] | None = None,
                         display_window_days: int | None = None,
                         web_brief_dir: str | None = None
                         ) -> tuple[int, str]:
    # B1 조회/표시 분리: window_days(조회 lookback, 기본 30 — 미소비 New 누락 방지
    # 안전망)와 payload 의 window_start~window_end 는 역할이 다르다. 후자는 v16
    # 프롬프트가 발행 브리프의 "검색 기간" 속성으로 그대로 렌더하므로 발행 cadence
    # (수집 윈도우, 주간 7일)를 유지해야 한다 — 프롬프트의 "지난 7일" 문구와 정합.
    # display_window_days 미지정 시 window_days 사용(기존 호출 호환).
    payload_window_days = (display_window_days if display_window_days is not None
                           else window_days)
    # 멱등성 v2(PL-10b/B1 근본해결): 소비 쿼리 **전에** ① 직전 OPEN handoff 를 STALE
    # 봉인하며 그 미발행 row 의 ref 를 비우고(B1 revert — 이번 emit 에 즉시 재투입)
    # ② reconcile sweep 으로 CONSUMED 마감/잔존 ref 를 정리한다. 순서가 뒤면 STALE
    # 된 handoff 의 row 가 하루 늦게 재투입된다. upsert 내부의 K4-1 가드는 그대로
    # 두되(여기서 이미 봉인됐으므로 no-op), v1(flag off)은 이 블록 전체를 건너뛴다.
    idem_v2 = _enable_handoff_idempotency_v2()
    handoff_id = f"routine-handoff::{run_date.isoformat()}"
    current_handoff_open = True
    if idem_v2:
        notion_stale_prior_open_handoffs(
            token, db_id, keep_handoff_id=handoff_id,
            superseded_by=run_date.isoformat(), revert_refs=True)
        # Codex P1: 오늘 handoff 의 종결 여부를 소비 쿼리 전에 확인 — 이미
        # CONSUMED(Processed)/STALE(Skipped)면 잔존 New(ref=오늘)는 reconcile 이
        # 마감/재투입하고, page 재기록·재유입·ref 기록은 전부 생략한다(아래).
        current_page = notion_find_handoff_page(token, db_id, handoff_id)
        current_status = (_intake_page_snapshot(current_page)["status"]
                          if current_page else None)
        current_handoff_open = current_status in (None, "New")
        try:
            notion_reconcile_handoff_refs(token, db_id,
                                          current_handoff_id=handoff_id)
        except NotionHandoffError as e:
            # reconcile 은 위생 단계 — 실패해도 중복/누락이 생기지 않는다(ref 가 남은
            # row 는 소비 쿼리에서 제외된 채 다음 emit 의 sweep 이 재처리). emit 계속.
            log("WARN", f"Handoff Ref reconcile 실패(다음 emit 재시도): "
                        f"{truncate(str(e), 160)}")
        if not current_handoff_open:
            # P1: 같은 날 재실행인데 오늘 handoff 가 이미 종결 — 발행 기록(payload)
            # 보존을 위해 page 를 다시 쓰지 않는다(부활 금지). 이번 실행의 신규 row 는
            # ref 미기록(=비어있음)으로 남아 다음 emit 의 handoff 에 정상 합류한다.
            # (여기서 ref 를 기록하면 다음 reconcile 이 미발행분을 CONSUMED 마감해
            # 침묵 누락이 되므로 기록하지 않는 것이 정확하다.)
            log("INFO", f"오늘 handoff({handoff_id})가 이미 '{current_status}' 종결 — "
                        f"재기록/재유입 없이 종료(신규 row 는 다음 emit 대기)")
            return 0, (current_page or {}).get("url", "")
    rows = notion_query_new_intake_rows(token, db_id, run_date, window_days,
                                        source_names=source_names,
                                        doc_ids=doc_ids,
                                        current_handoff_id=(handoff_id if idem_v2
                                                            else None),
                                        current_handoff_open=current_handoff_open)
    if _enable_handoff_v2():
        # K2-prep: dedupe → 하이브리드 raw 부착(메모리 우선) → scaffold v2 payload.
        # inmemory_raw 는 main() 가 당일 수집 IntakeItem.raw_payload 로 전달(K3 G2 와이어링).
        # 당일분은 메모리 적중(fetch 0), 과거 누적 New row 만 page children fetch 폴백.
        enriched, _stats = enrich_rows_with_raw(token, rows, inmemory_raw=inmemory_raw)
        payload = build_routine_handoff_payload_v2(enriched, run_date,
                                                   payload_window_days, generated_at)
        _pid, page_url = notion_upsert_routine_handoff(token, db_id, payload,
                                                       generated_at, compact=True)
        log("INFO", f"Routine handoff v2 생성(ENABLE_HANDOFF_V2): rows={payload['row_count']}")
        # §1-B 영구배선: raw 가 살아있는 이 지점(enriched)에서 빈슬롯 web brief 를 결정론
        # 산출한다(handoff 와 동일 cards·소스). 비파괴·비차단 — 실패해도 handoff/수집은 계속.
        if web_brief_dir:
            try:
                web_path = emit_web_brief_file(enriched, run_date,
                                               payload_window_days, web_brief_dir)
                log("INFO", f"빈슬롯 web brief 산출(§1-B): {web_path}")
            except Exception as e:  # noqa: BLE001 — web brief 실패가 수집을 죽이면 안 됨
                log("WARN", f"빈슬롯 web brief 산출 실패(handoff 계속): "
                            f"{truncate(str(e), 160)}")
    else:
        # 기존 v1 경로 — scheduled 운영 기본. 바이트 동일 보장(변경 없음).
        payload = build_routine_handoff_payload(rows, run_date,
                                                payload_window_days, generated_at)
        _pid, page_url = notion_upsert_routine_handoff(token, db_id, payload, generated_at)
    if idem_v2:
        # emit 표시는 handoff page 확정(upsert 성공) **후** — page 없는 ref 가 생기지
        # 않게 한다. 대상은 dedupe 전 전체 rows(중복 row 도 이 handoff 가 가져감).
        notion_mark_rows_handoff_ref(token, rows, handoff_id)
    return payload["row_count"], page_url


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────


def insert_items(token: str, db_id: str, items: Iterable[IntakeItem],
                 run_date: date, collected_at: datetime,
                 existing_ids: set[str], dry_run: bool) -> tuple[int, int, int]:
    """삽입 실행. 반환: (inserted, skipped, failed)"""
    inserted = 0
    skipped = 0
    failed = 0
    for item in items:
        if not item.document_id:
            log("WARN", f"document_id 없음 — skip (source={item.source})")
            continue
        # dedupe key = "source::document_id" (source 포함으로 FR/Recall ID 충돌 방지)
        dedup_key = f"{item.source}::{item.document_id}"
        if dedup_key in existing_ids:
            skipped += 1
            continue
        if dry_run:
            log("INFO", f"[DRY] insert source={item.source} id={item.document_id} "
                       f"date={item.date_iso} rel={item.qa_relevance} head={truncate(item.headline, 60)}")
            inserted += 1
            existing_ids.add(dedup_key)
            continue
        # Notion rate limit 방어: 삽입 간 최소 0.34s 지연 (≤ 3 req/s)
        time.sleep(0.34)
        ok = notion_create_page(token, db_id, item, run_date, collected_at)
        if ok:
            inserted += 1
            existing_ids.add(dedup_key)
        else:
            failed += 1
            log("WARN", f"insert 최종 실패 — 다음 항목으로 진행 doc={item.document_id}")
    return inserted, skipped, failed


_ALL_SOURCES = ["fr", "recall", "ema", "mhra", "pics", "eca", "wl"]
# ich/mfds 는 opt-in feature flag 소스라 _ALL_SOURCES(기본 all)엔 넣지 않되,
# --sources 선택지와 handoff source 매핑에는 포함한다.
_SOURCE_CHOICES = _ALL_SOURCES + ["mfds", "ich", "who", "hc", "fda483", "none"]
_SOURCE_TOKEN_TO_NOTION = {
    "fr": SOURCE_FR,
    "recall": SOURCE_RECALL,
    "ema": SOURCE_EMA,
    "mhra": SOURCE_MHRA,
    "pics": SOURCE_PICS,
    "eca": SOURCE_ECA,
    "wl": SOURCE_FDA_WL,
    "mfds": SOURCE_MFDS,
    "ich": SOURCE_ICH,
    "who": SOURCE_WHO,
    "hc": SOURCE_HC,
    "fda483": SOURCE_FDA_483,
}


def _health_payload(
    *,
    health: HealthCheckResult,
    stats: CollectionStats,
    run_date: date,
    start: date,
    end: date,
    event_name: str,
    dry_run: bool,
    requested_sources: list[str],
    active: set[str],
    flags: dict[str, bool],
    handoff_emitted: bool,
    handoff_failed: bool,
    handoff_row_count: int,
    handoff_url: str,
    handoff_error_msg: str,
    handoff_window_days: int = 0,
    aged_unconsumed_new: int = 0,
) -> dict[str, Any]:
    return {
        "schema_version": "grm-health/v1",
        "generated_at_kst": now_kst().isoformat(),
        "run_date_kst": run_date.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "event_name": event_name,
        "dry_run": dry_run,
        "requested_sources": requested_sources,
        "active_sources": sorted(active),
        "flags": flags,
        "health": health.to_dict(),
        "sources": _source_health_rows(stats),
        "handoff": {
            "emitted": handoff_emitted,
            "failed": handoff_failed,
            "row_count": handoff_row_count,
            "url": handoff_url,
            "error_msg": handoff_error_msg,
            "window_days": handoff_window_days,
            "aged_unconsumed_new": aged_unconsumed_new,   # B1: 윈도우 밖 미소비 New(하한)
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GRM API Intake Collector v15.1")
    parser.add_argument("--dry-run", action="store_true",
                        help="Notion 호출 없이 stdout 만 출력")
    parser.add_argument("--window-days", type=int, default=7,
                        choices=range(1, 91), metavar="N(1-90)",
                        help="수집 윈도우 일수 1~90 (default 7)")
    parser.add_argument("--sources", nargs="+", choices=_SOURCE_CHOICES,
                        default=None,
                        help="수집할 소스 선택 (기본: all). 예: --sources fr recall ema. "
                             "none은 feature-flag collector만 단독 실행")
    parser.add_argument("--emit-routine-handoff", action="store_true",
                        help="수집 후 Status=New row만 담은 Routine handoff 페이지를 Notion에 생성/갱신")
    parser.add_argument("--handoff-window-days", type=int, default=None,
                        choices=range(1, 91), metavar="N(1-90)",
                        help="Routine handoff 조회 윈도우. 기본 GRM_HANDOFF_WINDOW_DAYS"
                             "(미설정 시 30일 — 발행 cadence 초과로 미소비 New 누락 방지, B1)")
    parser.add_argument("--handoff-doc-ids", nargs="+", default=None,
                        help="검증/재처리용: 지정한 Document ID만 Routine handoff에 포함")
    args = parser.parse_args()
    requested_sources = args.sources or _ALL_SOURCES
    explicit_sources = args.sources is not None
    active = set() if "none" in requested_sources else set(requested_sources)

    notion_token = os.environ.get("NOTION_TOKEN", "").strip()
    notion_db = os.environ.get("NOTION_DATABASE_ID", "").strip()
    openfda_key = os.environ.get("OPENFDA_API_KEY", "").strip() or None
    data_go_kr_key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    data_go_kr_service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    law_go_kr_oc = os.environ.get("LAW_GO_KR_OC", "").strip()
    enable_search = env_flag("ENABLE_SEARCH")
    enable_mfds = env_flag("ENABLE_MFDS")
    enable_mfds_law = env_flag("ENABLE_MFDS_LAW")
    enable_mfds_recall = env_flag("ENABLE_MFDS_RECALL")
    enable_mfds_admin = env_flag("ENABLE_MFDS_ADMIN")
    enable_mfds_gmp_cert = env_flag("ENABLE_MFDS_GMP_CERT")
    enable_mfds_safety_letter = env_flag("ENABLE_MFDS_SAFETY_LETTER")
    enable_mfds_gmp_inspection = env_flag("ENABLE_MFDS_GMP_INSPECTION")
    enable_ich = env_flag("ENABLE_ICH") or "ich" in active
    enable_who = env_flag("ENABLE_WHO") or "who" in active
    enable_hc = env_flag("ENABLE_HC") or "hc" in active
    enable_fda483 = env_flag("ENABLE_FDA_483") or "fda483" in active
    enable_fda483_observations = env_flag("ENABLE_FDA_483_OBSERVATIONS")
    enable_moleg_api = env_flag("ENABLE_MOLEG_API")
    enable_scrape = env_flag("ENABLE_SCRAPE")
    event_name = os.environ.get("GRM_EVENT_NAME", "").strip()
    health_json_path = os.environ.get("GRM_HEALTH_JSON", "grm-health.json").strip()
    if enable_scrape:
        log("WARN", "ENABLE_SCRAPE=true 이지만 Web Scrape 수집기는 아직 미구현 — 건너뜀")

    if not args.dry_run:
        if not notion_token or not notion_db:
            log("ERROR", "NOTION_TOKEN / NOTION_DATABASE_ID 환경변수 필요")
            return 2

    # Modality 기록 활성 시 스키마 preflight — 속성 미생성/타입 불일치면 이번 실행은
    # Modality 기록만 끄고 수집은 계속(graceful degrade). preflight 는 read-only(GET)이므로
    # dry-run 에서도 토큰/DB 가 있으면 수행해, 활성화 전 검증 루프로 쓸 수 있게 한다.
    modality_requested = env_flag("ENABLE_MODALITY_TAG")
    modality_preflight_disabled = False
    modality_preflight_skipped = False
    if modality_requested and notion_token and notion_db:
        if not notion_verify_modality_property(notion_token, notion_db):
            modality_preflight_disabled = True
            os.environ["ENABLE_MODALITY_TAG"] = "false"
            log("WARN", "ENABLE_MODALITY_TAG=true 이나 'Modality' 스키마 불일치 — "
                        "이번 실행은 Modality 태그를 건너뜁니다(수집은 계속).")
    elif modality_requested:
        # 토큰/DB 없이 요청만 된 경우(예: 자격증명 없는 로컬 dry-run) — preflight 미수행.
        # 실제 기록도 자격증명 없이는 불가하므로 EFFECTIVE 를 false 로 두어 오해를 막는다.
        modality_preflight_skipped = True
        log("WARN", "ENABLE_MODALITY_TAG=true 이나 NOTION 자격증명이 없어 preflight 생략 — "
                    "Modality 태그 기록은 자격증명+속성이 있을 때만 동작(EFFECTIVE=false).")
    modality_effective = (modality_requested and not modality_preflight_disabled
                          and not modality_preflight_skipped)

    # 멱등성 v2 활성 시 'Handoff Ref' 스키마 preflight — 부재/타입 불일치면 이번 실행은
    # v2 만 끄고 v1(날짜 윈도우+K4-1)로 graceful degrade(Modality preflight 선례 패턴).
    handoff_idem_requested = env_flag("ENABLE_HANDOFF_IDEMPOTENCY_V2")
    handoff_idem_preflight_disabled = False
    handoff_idem_preflight_skipped = False
    if handoff_idem_requested and notion_token and notion_db:
        if not notion_verify_handoff_ref_property(notion_token, notion_db):
            handoff_idem_preflight_disabled = True
            os.environ["ENABLE_HANDOFF_IDEMPOTENCY_V2"] = "false"
            log("WARN", "ENABLE_HANDOFF_IDEMPOTENCY_V2=true 이나 'Handoff Ref' 스키마 "
                        "불일치 — 이번 실행은 v1(날짜 윈도우) 경로로 폴백합니다(수집은 계속).")
    elif handoff_idem_requested:
        handoff_idem_preflight_skipped = True
        log("WARN", "ENABLE_HANDOFF_IDEMPOTENCY_V2=true 이나 NOTION 자격증명이 없어 "
                    "preflight 생략 — 멱등성 v2 는 자격증명+속성이 있을 때만 동작.")
    handoff_idem_effective = (handoff_idem_requested
                              and not handoff_idem_preflight_disabled
                              and not handoff_idem_preflight_skipped)

    now_k = now_kst()
    run_date = kst_run_date(now_k)
    start, end = date_window(run_date, args.window_days)
    log("INFO", f"실행일(KST)={run_date}  window={start}~{end}  dry_run={args.dry_run}")

    # ── P0 개선: data.go.kr 지연공개 대응 윈도우 ───────────────────────────────
    # 회수·행정처분은 사건일(회수명령일·최종처분일) 기준으로 윈도우를 거른다. 그런데
    # data.go.kr은 과거 일자 항목을 뒤늦게 일괄 공개하는 경우가 많아, 기본 7일 윈도우
    # 밖으로 빠지면 매일 돌려도 (날짜가 과거라) 영구 누락된다.
    # → 이 두 소스만 수집 윈도우를 넓혀 backfill하고, 중복은 dedup_window_days가 막는다.
    #    handoff는 Run Date(수집일) 기준 필터이므로, 넓은 윈도우로 오늘 새로 잡힌 과거
    #    일자 항목도 Run Date=오늘이 되어 Routine까지 정상 전달된다.
    mfds_enforcement_window_days = max(
        args.window_days, _env_int("MFDS_ENFORCEMENT_WINDOW_DAYS", 30))
    enf_start = run_date - timedelta(days=mfds_enforcement_window_days)
    log("INFO", f"MFDS enforcement window={enf_start}~{end} "
                f"({mfds_enforcement_window_days}일, 회수·행정처분 지연공개 대응)")

    stats = CollectionStats()

    # ── Phase 1: Official API ──────────────────────────────────────────────
    fr_items: list[IntakeItem] = []
    if "fr" in active:
        fr_items, fr_err = collect_federal_register(start, end)
        stats.fr_fetched = len(fr_items)
        if fr_err:
            stats.fr_error = True
            stats.fr_error_msg = fr_err
            if "truncated" in fr_err:
                stats.fr_truncated = True

    recall_items: list[IntakeItem] = []
    if "recall" in active:
        recall_items, rec_err = collect_openfda_recalls(start, end, openfda_key)
        stats.recall_fetched = len(recall_items)
        if rec_err:
            stats.recall_error = True
            stats.recall_error_msg = rec_err
            if "truncated" in rec_err:
                stats.recall_truncated = True

    # ── Phase 2: RSS / HTML (v15.1) ───────────────────────────────────────
    ema_items: list[IntakeItem] = []
    if "ema" in active:
        ema_items, ema_err = collect_ema_rss(start, end)
        stats.ema_fetched = len(ema_items)
        if ema_err:
            stats.ema_error = True
            stats.ema_error_msg = ema_err

    mhra_items: list[IntakeItem] = []
    if "mhra" in active:
        mhra_items, mhra_err = collect_mhra_rss(start, end)
        stats.mhra_fetched = len(mhra_items)
        if mhra_err:
            stats.mhra_error = True
            stats.mhra_error_msg = mhra_err

    pics_items: list[IntakeItem] = []
    if "pics" in active:
        pics_items, pics_err = collect_pics_rss(start, end)
        stats.pics_fetched = len(pics_items)
        if pics_err:
            stats.pics_error = True
            stats.pics_error_msg = pics_err

    eca_items: list[IntakeItem] = []
    if "eca" in active:
        eca_items, eca_err = collect_eca_rss(start, end)
        stats.eca_fetched = len(eca_items)
        if eca_err:
            stats.eca_error = True
            stats.eca_error_msg = eca_err

    wl_items: list[IntakeItem] = []
    if "wl" in active:
        wl_items, wl_err = collect_fda_warning_letters(start, end)
        stats.wl_fetched = len(wl_items)
        # P1: 본문 excerpt 실패는 graceful(메타 카드 유지) — warning 표면화용 집계.
        wl_body_health = LAST_WL_HEALTH.get("wl_body") or {}
        stats.wl_body_attempted = int(wl_body_health.get("attempted") or 0)
        stats.wl_body_failed = int(wl_body_health.get("failed") or 0)
        if wl_err:
            stats.wl_error = True
            stats.wl_error_msg = wl_err

    # ── Phase 2b: MFDS (ENABLE_MFDS=true 시 실행, 기본 false) ───────────────
    mfds_items: list[IntakeItem] = []
    if enable_mfds:
        log("INFO", "=== MFDS 수집 시작 ===")
        if data_go_kr_key and not enable_moleg_api:
            log("INFO", "DATA_GO_KR_KEY 존재하지만 ENABLE_MOLEG_API=false - ogLmPp 호출 없이 RSS primary 사용")
        try:
            from collect_mfds import collect_mfds
            mfds_key = data_go_kr_key if enable_moleg_api else None
            mfds_items, mfds_err = collect_mfds(start, end, mfds_key)
        except Exception as e:
            mfds_items, mfds_err = [], str(e)
        stats.mfds_fetched = len(mfds_items)
        if mfds_err:
            stats.mfds_error = True
            stats.mfds_error_msg = mfds_err
            log("WARN", f"MFDS 오류: {mfds_err}")
    else:
        log("INFO", "ENABLE_MFDS=false — MFDS 수집 건너뜀")

    # ── MFDS law/admrul official API replacements (ENABLE_MFDS_LAW=true) ─────
    mfds_law_items: list[IntakeItem] = []
    if enable_mfds_law:
        log("INFO", "=== MFDS 법제처 행정규칙·법령 API 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_law_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_law_items = []
        else:
            try:
                from collect_mfds_law import collect_mfds_law
                mfds_law_items, mfds_law_err = collect_mfds_law(
                    start, end, data_go_kr_service_key, law_go_kr_oc=law_go_kr_oc)
            except Exception as e:
                mfds_law_items, mfds_law_err = [], str(e)
        stats.mfds_law_fetched = len(mfds_law_items)
        if mfds_law_err:
            stats.mfds_law_error = True
            stats.mfds_law_error_msg = mfds_law_err
            log("WARN", f"MFDS Law 오류: {mfds_law_err}")
    else:
        log("INFO", "ENABLE_MFDS_LAW=false — MFDS 법제처 행정규칙·법령 API 수집 건너뜀")

    # ── Phase 2c: MFDS Recall / Self-Check (ENABLE_MFDS_RECALL=true 시 실행) ─
    mfds_recall_items: list[IntakeItem] = []
    if enable_mfds_recall:
        log("INFO", "=== MFDS 회수·판매중지 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_recall_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_recall_items = []
        else:
            try:
                from collect_mfds_recall import collect_mfds_recall
                # P0: 지연공개 대응 — 넓은 enforcement 윈도우 사용
                mfds_recall_items, mfds_recall_err = collect_mfds_recall(
                    enf_start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_recall_items, mfds_recall_err = [], str(e)
        stats.mfds_recall_fetched = len(mfds_recall_items)
        if mfds_recall_err:
            stats.mfds_recall_error = True
            stats.mfds_recall_error_msg = mfds_recall_err
            log("WARN", f"MFDS Recall 오류: {mfds_recall_err}")
    else:
        log("INFO", "ENABLE_MFDS_RECALL=false — MFDS 회수·판매중지 수집 건너뜀")

    # ── Phase 2c: MFDS Administrative Actions (ENABLE_MFDS_ADMIN=true 시 실행) ─
    mfds_admin_items: list[IntakeItem] = []
    if enable_mfds_admin:
        log("INFO", "=== MFDS 행정처분 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_admin_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_admin_items = []
        else:
            try:
                from collect_mfds_admin_action import collect_mfds_admin_actions
                # P0: 지연공개 대응 — 넓은 enforcement 윈도우 사용
                mfds_admin_items, mfds_admin_err = collect_mfds_admin_actions(
                    enf_start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_admin_items, mfds_admin_err = [], str(e)
        stats.mfds_admin_fetched = len(mfds_admin_items)
        if mfds_admin_err:
            stats.mfds_admin_error = True
            stats.mfds_admin_error_msg = mfds_admin_err
            log("WARN", f"MFDS Admin 오류: {mfds_admin_err}")
    else:
        log("INFO", "ENABLE_MFDS_ADMIN=false — MFDS 행정처분 수집 건너뜀")

    # ── MFDS GMP Certificate Status (ENABLE_MFDS_GMP_CERT=true 시 실행) ─────
    mfds_gmp_cert_items: list[IntakeItem] = []
    if enable_mfds_gmp_cert:
        log("INFO", "=== MFDS GMP 적합판정서 발급현황 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_gmp_cert_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_gmp_cert_items = []
        else:
            try:
                from collect_mfds_gmp_cert import collect_mfds_gmp_certs
                mfds_gmp_cert_items, mfds_gmp_cert_err = collect_mfds_gmp_certs(
                    start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_gmp_cert_items, mfds_gmp_cert_err = [], str(e)
        stats.mfds_gmp_cert_fetched = len(mfds_gmp_cert_items)
        if mfds_gmp_cert_err:
            stats.mfds_gmp_cert_error = True
            stats.mfds_gmp_cert_error_msg = mfds_gmp_cert_err
            log("WARN", f"MFDS GMP Certificate 오류: {mfds_gmp_cert_err}")
    else:
        log("INFO", "ENABLE_MFDS_GMP_CERT=false — MFDS GMP 적합판정서 수집 건너뜀")

    # ── MFDS Safety Letter API (ENABLE_MFDS_SAFETY_LETTER=true 시 실행) ─────
    mfds_safety_letter_items: list[IntakeItem] = []
    if enable_mfds_safety_letter:
        log("INFO", "=== MFDS 안전성서한 API 수집 시작 ===")
        if not data_go_kr_service_key:
            mfds_safety_letter_err = "DATA_GO_KR_SERVICE_KEY 환경변수 필요"
            mfds_safety_letter_items = []
        else:
            try:
                from collect_mfds_safety_letter import collect_mfds_safety_letters
                mfds_safety_letter_items, mfds_safety_letter_err = collect_mfds_safety_letters(
                    start, end, data_go_kr_service_key)
            except Exception as e:
                mfds_safety_letter_items, mfds_safety_letter_err = [], str(e)
        stats.mfds_safety_letter_fetched = len(mfds_safety_letter_items)
        if mfds_safety_letter_err:
            stats.mfds_safety_letter_error = True
            stats.mfds_safety_letter_error_msg = mfds_safety_letter_err
            log("WARN", f"MFDS Safety Letter 오류: {mfds_safety_letter_err}")
    else:
        log("INFO", "ENABLE_MFDS_SAFETY_LETTER=false — MFDS 안전성서한 API 수집 건너뜀")

    # ── Phase 2d: MFDS GMP Inspection Results (ENABLE_MFDS_GMP_INSPECTION=true 시 실행) ─
    mfds_gmp_inspection_items: list[IntakeItem] = []
    if enable_mfds_gmp_inspection:
        log("INFO", "=== MFDS GMP 실태조사 결과 수집 시작 ===")
        try:
            import collect_mfds_gmp_inspection as mfds_gmp_module
            mfds_gmp_inspection_items, mfds_gmp_inspection_err = mfds_gmp_module.collect_mfds_gmp_inspections(
                start, end)
            gmp_health = getattr(mfds_gmp_module, "LAST_HEALTH", {}) or {}
        except Exception as e:
            mfds_gmp_inspection_items, mfds_gmp_inspection_err = [], str(e)
            gmp_health = {}
        stats.mfds_gmp_inspection_fetched = len(mfds_gmp_inspection_items)
        stats.mfds_gmp_inspection_parse_status = dict(gmp_health.get("parse_status_counts") or {})
        stats.mfds_gmp_inspection_deficiency = dict(gmp_health.get("deficiency_counts") or {})
        stats.mfds_gmp_inspection_manual_review = int(gmp_health.get("manual_review_count") or 0)
        stats.mfds_gmp_inspection_page_warnings = list(gmp_health.get("page_warnings") or [])
        # [상세보기 결정론 승격 2026-07-02] 지적 표 추출 관측(degrade 는 요약카드 유지 — 비차단).
        deficiency_table_health = gmp_health.get("deficiency_table") or {}
        stats.gmp_deficiency_table_enabled = bool(deficiency_table_health.get("enabled"))
        stats.gmp_deficiency_table_attempted = int(deficiency_table_health.get("attempted") or 0)
        stats.gmp_deficiency_table_extracted = int(deficiency_table_health.get("extracted") or 0)
        stats.gmp_deficiency_table_failed = int(deficiency_table_health.get("failed") or 0)
        stats.gmp_deficiency_table_warnings = list(deficiency_table_health.get("warnings") or [])
        if stats.gmp_deficiency_table_failed:
            log("WARN", "MFDS GMP 지적 표 추출 degrade "
                        f"{stats.gmp_deficiency_table_failed}건(요약카드 유지): "
                        f"{stats.gmp_deficiency_table_warnings}")
        if mfds_gmp_inspection_err:
            stats.mfds_gmp_inspection_error = True
            stats.mfds_gmp_inspection_error_msg = mfds_gmp_inspection_err
            log("WARN", f"MFDS GMP Inspection 오류: {mfds_gmp_inspection_err}")
    else:
        log("INFO", "ENABLE_MFDS_GMP_INSPECTION=false — MFDS GMP 실태조사 결과 수집 건너뜀")

    # ── P1: ICH 직접 모니터링 (ENABLE_ICH=true 시 실행) ──────────────────────
    ich_items: list[IntakeItem] = []
    if enable_ich:
        log("INFO", "=== ICH 수집 시작 ===")
        try:
            from collect_ich import collect_ich
            ich_items, ich_err = collect_ich(run_date)
        except Exception as e:  # noqa: BLE001
            ich_items, ich_err = [], str(e)
        stats.ich_fetched = len(ich_items)
        if ich_err:
            stats.ich_error = True
            stats.ich_error_msg = ich_err
            log("WARN", f"ICH 오류: {ich_err}")
    else:
        log("INFO", "ENABLE_ICH=false — ICH 수집 건너뜀")

    # ── P1: WHO Prequalification (ENABLE_WHO=true 또는 --sources who) ─────────
    who_items: list[IntakeItem] = []
    if enable_who:
        log("INFO", "=== WHO 수집 시작 ===")
        try:
            import collect_who as who_module
            who_items, who_err = who_module.collect_who(start, end)
            who_health = getattr(who_module, "LAST_HEALTH", {}) or {}
        except Exception as e:  # noqa: BLE001
            who_items, who_err = [], str(e)
            who_health = {}
        stats.who_fetched = len(who_items)
        # P1: WHOPIR excerpt 실패/cap 은 graceful(링크 카드 유지) — warning 표면화용 집계.
        whopir_excerpt_health = who_health.get("whopir_excerpt") or {}
        stats.whopir_excerpt_attempted = int(whopir_excerpt_health.get("attempted") or 0)
        stats.whopir_excerpt_failed = int(whopir_excerpt_health.get("failed") or 0)
        stats.whopir_excerpt_capped = int(bool(whopir_excerpt_health.get("capped")))
        if who_err:
            stats.who_error = True
            stats.who_error_msg = who_err
            log("WARN", f"WHO 오류: {who_err}")
    else:
        log("INFO", "ENABLE_WHO=false — WHO 수집 건너뜀")

    # ── P1: Health Canada (ENABLE_HC=true 또는 --sources hc) ─────────────────
    hc_items: list[IntakeItem] = []
    if enable_hc:
        log("INFO", "=== Health Canada 수집 시작 ===")
        try:
            from collect_hc import collect_hc
            # recall/advisory 이므로 지연공개 대비 enforcement 윈도우 사용
            hc_items, hc_err = collect_hc(enf_start, end)
        except Exception as e:  # noqa: BLE001
            hc_items, hc_err = [], str(e)
        stats.hc_fetched = len(hc_items)
        if hc_err:
            stats.hc_error = True
            stats.hc_error_msg = hc_err
            log("WARN", f"HC 오류: {hc_err}")
    else:
        log("INFO", "ENABLE_HC=false — Health Canada 수집 건너뜀")

    # ── WHY-1 #3: FDA 483 (ENABLE_FDA_483=true 또는 --sources fda483) ─────────
    fda483_items: list[IntakeItem] = []
    if enable_fda483:
        log("INFO", "=== FDA 483 수집 시작 ===")
        try:
            import collect_fda_483 as fda483_module
            # 483 은 publish date 지연공개형 → enforcement 윈도우(MFDS_ENFORCEMENT_WINDOW_DAYS) 사용
            fda483_items, fda483_err = fda483_module.collect_fda_483(enf_start, end)
            fda483_health = getattr(fda483_module, "LAST_HEALTH", {}) or {}
        except Exception as e:  # noqa: BLE001
            fda483_items, fda483_err = [], str(e)
            fda483_health = {}
        stats.fda483_fetched = len(fda483_items)
        # P1: 483 excerpt 실패/cap·전수 경로 degrade 는 graceful — warning 표면화용 집계.
        fda483_excerpt_health = fda483_health.get("fda483_excerpt") or {}
        stats.fda483_excerpt_attempted = int(fda483_excerpt_health.get("attempted") or 0)
        stats.fda483_excerpt_failed = int(fda483_excerpt_health.get("failed") or 0)
        stats.fda483_excerpt_capped = int(bool(fda483_excerpt_health.get("capped")))
        stats.fda483_source_degraded = int(bool(fda483_health.get("source_degraded")))
        fda483_obs_health = fda483_health.get("fda_483_observations") or {}
        stats.fda483_observations_enabled = bool(fda483_obs_health.get("enabled"))
        stats.fda483_observations_attempted = int(fda483_obs_health.get("attempted") or 0)
        stats.fda483_observations_extracted = int(fda483_obs_health.get("extracted") or 0)
        stats.fda483_observations_failed = int(fda483_obs_health.get("failed") or 0)
        stats.fda483_observations_warnings = list(fda483_obs_health.get("warnings") or [])
        if fda483_err:
            stats.fda483_error = True
            stats.fda483_error_msg = fda483_err
            log("WARN", f"FDA 483 오류: {fda483_err}")
    else:
        log("INFO", "ENABLE_FDA_483=false — FDA 483 수집 건너뜀")

    total_fetched = (stats.fr_fetched + stats.recall_fetched + stats.ema_fetched
                     + stats.mhra_fetched + stats.pics_fetched
                     + stats.eca_fetched + stats.wl_fetched
                     + stats.mfds_fetched + stats.mfds_law_fetched
                     + stats.mfds_recall_fetched
                     + stats.mfds_admin_fetched + stats.mfds_gmp_cert_fetched
                     + stats.mfds_safety_letter_fetched
                     + stats.mfds_gmp_inspection_fetched
                     + stats.ich_fetched
                     + stats.who_fetched
                     + stats.hc_fetched
                     + stats.fda483_fetched)
    log("INFO", (
        f"수집 완료: FR={stats.fr_fetched} · Recall={stats.recall_fetched} · "
        f"EMA={stats.ema_fetched} · MHRA={stats.mhra_fetched} · "
        f"PICS={stats.pics_fetched} · ECA={stats.eca_fetched} · "
        f"WL={stats.wl_fetched} · MFDS={stats.mfds_fetched} · "
        f"MFDS-Law={stats.mfds_law_fetched} · "
        f"MFDS-Recall={stats.mfds_recall_fetched} · "
        f"MFDS-Admin={stats.mfds_admin_fetched} · "
        f"MFDS-GMPCert={stats.mfds_gmp_cert_fetched} · "
        f"MFDS-SafetyLetter={stats.mfds_safety_letter_fetched} · "
        f"MFDS-GMPInspection={stats.mfds_gmp_inspection_fetched} · "
        f"ICH={stats.ich_fetched} · WHO={stats.who_fetched} · "
        f"HC={stats.hc_fetched} · FDA483={stats.fda483_fetched} · 합계={total_fetched}건"
    ))

    # 3) Notion 기존 row (중복 제거)
    # RAPS_NEWS 등 freshness=pm(31일) 소스가 있으면 dedupe 윈도우를 35일로 확장
    # (7일 윈도우 시 8일 전 RAPS URL이 누락되어 재삽입될 수 있음 — Task #13)
    _SEARCH_DEDUP_WINDOW_DAYS = 35  # pm(31일) + 여유 4일
    # P0: dedup 윈도우는 가장 넓은 수집 윈도우(enforcement 30일)를 반드시 덮어야 한다.
    # 그래야 enforcement 윈도우로 backfill된 과거 일자 항목이 다음 실행에서 중복 재삽입되지 않는다.
    dedup_window_days = max(
        args.window_days,
        mfds_enforcement_window_days,
        _SEARCH_DEDUP_WINDOW_DAYS if enable_search else 0,
    )
    log("INFO", f"dedupe window={dedup_window_days}일 (window_days={args.window_days}, enable_search={enable_search})")

    # P1: snapshot 소스(ICH·WHO)는 날짜 미단언/URL 기반 안정 스냅샷이라 매 실행 동일 항목을 재수집한다.
    # 기본 dedup 윈도우(≤30일)를 넘으면 같은 항목이 다시 New로 들어오므로(월간 중복 삽입),
    # 이들 소스만 별도로 장기(3년) Source-한정 dedup을 조회해 합친다.
    # (전체 윈도우를 3년으로 늘리면 Notion 페이지 cap에 걸릴 수 있어 Source 필터로 한정.)
    _SNAPSHOT_DEDUP_WINDOW_DAYS = 1095
    _SNAPSHOT_SOURCES = {SOURCE_ICH, SOURCE_WHO}

    if args.dry_run:
        existing: set[str] = set()
    else:
        try:
            existing = notion_query_existing_doc_ids(notion_token, notion_db, run_date,
                                                     window_days=dedup_window_days)
        except NotionDedupeQueryError as e:
            # 중복 조회 실패 시 빈 set으로 진행하면 대량 중복 insert 위험 → 중단
            log("ERROR", f"중복 조회 실패 — duplicate insert 방지를 위해 insert 단계 중단: {e}")
            return 1
        snapshot_active = ({SOURCE_ICH} if enable_ich else set()) | ({SOURCE_WHO} if enable_who else set())
        if snapshot_active:
            try:
                snap_existing = notion_query_existing_doc_ids(
                    notion_token, notion_db, run_date,
                    window_days=_SNAPSHOT_DEDUP_WINDOW_DAYS,
                    source_names=snapshot_active)
                existing |= snap_existing
                log("INFO", f"snapshot dedup({sorted(snapshot_active)}) +{len(snap_existing)}건 "
                            f"(최근 {_SNAPSHOT_DEDUP_WINDOW_DAYS}일)")
            except NotionDedupeQueryError as e:
                log("ERROR", f"snapshot dedup 조회 실패 — 중복 삽입 방지를 위해 중단: {e}")
                return 1

    collected_at = now_k

    # 4) 삽입 (반환: inserted, skipped, failed)
    fr_in, fr_sk, fr_fail = insert_items(notion_token, notion_db, fr_items,
                                         run_date, collected_at, existing, args.dry_run)
    stats.fr_inserted = fr_in
    stats.fr_skipped_dup = fr_sk
    stats.fr_insert_failed = fr_fail

    rec_in, rec_sk, rec_fail = insert_items(notion_token, notion_db, recall_items,
                                            run_date, collected_at, existing, args.dry_run)
    stats.recall_inserted = rec_in
    stats.recall_skipped_dup = rec_sk
    stats.recall_insert_failed = rec_fail

    # Phase 2 삽입
    ema_in, ema_sk, ema_fail = insert_items(notion_token, notion_db, ema_items,
                                             run_date, collected_at, existing, args.dry_run)
    stats.ema_inserted = ema_in
    stats.ema_skipped_dup = ema_sk
    stats.ema_insert_failed = ema_fail

    mhra_in, mhra_sk, mhra_fail = insert_items(notion_token, notion_db, mhra_items,
                                                run_date, collected_at, existing, args.dry_run)
    stats.mhra_inserted = mhra_in
    stats.mhra_skipped_dup = mhra_sk
    stats.mhra_insert_failed = mhra_fail

    pics_in, pics_sk, pics_fail = insert_items(notion_token, notion_db, pics_items,
                                                run_date, collected_at, existing, args.dry_run)
    stats.pics_inserted = pics_in
    stats.pics_skipped_dup = pics_sk
    stats.pics_insert_failed = pics_fail

    eca_in, eca_sk, eca_fail = insert_items(notion_token, notion_db, eca_items,
                                             run_date, collected_at, existing, args.dry_run)
    stats.eca_inserted = eca_in
    stats.eca_skipped_dup = eca_sk
    stats.eca_insert_failed = eca_fail

    wl_in, wl_sk, wl_fail = insert_items(notion_token, notion_db, wl_items,
                                          run_date, collected_at, existing, args.dry_run)
    stats.wl_inserted = wl_in
    stats.wl_skipped_dup = wl_sk
    stats.wl_insert_failed = wl_fail

    mfds_in, mfds_sk, mfds_fail = insert_items(notion_token, notion_db, mfds_items,
                                               run_date, collected_at, existing, args.dry_run)
    stats.mfds_inserted = mfds_in
    stats.mfds_skipped_dup = mfds_sk
    stats.mfds_insert_failed = mfds_fail

    mfds_law_in, mfds_law_sk, mfds_law_fail = insert_items(
        notion_token, notion_db, mfds_law_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_law_inserted = mfds_law_in
    stats.mfds_law_skipped_dup = mfds_law_sk
    stats.mfds_law_insert_failed = mfds_law_fail

    mfds_rec_in, mfds_rec_sk, mfds_rec_fail = insert_items(
        notion_token, notion_db, mfds_recall_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_recall_inserted = mfds_rec_in
    stats.mfds_recall_skipped_dup = mfds_rec_sk
    stats.mfds_recall_insert_failed = mfds_rec_fail

    mfds_admin_in, mfds_admin_sk, mfds_admin_fail = insert_items(
        notion_token, notion_db, mfds_admin_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_admin_inserted = mfds_admin_in
    stats.mfds_admin_skipped_dup = mfds_admin_sk
    stats.mfds_admin_insert_failed = mfds_admin_fail

    mfds_gmp_cert_in, mfds_gmp_cert_sk, mfds_gmp_cert_fail = insert_items(
        notion_token, notion_db, mfds_gmp_cert_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_gmp_cert_inserted = mfds_gmp_cert_in
    stats.mfds_gmp_cert_skipped_dup = mfds_gmp_cert_sk
    stats.mfds_gmp_cert_insert_failed = mfds_gmp_cert_fail

    mfds_safety_letter_in, mfds_safety_letter_sk, mfds_safety_letter_fail = insert_items(
        notion_token, notion_db, mfds_safety_letter_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_safety_letter_inserted = mfds_safety_letter_in
    stats.mfds_safety_letter_skipped_dup = mfds_safety_letter_sk
    stats.mfds_safety_letter_insert_failed = mfds_safety_letter_fail

    mfds_gmp_insp_in, mfds_gmp_insp_sk, mfds_gmp_insp_fail = insert_items(
        notion_token, notion_db, mfds_gmp_inspection_items,
        run_date, collected_at, existing, args.dry_run)
    stats.mfds_gmp_inspection_inserted = mfds_gmp_insp_in
    stats.mfds_gmp_inspection_skipped_dup = mfds_gmp_insp_sk
    stats.mfds_gmp_inspection_insert_failed = mfds_gmp_insp_fail

    ich_in, ich_sk, ich_fail = insert_items(
        notion_token, notion_db, ich_items,
        run_date, collected_at, existing, args.dry_run)
    stats.ich_inserted = ich_in
    stats.ich_skipped_dup = ich_sk
    stats.ich_insert_failed = ich_fail

    who_in, who_sk, who_fail = insert_items(
        notion_token, notion_db, who_items,
        run_date, collected_at, existing, args.dry_run)
    stats.who_inserted = who_in
    stats.who_skipped_dup = who_sk
    stats.who_insert_failed = who_fail

    hc_in, hc_sk, hc_fail = insert_items(
        notion_token, notion_db, hc_items,
        run_date, collected_at, existing, args.dry_run)
    stats.hc_inserted = hc_in
    stats.hc_skipped_dup = hc_sk
    stats.hc_insert_failed = hc_fail

    fda483_in, fda483_sk, fda483_fail = insert_items(
        notion_token, notion_db, fda483_items,
        run_date, collected_at, existing, args.dry_run)
    stats.fda483_inserted = fda483_in
    stats.fda483_skipped_dup = fda483_sk
    stats.fda483_insert_failed = fda483_fail

    # ── Phase 2a: Brave Search (ENABLE_SEARCH=true 시 실행) ──────────────────
    # enable_search는 위 dedupe 윈도우 계산 시 이미 정의됨 (재정의 불필요)
    search_items: list[IntakeItem] = []  # G2: inmemory_raw 집계에서 항상 참조 가능하게 선초기화
    if enable_search:
        brave_api_key = os.environ.get("BRAVE_API_KEY", "")
        log("INFO", "=== Brave Search 수집 시작 ===")
        try:
            from collect_search import collect_brave_search
            search_items, search_err = collect_brave_search(brave_api_key)
        except Exception as e:
            search_items, search_err = [], str(e)

        stats.search_fetched = len(search_items)
        if search_err:
            stats.search_error = True
            stats.search_error_msg = search_err
            log("WARN", f"Brave Search 오류: {search_err}")

        src_in, src_sk, src_fail = insert_items(
            notion_token, notion_db, search_items,
            run_date, collected_at, existing, args.dry_run,
        )
        stats.search_inserted = src_in
        stats.search_skipped_dup = src_sk
        stats.search_insert_failed = src_fail
    else:
        log("INFO", "ENABLE_SEARCH=false — Brave Search 건너뜀")

    handoff_emitted = False
    handoff_failed = False
    handoff_row_count = 0
    handoff_url = ""
    handoff_error_msg = ""
    # B1: 윈도우는 emit 여부와 무관하게 결정(노후 미소비 New 경고 기준으로도 사용).
    handoff_window_days = resolve_handoff_window_days(args.handoff_window_days)
    if args.emit_routine_handoff:
        if args.dry_run:
            log("INFO", "--emit-routine-handoff 지정됐지만 dry-run 이므로 Notion handoff 생성 생략")
        elif stats.has_insert_failures():
            handoff_failed = True
            handoff_error_msg = "insert failure가 있어 partial handoff 생성 생략"
            log("ERROR", f"Routine handoff 생성 생략: {handoff_error_msg}")
        else:
            try:
                handoff_sources = None
                if explicit_sources and "none" not in requested_sources:
                    handoff_sources = {
                        _SOURCE_TOKEN_TO_NOTION[src]
                        for src in requested_sources
                        if src in _SOURCE_TOKEN_TO_NOTION
                    }
                handoff_doc_ids = set(args.handoff_doc_ids or []) or None
                # G2 와이어링: 당일 수집분 raw 를 메모리로 전달(과거 New row 만 fetch 폴백).
                # v1 경로(ENABLE_HANDOFF_V2 off)는 inmemory_raw 미사용 → 무영향.
                inmemory_raw = build_inmemory_raw(
                    fr_items, recall_items, ema_items, mhra_items, pics_items, eca_items,
                    wl_items, mfds_items, mfds_law_items, mfds_recall_items,
                    mfds_admin_items, mfds_gmp_cert_items, mfds_safety_letter_items,
                    mfds_gmp_inspection_items, ich_items, who_items, hc_items,
                    fda483_items, search_items)
                # §1-B: web brief emit 활성 시 산출 디렉터리(없으면 None=비활성). raw 가
                # 살아있는 handoff v2 경로 내부에서만 산출(빈슬롯 grm-web-card/v1).
                web_brief_dir = resolve_web_brief_dir() if _enable_web_brief_emit() else None
                handoff_row_count, handoff_url = emit_routine_handoff(
                    notion_token, notion_db, run_date, handoff_window_days, collected_at,
                    source_names=handoff_sources, doc_ids=handoff_doc_ids,
                    inmemory_raw=inmemory_raw,
                    # B1 조회/표시 분리: 브리프 "검색 기간"은 수집 윈도우(주간) 유지.
                    display_window_days=args.window_days,
                    web_brief_dir=web_brief_dir)
                handoff_emitted = True
            except NotionHandoffError as e:
                handoff_failed = True
                handoff_error_msg = str(e)
                log("ERROR", f"Routine handoff 생성 실패: {handoff_error_msg}")

    # B1 임시 방어 ②: 윈도우 밖 미소비 New 카운트(읽기전용 — dry-run 도 자격증명이
    # 있으면 수행해 검증 루프로 쓸 수 있다). 실패는 조용한 0 이 아니라 경고로 표면화.
    aged_unconsumed_new = 0
    aged_new_query_error = ""
    if notion_token and notion_db:
        try:
            aged_unconsumed_new = notion_count_aged_unconsumed_new(
                notion_token, notion_db, run_date, handoff_window_days)
            if aged_unconsumed_new and handoff_idem_effective:
                # Codex P2: v2 effective — 노후 New 는 ref 기반 쿼리가 자동 재투입(정보성).
                log("INFO", f"handoff 윈도우({handoff_window_days}일) 밖 미소비 New row "
                            f"{aged_unconsumed_new}건 — 멱등성 v2 자동 재투입 대상(다음 emit 포함)")
            elif aged_unconsumed_new:
                log("WARN", f"handoff 윈도우({handoff_window_days}일) 밖 미소비 New row "
                            f"{aged_unconsumed_new}건 — Routine 누락/지연 의심")
        except Exception as e:  # noqa: BLE001 — 감시 실패가 수집 자체를 죽이면 안 됨
            aged_new_query_error = str(e)
            log("WARN", f"노후 미소비 New row 카운트 조회 실패: {aged_new_query_error}")

    flags = {
        "ENABLE_SEARCH": enable_search,
        "ENABLE_MFDS": enable_mfds,
        "ENABLE_MFDS_LAW": enable_mfds_law,
        "ENABLE_MFDS_RECALL": enable_mfds_recall,
        "ENABLE_MFDS_ADMIN": enable_mfds_admin,
        "ENABLE_MFDS_GMP_CERT": enable_mfds_gmp_cert,
        "ENABLE_MFDS_SAFETY_LETTER": enable_mfds_safety_letter,
        "ENABLE_MFDS_GMP_INSPECTION": enable_mfds_gmp_inspection,
        "ENABLE_ICH": enable_ich,
        "ENABLE_WHO": enable_who,
        "ENABLE_HC": enable_hc,
        "ENABLE_FDA_483": enable_fda483,
        "ENABLE_FDA_483_OBSERVATIONS": enable_fda483_observations,
        "ENABLE_MOLEG_API": enable_moleg_api,
        "ENABLE_SCRAPE": enable_scrape,
        "ENABLE_MODALITY_TAG_REQUESTED": modality_requested,
        "ENABLE_MODALITY_TAG_EFFECTIVE": modality_effective,
        "ENABLE_MODALITY_TAG_PREFLIGHT_SKIPPED": modality_preflight_skipped,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_REQUESTED": handoff_idem_requested,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_EFFECTIVE": handoff_idem_effective,
        "ENABLE_HANDOFF_IDEMPOTENCY_V2_PREFLIGHT_SKIPPED": handoff_idem_preflight_skipped,
        "MFDS_HTTP_PROXY_CONFIGURED": bool(os.environ.get("MFDS_HTTP_PROXY", "").strip()),
        "LAW_GO_KR_OC_CONFIGURED": bool(law_go_kr_oc),
    }
    health = _evaluate_health(
        modality_preflight_disabled=modality_preflight_disabled,
        handoff_idem_preflight_disabled=handoff_idem_preflight_disabled,
        handoff_idem_effective=handoff_idem_effective,
        stats=stats,
        active=active,
        enable_search=enable_search,
        enable_mfds=enable_mfds,
        enable_mfds_law=enable_mfds_law,
        enable_mfds_recall=enable_mfds_recall,
        enable_mfds_admin=enable_mfds_admin,
        enable_mfds_gmp_cert=enable_mfds_gmp_cert,
        enable_mfds_safety_letter=enable_mfds_safety_letter,
        enable_mfds_gmp_inspection=enable_mfds_gmp_inspection,
        enable_ich=enable_ich,
        enable_who=enable_who,
        enable_hc=enable_hc,
        enable_fda483=enable_fda483,
        enable_moleg_api=enable_moleg_api,
        enable_scrape=enable_scrape,
        event_name=event_name,
        emit_routine_handoff=args.emit_routine_handoff,
        handoff_emitted=handoff_emitted,
        handoff_failed=handoff_failed,
        handoff_error_msg=handoff_error_msg,
        aged_unconsumed_new=aged_unconsumed_new,
        aged_new_query_error=aged_new_query_error,
        handoff_window_days=handoff_window_days,
    )
    health_payload = _health_payload(
        health=health,
        stats=stats,
        run_date=run_date,
        start=start,
        end=end,
        event_name=event_name,
        dry_run=args.dry_run,
        requested_sources=list(requested_sources),
        active=active,
        flags=flags,
        handoff_emitted=handoff_emitted,
        handoff_failed=handoff_failed,
        handoff_row_count=handoff_row_count,
        handoff_url=handoff_url,
        handoff_error_msg=handoff_error_msg,
        handoff_window_days=handoff_window_days,
        aged_unconsumed_new=aged_unconsumed_new,
    )
    _write_health_json(health_json_path, health_payload)

    log("INFO", "── Collection summary ──\n" + stats.summary())

    # GitHub Actions 가 읽을 수 있는 GITHUB_STEP_SUMMARY 출력
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("## GRM Intake Collection Summary\n\n")
                f.write(f"- Run date (KST): `{run_date.isoformat()}`\n")
                f.write(f"- Window: `{start.isoformat()}` ~ `{end.isoformat()}`\n")
                def _src_line(label: str, fetched: int, inserted: int,
                              skipped: int, failed: int, error: bool,
                              err_msg: str, truncated: bool = False) -> str:
                    prefix = "⚠️ " if (error or failed > 0 or truncated) else ""
                    trunc  = " · ⚠️ TRUNCATED" if truncated else ""
                    return (f"- {prefix}{label}: fetched {fetched} · "
                            f"inserted {inserted} · skip-dup {skipped} · "
                            f"failed {failed} · error `{err_msg or 'none'}`{trunc}\n")

                f.write(_src_line("Federal Register", stats.fr_fetched, stats.fr_inserted,
                                  stats.fr_skipped_dup, stats.fr_insert_failed,
                                  stats.fr_error, stats.fr_error_msg, stats.fr_truncated))
                f.write(_src_line("OpenFDA Recall", stats.recall_fetched, stats.recall_inserted,
                                  stats.recall_skipped_dup, stats.recall_insert_failed,
                                  stats.recall_error, stats.recall_error_msg, stats.recall_truncated))
                f.write(_src_line("EMA RSS", stats.ema_fetched, stats.ema_inserted,
                                  stats.ema_skipped_dup, stats.ema_insert_failed,
                                  stats.ema_error, stats.ema_error_msg))
                f.write(_src_line("MHRA RSS", stats.mhra_fetched, stats.mhra_inserted,
                                  stats.mhra_skipped_dup, stats.mhra_insert_failed,
                                  stats.mhra_error, stats.mhra_error_msg))
                f.write(_src_line("PIC/S RSS", stats.pics_fetched, stats.pics_inserted,
                                  stats.pics_skipped_dup, stats.pics_insert_failed,
                                  stats.pics_error, stats.pics_error_msg))
                f.write(_src_line("ECA Academy RSS", stats.eca_fetched, stats.eca_inserted,
                                  stats.eca_skipped_dup, stats.eca_insert_failed,
                                  stats.eca_error, stats.eca_error_msg))
                f.write(_src_line("FDA Warning Letters", stats.wl_fetched, stats.wl_inserted,
                                  stats.wl_skipped_dup, stats.wl_insert_failed,
                                  stats.wl_error, stats.wl_error_msg))
                if enable_mfds:
                    f.write(_src_line("MFDS", stats.mfds_fetched, stats.mfds_inserted,
                                      stats.mfds_skipped_dup, stats.mfds_insert_failed,
                                      stats.mfds_error, stats.mfds_error_msg))
                if enable_mfds_recall:
                    f.write(_src_line("MFDS Recall", stats.mfds_recall_fetched,
                                      stats.mfds_recall_inserted,
                                      stats.mfds_recall_skipped_dup,
                                      stats.mfds_recall_insert_failed,
                                      stats.mfds_recall_error,
                                      stats.mfds_recall_error_msg))
                if enable_mfds_admin:
                    f.write(_src_line("MFDS Admin", stats.mfds_admin_fetched,
                                      stats.mfds_admin_inserted,
                                      stats.mfds_admin_skipped_dup,
                                      stats.mfds_admin_insert_failed,
                                      stats.mfds_admin_error,
                                      stats.mfds_admin_error_msg))
                if enable_mfds_gmp_inspection:
                    f.write(_src_line("MFDS GMP Inspection",
                                      stats.mfds_gmp_inspection_fetched,
                                      stats.mfds_gmp_inspection_inserted,
                                      stats.mfds_gmp_inspection_skipped_dup,
                                      stats.mfds_gmp_inspection_insert_failed,
                                      stats.mfds_gmp_inspection_error,
                                      stats.mfds_gmp_inspection_error_msg))
                if enable_ich:
                    f.write(_src_line("ICH", stats.ich_fetched, stats.ich_inserted,
                                      stats.ich_skipped_dup, stats.ich_insert_failed,
                                      stats.ich_error, stats.ich_error_msg))
                if enable_who:
                    f.write(_src_line("WHO", stats.who_fetched, stats.who_inserted,
                                      stats.who_skipped_dup, stats.who_insert_failed,
                                      stats.who_error, stats.who_error_msg))
                if enable_hc:
                    f.write(_src_line("Health Canada", stats.hc_fetched, stats.hc_inserted,
                                      stats.hc_skipped_dup, stats.hc_insert_failed,
                                      stats.hc_error, stats.hc_error_msg))
                if enable_fda483:
                    f.write(_src_line("FDA 483", stats.fda483_fetched, stats.fda483_inserted,
                                      stats.fda483_skipped_dup, stats.fda483_insert_failed,
                                      stats.fda483_error, stats.fda483_error_msg))
                if enable_search:
                    f.write(_src_line("Brave Search", stats.search_fetched, stats.search_inserted,
                                      stats.search_skipped_dup, stats.search_insert_failed,
                                      stats.search_error, stats.search_error_msg))
                if args.emit_routine_handoff:
                    if handoff_emitted:
                        f.write(f"- Routine handoff: `{handoff_row_count}` New rows · "
                                f"{handoff_url or 'url unavailable'}\n")
                    elif handoff_failed:
                        f.write(f"- ⚠️ Routine handoff: failed/skipped · "
                                f"`{handoff_error_msg[:160]}`\n")
                    else:
                        f.write("- Routine handoff: not emitted (dry-run)\n")
                f.write(f"- Dry run: `{args.dry_run}`\n")
                if stats.has_insert_failures():
                    total_fail = stats.total_insert_failures()
                    f.write(f"\n> ⚠️ **Notion 삽입 실패 {total_fail}건** — "
                            f"해당 항목은 이번 주 다이제스트에서 누락될 수 있습니다. "
                            f"Actions 로그에서 doc ID 확인 후 필요 시 수동 재실행.\n")
                if stats.has_source_errors():
                    f.write("\n> ❌ **수집기 오류 발생** — "
                            "Actions 로그에서 source별 error 메시지를 확인하세요.\n")
                    if stats.search_error:
                        f.write(f"> Brave Search error: `{stats.search_error_msg[:120] or 'none'}` "
                                f"— BRAVE_API_KEY 및 ENABLE_SEARCH 설정 확인.\n")
                    if stats.mfds_error:
                        f.write(f"> MFDS error: `{stats.mfds_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS 및 DATA_GO_KR_KEY 설정 확인.\n")
                    if stats.mfds_recall_error:
                        f.write(f"> MFDS Recall error: `{stats.mfds_recall_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS_RECALL 및 DATA_GO_KR_SERVICE_KEY 설정 확인.\n")
                    if stats.mfds_admin_error:
                        f.write(f"> MFDS Admin error: `{stats.mfds_admin_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS_ADMIN 및 DATA_GO_KR_SERVICE_KEY 설정 확인.\n")
                    if stats.mfds_gmp_inspection_error:
                        f.write(f"> MFDS GMP Inspection error: "
                                f"`{stats.mfds_gmp_inspection_error_msg[:120] or 'none'}` "
                                f"— ENABLE_MFDS_GMP_INSPECTION 및 nedrug HTML 구조 확인.\n")
                _write_health_summary(f, health, health_json_path)
        except OSError as e:
            log("WARN", f"STEP_SUMMARY 쓰기 실패: {e}")

    # 종료 코드는 _evaluate_health() 하나만 기준으로 삼는다.
    # - failure → exit 1 (workflow failure Issue)
    # - warning → exit 0 (warning Issue/summary)
    # - ok      → exit 0
    for finding in health.failures:
        log("ERROR", f"health failure [{finding.code}] {finding.source}: {finding.message}")
    for finding in health.warnings:
        log("WARN", f"health warning [{finding.code}] {finding.source}: {finding.message}")
    return health.exit_code


if __name__ == "__main__":
    sys.exit(main())
